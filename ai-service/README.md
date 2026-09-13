# ai-service

발표 리허설 코치 — AI 분석 파트 (Python / FastAPI).
STT(음성 인식), 발화 전달력 분석(WPM·침묵·필러), 대본 정합성 판정을 담당하는 SQS 워커입니다.

## 이 서비스가 하는 일

프론트엔드에서 업로드한 발표 PPT + 녹음 오디오를, 백엔드(Spring)가 S3에 올리고 SQS에 잡(job)을 넣으면 이 워커가 그 잡을 집어서 분석하고 결과를 백엔드로 콜백합니다. 자체 HTTP API를 갖는 서버가 아니라 SQS 큐를 계속 폴링하는 상주 프로세스입니다.

```
프론트 업로드 → 백엔드(Spring) → S3 업로드 + SQS 잡 발행
                                        │
                                        ▼
                          ai-service(SQS 워커, 이 레포)
                          1. S3에서 pptx/오디오 다운로드
                          2. CLOVA Speech로 STT
                          3. (동시 실행) VAD 기반 전달력 분석 ∥ LLM 정합성 판정
                          4. 결과를 백엔드로 콜백 (POST /api/internal/analysis-results)
```

## 폴더 구조

| 경로 | 역할 |
|---|---|
| `app/main.py` | FastAPI 진입점. `/health` 헬스체크 + `/debug/analyze`(S3/SQS 없이 로컬 파일로 파이프라인 직접 검증) |
| `app/pipeline/run_analysis.py` | 전체 파이프라인 오케스트레이션 (STT → [VAD 분석 ∥ LLM 판정] 동시 실행 → 리포트 조립) |
| `app/parsing/pptx_parser.py` | PPTX에서 슬라이드별 텍스트 추출 |
| `app/stt/clova_client.py` | CLOVA Speech STT 호출, word-level 타임스탬프 파싱 |
| `app/audio/delivery_metrics.py` | WPM, 침묵/긴침묵, 그룹A 필러("음"/"어", Silero VAD로 탐지), 성량 변화 계산 |
| `app/llm/gateway_client.py` | LLM 호출 — 대본 정합성 판정, 그룹B 필러("그"/"저"/"뭐" 등 문맥 판단 필요), script_diff |
| `app/clients/s3_client.py` | S3에서 pptx/오디오 다운로드 |
| `app/clients/sqs_consumer.py` | SQS 폴링 루프(`poll_forever`) — 이 서비스의 실제 진입점(실배포 시 systemd로 상주 실행) |
| `app/schemas/report.py` | 최종 분석 리포트 스키마 (`AnalysisReport`) — API_명세서 §2.3.1과 1:1 대응 |
| `app/schemas/job.py` | Spring ↔ Python 계약 스키마 (SQS 잡 메시지, 결과 콜백) |
| `scripts/` | 실제 CLOVA/LLM/S3 등을 호출하는 수동 검증용 스크립트 (아래 표 참고) |
| `tests/` | pytest 유닛 테스트 (외부 호출은 monkeypatch로 대체) |

## 구현 상세

각 모듈이 실제로 어떻게 동작하는지 정리합니다. 배경/근거는 [`AI_설계.md`](./AI_설계.md)에 더 자세히 있습니다.

### 1. PPTX 파싱 (`app/parsing/pptx_parser.py`)

`python-pptx`로 슬라이드마다 텍스트를 뽑습니다. "핵심 주장" 단위 추출은 여기서 하지 않고 슬라이드 원문 텍스트를 통째로 LLM에 넘겨서 LLM 프롬프트가 주장을 뽑아내도록 위임합니다. 도형은 z-order가 아니라 화면 위치(top, left) 기준으로 정렬해서 대략적인 읽는 순서를 맞추고, 그룹 도형은 재귀적으로 훑고, 표는 행 단위로 `" | "`로 이어 붙입니다. 발표자 노트는 청중이 보는 내용이 아니므로 제외합니다. 파일이 없거나 pptx 형식이 아니거나 슬라이드가 0개면 `PptxParseError`.

### 2. STT (`app/stt/clova_client.py`)

CLOVA Speech `/recognizer/upload`(장문 인식, 파일 직접 업로드)를 동기 호출합니다. `wordAlignment: True`로 단어별 시작/종료 ms를 받고, 이 word-level 타임스탬프가 뒤의 전달력 분석(gap 계산)과 그룹B 필러 문맥 판단의 기반이 됩니다. 응답에서 `result`가 `COMPLETED`/`SUCCEEDED`가 아니면(사용량 초과 등으로 HTTP 200이면서 내부적으로만 실패하는 경우가 있음을 실측으로 확인) 바로 `SttError`로 처리합니다 — 예전엔 이 필드를 안 봐서 실패를 "무음이라 세그먼트가 없는 정상 응답"으로 오인한 적이 있었습니다.

### 3. 전달력 분석 (`app/audio/delivery_metrics.py`)

WPM, 침묵/긴 침묵, 그룹A 필러("음"/"어"), 성량 변화를 계산합니다. 핵심 아이디어는 CLOVA가 "음"/"어" 같은 순수 발성은 전사 텍스트에서 지워버리지만 인접 단어 사이의 시간 간격(gap)은 정확히 남는다는 점입니다.

- 단어들을 시작 시각 기준으로 정렬해 단어 사이 gap을 전부 나열합니다(`compute_word_gaps`). 오디오 맨 앞(첫 단어 시작 전)과 맨 뒤(마지막 단어 끝난 후) gap은 `is_edge=True`로 표시합니다 — 마이크 세팅/녹음 종료 지연 같은 녹음 아티팩트일 뿐 발표 중 침묵이 아니라서, 뒤 단계(`classify_gaps`)에서 이 두 gap은 전부 제외합니다.
- 나머지 gap 중 250ms(`GAP_CANDIDATE_THRESHOLD_MS`) 미만은 자연스러운 조음 간격으로 보고 침묵으로만 집계, VAD를 돌리지 않습니다.
- 250ms 이상인 gap만 원본 오디오에서 앞뒤로 100ms 패딩을 붙여 잘라내 Silero VAD(`get_speech_timestamps`)로 "목소리 있음"인지 확인합니다. 60ms(`VAD_MIN_SPEECH_MS`) 이상 음성이 감지되면 그룹A 필러 후보로, 아니면 순수 침묵으로 집계합니다. 정확히 "음"인지 "어"인지는 CLOVA 원문이 없어 구분이 안 되므로 `FillerType.ETC`로 잡습니다.
- 침묵으로 집계된 gap 중 3000ms(`LONG_PAUSE_THRESHOLD_MS`) 이상은 `long_pauses`에 별도 기록합니다.
- WPM은 오디오 전체 길이가 아니라 "첫 단어 시작 ~ 마지막 단어 끝"(`compute_speaking_window_ms`) 구간을 분모로 씁니다 — 녹음 전후 무음 때문에 분당 단어 수가 실제보다 낮게 나오는 걸 방지합니다.
- 성량 변화는 STT/VAD 결과와 무관하게 오디오 자체의 RMS(진폭)를 200ms 프레임 단위로 구해 변동계수(상대표준편차)로 냅니다.

### 4. LLM 정합성 판정 (`app/llm/gateway_client.py`)

건국대 API Gateway(Mindlogic factchat-cloud, OpenAI 호환)에 LLM 호출 **한 번**으로 아래 다섯 가지를 동시에 처리합니다(비용 절감을 위해 의도적으로 한 호출에 묶음).

- `consistency` — 슬라이드별 핵심 주장이 발화에서 뒷받침됐는지(`SUPPORTED`/`NOT_MENTIONED`/`NO_BASIS`)
- `off_topic` — 슬라이드 주제와 무관한 발화 구간
- `logic_gaps` — 근거 없이 결론으로 건너뛰는 구간
- `script_diff` — 대본이 주어졌을 때만, 생략/추가/변경 구간
- 그룹B 필러 판단 — "그"/"저"/"뭐"/"뭔가"/"좀"/"막"/"그냥"/"같다"(어간 접두 매칭) 후보를 문자열 매칭으로 먼저 뽑고(`find_group_b_candidates`), 그 단어가 실제 문법적 역할(예: "저는"의 1인칭 대명사)인지 진짜 필러인지는 LLM이 앞뒤 4단어(`CONTEXT_WINDOW`) 문맥을 보고 판단합니다. 판단 대상 단어는 `《 》`로 감싸 명확히 표시합니다.

환각 방지를 위해 LLM에게 원시 ms 타임스탬프를 직접 만들게 하지 않습니다. 대신 `transcript.segments`를 번호 붙여 보여주고, LLM은 "몇 번 세그먼트가 근거/위치인지" 인덱스만 답하면 코드가 실제 `start_ms`/`end_ms`로 치환합니다(`_resolve_segment_ms`). 응답은 `response_format: json_schema`(strict 모드)로 구조를 강제하고, `script_text == spoken_text`인데도 "변경"으로 오판정하는 할루시네이션이 실측에서 발견돼 코드에서도 방어적으로 걸러냅니다.

### 5. 파이프라인 오케스트레이션 (`app/pipeline/run_analysis.py`)

S3/SQS/FastAPI를 전혀 몰라도 되는 순수 함수(`run_analysis`)로 설계했습니다. 실제 의존 관계상 병렬화할 수 있는 지점이 두 곳 있어 `ThreadPoolExecutor`로 각각 동시 실행합니다.

1. **1단계**: `parse_pptx`(PPTX 파싱)와 `transcribe`(STT)는 서로의 결과가 필요 없어 동시 실행.
2. **2단계**: `compute_delivery_metrics`(그룹A 필러 포함, Silero VAD)와 `analyze_consistency`(그룹B 필러 포함, LLM 호출)는 둘 다 STT 결과만 있으면 되고 서로는 필요 없어 동시 실행. LLM 호출은 네트워크 I/O 대기 중 GIL을 해제하고 Silero VAD는 텐서 연산 중 GIL을 해제하므로, 스레드 두 개로 겹쳐 돌리는 것만으로 실질적인 이득이 있습니다(asyncio/멀티프로세스까지는 필요 없다고 판단).

두 단계가 끝나면 그룹A 필러(`delivery.fillers`)와 그룹B 필러(`llm_result.group_b_fillers`)를 합쳐 최종 `fillers` 목록을 만들고 `AnalysisReport`를 조립합니다. 어느 한 단계라도 실패하면(`PptxParseError`/`SttError`/`LlmError`) 예외를 그대로 위로 올려 전체를 실패로 취급합니다(all-or-nothing).

### 6. 인프라 레이어 — SQS 워커 (`app/clients/sqs_consumer.py`, `s3_client.py`)

`run_analysis`는 S3/SQS를 몰라도 되게 설계했으므로, 이 레이어는 "메시지 받기 → S3 다운로드 → `run_analysis` 호출 → 콜백 POST → 메시지 삭제"만 얇게 감쌉니다.

- `poll_forever()`가 SQS를 20초 롱폴링하며 한 번에 메시지 1개씩 처리하는 단일 워커 루프입니다(해커톤 스코프에 맞춘 최소 구현 — 동시 처리가 필요해지면 이 루프를 여러 프로세스로 띄우면 됩니다). `VisibilityTimeout`은 15분(900초)으로 넉넉히 잡아 STT+VAD+LLM 처리 시간을 감안합니다.
- 메시지 파싱에 실패하면(`SqsJobMessage` 검증 실패) 콜백 보낼 대상(`presentation_id`)조차 모르므로 메시지를 지우지 않고 그대로 둬서 visibility timeout 후 재배달(→ DLQ)에 맡깁니다.
- S3에서 pptx/오디오를 임시 디렉토리(`tempfile.TemporaryDirectory`)로 내려받고(`s3_client.py`, 읽기 전용 — `s3:GetObject`만 필요), 오디오 길이는 `librosa.get_duration`으로 계산합니다.
- `run_analysis` 실패 시 예외 타입에 따라 `PPTX_PARSE_FAILED`/`AUDIO_NOT_FOUND`/`STT_FAILED`/`LLM_FAILED`/`TIMEOUT` 중 하나로 매핑해 실패 콜백을 보냅니다(부분 실패도 전체 실패로 취급, all-or-nothing).
- 콜백은 `X-Worker-Secret` 헤더로 인증하고, 실패 시 `[2, 5, 15]`초 백오프로 재시도합니다. **콜백 전송 자체가 끝까지 실패한 경우에만** SQS 메시지를 지우지 않고 재배달에 맡깁니다 — STT/LLM은 이미 과금된 호출이라, 콜백만 실패했다고 처음부터 다시 돌리면 이중 과금이 나기 때문입니다.

## 환경 설정 (Windows)

1. 파이썬 3.10~3.13 중 하나 필요(권장 3.12) — `winget install Python.Python.3.12`
2. 전용 가상환경 생성 + 의존성 설치:
   ```powershell
   .\setup_env.ps1
   ```
   torch/librosa/silero-vad 포함이라 시간이 좀 걸립니다. 이미 `.venv`가 있으면 재사용하고 requirements만 다시 설치합니다.
3. `ffmpeg` 설치 확인 (librosa가 m4a/webm 오디오 디코딩에 사용) — `which ffmpeg`(또는 `where ffmpeg`)로 확인, 없으면 설치.
4. `.env` 생성 — `.env.example`을 복사해서 CLOVA/LLM 게이트웨이/AWS/콜백 값을 채웁니다.
   - AWS 자격증명은 `.env`에 넣지 않습니다. 로컬 개발 중엔 `aws configure`로 `coach-ai` IAM 유저 키를 별도 설정하고, EC2 배포 시엔 인스턴스 IAM role(`DefaultCredentialsProvider`)을 그대로 씁니다.

## 실행 방법

파이썬 스크립트/모듈은 항상 `.\run.ps1`로 실행합니다(가상환경 활성화 없이 `.venv`의 파이썬을 바로 사용하는 래퍼).

**로컬 디버그 서버** — S3/SQS 없이 로컬 파일 경로로 파이프라인만 검증:
```powershell
.\run.ps1 -m uvicorn app.main:app --reload
```
`POST /debug/analyze`에 `{"pptx_path": "...", "audio_path": "...", "audio_duration_ms": ..., "script": "..."}`를 보내면 결과 리포트가 그대로 반환됩니다.

**실서비스 워커** (SQS 폴링):
```powershell
.\run.ps1 -m app.clients.sqs_consumer
```
배포 환경(EC2 systemd) 세팅은 [`DEPLOY.md`](./DEPLOY.md) 참고.

## 테스트

```powershell
.\run.ps1 -m pytest tests\ -q
```

개별 모듈만:
```powershell
.\run.ps1 -m pytest tests\test_delivery_metrics.py -v
```

## 실측 검증용 스크립트 (`scripts/`)

단위 테스트와 별개로, 실제 CLOVA/LLM/S3/백엔드를 호출해서 눈으로 결과를 확인하는 스크립트입니다.

| 스크립트 | 용도 |
|---|---|
| `test_run_analysis.py <pptx> <오디오> [대본]` | 파이프라인 전체를 로컬 파일 기준으로 실행 |
| `test_clova_upload.py` | CLOVA Speech STT 단독 호출 검증 |
| `test_delivery_metrics.py` | WPM/침묵/필러/성량 분석 단독 검증 |
| `test_llm_gateway.py` | LLM 정합성 판정/그룹B 필러/script_diff 단독 검증 |
| `test_model_comparison.py` | LLM 모델별(gemini-3.5-flash-lite 등) 정확도 비교 |
| `test_s3_download.py <presentation_id>` | S3 다운로드 단독 검증 |
| `test_worker_secret.py` | `WORKER_SECRET`이 실배포 백엔드 설정과 일치하는지 검증 |
| `test_e2e_via_backend.py [BASE_URL] [pptx] [오디오]` | 프론트 대신 백엔드 API를 직접 호출해 SQS에 실제 잡을 넣는 종단 테스트 |

## 분석 리포트 출력

`app/schemas/report.py`의 `AnalysisReport`가 최종 산출물이며, 크게 네 부분으로 구성됩니다.

- `delivery` — WPM, 전체 침묵 시간, 긴 침묵(`long_pauses`) 목록, 필러(`fillers`) 목록, 성량 변화
- `consistency` — 슬라이드별 주장이 발화 내용으로 뒷받침되는지(`SUPPORTED`/`NOT_MENTIONED`/`NO_BASIS`)
- `off_topic` / `logic_gaps` — 주제 이탈 구간, 논리 비약 구간
- `script_diff` — 대본이 있을 때만 채워짐(생략/추가/변경 구간)

필드/enum 정의를 바꿀 땐 `docs/API_명세서.md` §2.3.1을 먼저 고치고 이 스키마를 맞추는 순서를 지킵니다(스키마 상단 주석 참고).

## 관련 문서

- [`AI_설계.md`](./AI_설계.md) — 설계 배경, 필러 탐지 전략, 각 임계값을 이렇게 잡은 이유
- [`DEPLOY.md`](./DEPLOY.md) — EC2 systemd 배포 절차
