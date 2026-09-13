# AI 서비스 설계 (ai-service)

> PTPT · AI 파트(STT·음성분석·LLM 정합검사) 설계 기록.
> Spring↔Python 계약(JSON 필드/enum)은 [`docs/API_명세서.md`](../docs/API_명세서.md) 참고. 여기는 그 계약을 채우는 내부 구현 설계.
> 코드 뼈대는 이미 있음: `app/schemas/`(계약 pydantic화), `app/{parsing,stt,audio,llm}/`(스텁), `app/pipeline/run_analysis.py`(오케스트레이션), `app/clients/`(S3/SQS, 스텁), `scripts/test_clova_upload.py`(STT 실측 테스트 스크립트).

---

## 1. STT

**결정: 네이버 클로바 스피치(CLOVA Speech) API, "장문 인식" 도메인 사용.**

- NCP 콘솔에 STT 상품이 두 종류였음 — CSR(단문, 레거시, CLOVA Speech로 통합 안내됨)과 CLOVA Speech(신규). CLOVA Speech 안에서도 도메인 타입이 **장문 인식**(음성 메모·자막 생성·통화 녹취록 관리용, 완성된 파일 배치 처리) / 단문 인식(짧은 명령어·챗봇용) / 스트리밍 인식(실시간 방송·회의용) 세 가지로 나뉘는데, 우리는 "브라우저 녹음 완료 → S3 업로드 → 배치로 통째 분석" 흐름이라 **장문 인식**이 맞음.
- 도메인 생성 설정: 서비스 플랜 Free(월 20분 무료, 개발 중 테스트엔 충분), **화자 인식 미사용**(발표자 1인 녹음이라 화자 구분 불필요, 비용 절감), 이벤트 탐지 미사용.
- **Object Storage 불필요.** 장문 인식 도메인 생성 마법사가 "인식 대상/결과 저장 경로"를 필수로 요구하지만, 이건 `/recognizer/object-storage`(NCP Object Storage 트리거) 방식에서만 쓰임. 우리는 **`/recognizer/upload`**(로컬 파일 직접 멀티파트 업로드, `completion: sync`로 바로 JSON 응답) 방식을 쓰므로 도메인 생성 시엔 형식상 아무 버킷이나 지정하고 실제 파이프라인에선 아예 안 씀. AWS S3에서 내려받은 오디오를 그대로 `/recognizer/upload`에 보내면 됨 — NCP Object Storage를 프로젝트 저장소로 추가할 필요 없음.
- 인증: 도메인 생성 후 콘솔에서 **Invoke URL**과 **Secret Key** 발급. `.env`에 `CLOVA_INVOKE_URL`(전체 URL, `/recognizer/upload`는 코드가 붙임), `CLOVA_SECRET_KEY`로 관리. 요청 헤더 `X-CLOVASPEECH-API-KEY`에 Secret Key.
- 비용 관리: 월 20분 무료 초과분은 종량제(분당 약 30~45원). 개발 중 반복 테스트 + 대회 당일 실사용 감안해 예산/사용량 알림 필요.

### 1.1 실측 테스트 결과 (2026-09-07 확정)

`scripts/test_clova_upload.py`로 실제 녹음 파일 호출해서 확인함.

- **word-level 타임스탬프: 지원됨.** `wordAlignment: true`로 요청하면 `segments[].words`에 `[시작ms, 종료ms, "단어"]` 형태로 나옴. `evidence_at_ms`, `fillers[].at_ms` 설계 그대로 유지 가능.
- **채움말("음"/"어") 텍스트 보존: 안 됨.** `noiseFiltering: false`로 명시해도 전사 텍스트에서 사라짐 — STT 내부 후처리(정규화) 단계에서 걸러지는 것으로 판단, 요청 파라미터로 되돌릴 방법을 못 찾음. 대신 **인접 단어 타임스탬프 사이에 정확한 빈틈(500ms~1.9초)이 남는 걸 실측으로 확인** — 이 특성을 이용해 §2에서 VAD로 우회.
- **"그"/"저"/"뭐" 같은 실단어는 정상 보존됨.** 다만 "그 사람이"(지시대명사)처럼 필러 아닌 정상 용법과 텍스트만으로는 구분 안 됨 — 문맥 판단이 필요해서 §2에서 LLM으로 처리.
- **`diarization` 파라미터는 요청값과 무관하게 도메인 설정을 따라감.** `enable: true`로 보내도 응답의 `params.diarization.enable`은 도메인이 미사용이면 `false`로 강제됨. 초기에 `enable: false`를 명시했는데도 `"speaker detect is off"` 400 에러가 났던 건, 도메인을 막 생성한 직후라 완전히 프로비저닝되기 전에 호출해서 생긴 일시적 문제로 추정(시간 지나고 재시도하니 해결됨) — 도메인 생성 직후 바로 호출이 실패하면 잠깐 기다렸다 재시도해볼 것.

### 1.2 `transcribe()` 정식 구현 완료 (2026-09-07)

`scripts/test_clova_upload.py`로 검증한 호출 방식을 `app/stt/clova_client.py`의 `transcribe()`로 옮김. `diarization`은 도메인 설정과 일치하게 `False`로 명시(§1.1에서 확인했듯 실제 동작에는 영향 없지만 의도를 코드에 명확히 남김). 응답 파싱(`_parse_response()`)이 `segments[]`를 `TranscriptResult.segments`(`TranscriptSegment` 리스트)로, `segments[].words`를 평탄화해서 `TranscriptResult.words`(`WordTiming` 리스트)로 변환 — 이 두 필드가 각각 §2 그룹A/그룹B와 §3 LLM 프롬프트의 입력이 됨. 설정 누락/파일 없음/네트워크 오류/4xx·5xx/응답 파싱 실패를 전부 `SttError`로 통일. 단위 테스트(`tests/test_clova_client.py`)는 `_parse_response()`(fixture 기반)와 `transcribe()`의 각 에러 분기·성공 경로(httpx.post는 monkeypatch로 대체)를 커버 — 실제 네트워크 호출은 안 함(그건 여전히 `scripts/test_clova_upload.py`가 실측용으로 담당).

## 2. 채움말(filler) 탐지 전략 — 2026-09-07 확정

`docs/API_명세서.md` §1의 `filler.type` enum을 `음`·`어`·`그`·`저`·`뭐`·`뭔가`·`좀`·`막`·`그냥`·`같다`·`기타`로 확장함(`app/schemas/report.py`의 `FillerType`도 동일 반영). 탐지 방식이 성격이 다른 두 그룹으로 갈림.

### 그룹 A — `음`/`어` (VAD 기반)

STT가 텍스트에서 완전히 지워버리는 순수 발성이라(§1.1), 텍스트 기반으로는 탐지 불가. **CLOVA 타임스탬프 빈틈 + Silero VAD 교차 검증**으로 우회:

1. CLOVA `words` 배열에서 인접 단어 간 간격(`다음 단어 시작 - 현재 단어 끝`)을 계산 — 실측 데이터 기준 자연스러운 단어 간 간격은 0~150ms, 필러 의심 구간은 500ms 이상이었음. **임계값 200~300ms**로 "후보 gap" 추출(정확한 값은 라벨링 데이터로 튜닝 필요). 세그먼트 시작 전/세그먼트 경계 사이도 동일하게 체크.
2. 각 후보 gap만 원본 오디오에서 슬라이스(앞뒤 ~100ms 패딩)해서 Silero VAD(`pip install silero-vad`, torch 기반, 16kHz 모노 필요 → ffmpeg/soundfile로 리샘플링)에 넣어 "목소리 있음/없음" 확인. 목소리 있으면 필러 확정, 없으면(순수 침묵) 제외.
3. **미정**: `text` 필드 처리 방식. CLOVA가 원문을 안 주니 정확한 단어("음"인지 "어"인지)를 알 수 없음 — 일단 `type: 기타` + 추정 표시(placeholder) 처리로 갈 예정, 세부 포맷 확정 필요.
4. **검증 필요**: 실제 사람이 오디오 들으면서 필러 위치를 직접 표시한 라벨링 데이터로 gap 임계값·VAD 파라미터(`min_speech_duration_ms`, `threshold`) 튜닝하고 정밀도 확인.

전체 파일에 VAD를 돌리는 대신 CLOVA가 못 채운 gap 구간에만 타겟팅해서 VAD를 돌리는 구조라 계산량도 적고 로직도 단순함.

**구현 완료 (2026-09-07, `app/audio/delivery_metrics.py`):** 위 1~3단계 그대로 구현.

- `compute_word_gaps()`: words 배열(+오디오 시작/끝)에서 gap 나열.
- `classify_gaps()`: gap이 `GAP_CANDIDATE_THRESHOLD_MS`(250ms, 200~300 중간값) 미만이면 VAD 없이 침묵으로만 집계. 이상이면 `_is_voiced()`로 VAD 판정 — 목소리 있으면 필러 후보, 없으면 침묵(+`LONG_PAUSE_THRESHOLD_MS`=1000ms 이상이면 `long_pauses[]`에도 기록).
- `_is_voiced()`: gap 앞뒤로 `VAD_SLICE_PADDING_MS`(100ms) 패딩을 붙여 오디오를 슬라이스하고 Silero VAD(`get_speech_timestamps`)로 음성 구간 합이 `VAD_MIN_SPEECH_MS`(60ms) 이상인지 확인.
- **3번 항목(text 필드 미정) 결론**: `type: 기타(ETC)` + `text: "[VAD 감지 · 원문 미상]"` 고정 placeholder로 확정. 나중에 그룹A 안에서 "음"/"어"를 음향적으로 구분하고 싶어지면(피치·길이 등으로) 이 자리를 교체하면 됨.
  - **왜 "음"/"어" 중 어느 쪽인지 정확히 못 알아내는가(2026-09-07 정리)**: Silero VAD는 "이 구간에 사람 목소리가 있는가/없는가"만 판단하는 이진 분류기(voice activity detector)라서, 애초에 단어나 음소 개념 자체가 없음 — 뭐라고 말했는지는 원리적으로 알 방법이 없음. CLOVA에 그 구간만 다시 잘라서 보내는 것도 고려했지만, CLOVA가 애초에 "음"/"어"를 후처리 단계에서 지우는 게 원인(§1.1)이라 구간을 떼어내서 다시 보내도 마찬가지로 걸러질 가능성이 높고, 재호출 비용도 듦. 정확히 구분하려면 "음"/"어" 전용으로 학습된 별도 분류기(피치·포먼트 등 음향 특징 기반)가 있어야 하는데, 학습 데이터도 없고 지금 범위를 벗어남 — 그래서 `기타` + placeholder로 정리하고 넘어가기로 함. 코칭 관점에서도 정확히 어떤 단어인지보다 "여기 채움말 있음"이 핵심이라 실용적으로 충분하다고 판단.
- Silero VAD 모델은 `pip install silero-vad`로 가중치까지 같이 배포되어 별도 GitHub 클론이나 `torch.hub` 다운로드가 필요 없음 — `requirements.txt`에 `silero-vad`+`torch` 추가, 모듈 내부 `_get_vad_model()`이 프로세스당 1회 로드.
- 오디오 로딩(`_load_audio_16k_mono()`)은 librosa로 16kHz 모노 변환 — m4a/webm 디코딩에 시스템 ffmpeg 필요.
- `compute_wpm()`/`compute_volume_variation()`(RMS 변동계수 기반, VAD와 무관)도 같은 파일에 구현. `run_analysis.py`도 `compute_delivery_metrics(stt_result, input.audio_path, input.audio_duration_ms)` 시그니처(오디오 경로 추가)로 갱신.
- 단위 테스트(`tests/test_delivery_metrics.py`)는 gap 나열/분류 로직과 WPM/성량 계산을 커버. `_is_voiced()`(실제 VAD 호출부)는 monkeypatch로 대체해서 검증 — torch/librosa 설치 없이도 로직 테스트가 돌아가게 함. 실제 VAD 정확도 자체는 여전히 라벨링 데이터 검증 필요(§5).

### 그룹 B — `그`/`저`/`뭐`/`뭔가`/`좀`/`막`/`그냥`/`같다` (LLM 문맥 판단)

실제 사전 단어라 STT 텍스트에 항상 남음(VAD 불필요). 다만 지시대명사/1인칭/의문사 등 **정상 용법과 필러성 사용이 같은 단어**라(예: "저는 발표를"의 "저" vs "저... 그게"의 "저") 단순 문자열 매칭은 오탐이 많음. **LLM이 정합성 판정과 같은 호출에서 문맥을 보고 필러 여부 판단** (`app/llm/gateway_client.py`).

**구현 완료 (2026-09-07, `app/llm/gateway_client.py`):**

- **후보 탐지는 코드가, 필러 여부 판단은 LLM이.** `find_group_b_candidates()`가 문자열 매칭으로 후보만 추림 — `그`/`저`/`뭐`/`뭔가`/`좀`/`막`/`그냥`은 정확히 일치, `같다`는 어미 활용(같아요/같은/같습니다)이 흔해서 `같` 접두 매칭으로 넓게 잡음. 각 후보는 `_context_snippet()`으로 앞뒤 4단어 문맥을 만들고 판단 대상 토큰을 `《 》`로 표시해서 LLM에 보여줌(같은 단어가 문맥 안에 여러 번 나올 수 있어서 어느 토큰인지 명확히 표시 필요).
- **정합성 판정과 그룹B 판단을 한 번의 LLM 호출로 묶기로 확정**(비용 절감). `off_topic`/`logic_gaps`/`script_diff`도 같은 호출.
- **원시 ms 타임스탬프는 LLM에게 만들게 하지 않음(환각 위험).** 대신 `transcript.segments`(CLOVA 응답의 문장급 청크, `TranscriptResult`에 새로 추가됨 — 아래 참고)에 번호를 매겨 보여주고, LLM은 "몇 번 세그먼트가 근거/위치인지" 인덱스만 답하게 한 뒤 `_resolve_segment_ms()`가 실제 start_ms/end_ms로 치환. 범위 밖 인덱스는 해당 항목을 버림(잘못된 타임스탬프로 report에 들어가는 것 방지).
- **`TranscriptResult`에 `segments: list[TranscriptSegment]` 필드 추가** (`app/stt/clova_client.py`) — CLOVA 응답의 `segments[]`를 그대로 옮기는 용도. §3.2 콜백의 `transcript` 필드 타입과 동일해서 `run_analysis.py`에서 변환 없이 그대로 씀(§4 파이프라인 5번 항목 단순화).
- **strict 모드 JSON 스키마 정규화**(`_strict_json_schema()`): pydantic `model_json_schema()`가 기본으로는 OpenAI strict 모드 요구사항(모든 object에 `additionalProperties:false`, 모든 속성이 `required`)을 만족 안 해서 후처리 함수로 재귀 정규화.
- **`fillers` 최종 목록은 그룹A(delivery.fillers) + 그룹B(group_b_fillers) 병합** — `analyze_consistency()`는 그룹B만 반환, 합치는 건 `run_analysis.py` 조립 단계(§4)에서.
- 프롬프트(`_SYSTEM_PROMPT`)에 정상 용법 예시("저는 발표를"의 "저"=대명사, "3배 좀 넘게"의 "좀"=수량부사 등)를 넣어서 애매하면 필러 아님 쪽으로 판정하게 유도(과탐 방지 우선).
- 단위 테스트(`tests/test_gateway_client.py`)는 후보 탐지·문맥 스니펫·스키마 정규화·응답→report 매핑(세그먼트 인덱스 치환, 대본 없을 때 script_diff 강제 null 등)을 커버. **실제 게이트웨이 호출(`analyze_consistency` 전체)은 API 키/네트워크가 필요해 여기 포함 안 됨 — 모델 확정 전 수동 검증 필요.**

## 3. LLM

**결정: 건국대 API Gateway(Mindlogic, factchat-cloud.mindlogic.ai) 사용. 구체적 모델은 개발 중 실데이터로 검증하며 확정.**

- OpenAI Chat Completions 포맷과 호환(`/v1/gateway/chat/completions/`), 기존 OpenAI SDK에 base_url만 바꿔서 사용 가능. `response_format`(json_schema, strict) 지원 확인 완료 — 리포트 스키마(2.3.1절)를 그대로 강제 출력 가능. 모델을 나중에 바꿔도 이 방식은 그대로 유지됨(같은 게이트웨이, 같은 스키마).
- **모델 선정 기준: 비용(크레딧 소모) 최우선, 단 정합성 판정처럼 reasoning이 필요한 작업의 품질도 같이 봐야 해서 실제 개발 중 확정.** 일단 제일 저렴한 모델로 시작 → 실데이터로 판정 정확도 테스트 → 부족하면 한 단계 위 모델로 올리는 방식. §2 그룹B 채움말 판단도 같은 호출에 곁들일지, 별도 호출로 뺄지는 프롬프트/비용 보면서 결정.
- 동일 테스트 요청 기준 크레딧 소모 비교(참고용, 실제 태스크 기준 재검증 필요):

| 모델 | 크레딧 소모 |
|---|---|
| gemini-3.5-flash-lite | 0.11 (최저) |
| gemini-3.7-flash | 0.13 |
| gemini-3.8-flash | 0.14 |
| gemini-3.6-flash | 0.17 |
| gemini-3.5-flash | 0.30 |

- 32개 모델 중 Claude(haiku/opus), OpenAI(gpt-5.6 계열) 등도 후보에 포함 — flash-lite 품질이 부족하면 이쪽으로 전환 검토.
- 월 3000크레딧 할당(매월 1일 리셋). flash-lite 기준 토큰당 약 0.0013크레딧 → 발표 1건(추정 3000~4500토큰)당 대략 4~6크레딧, 월 500건 이상 처리 가능 추정(단, 더 비싼 모델로 갈 경우 이 추정치는 낮아짐).
- **미확인**: EC2(운영 서버)에서 접근 가능한지, 해커톤 같은 외부 대회 프로젝트에 사용해도 되는 약관인지 — 배포 전 확인 필요.
- 기존에 검토했던 Gemini 공개 무료 티어/OpenAI 유료/Claude/자체호스팅 오픈소스 방안은 이 게이트웨이로 대체.

### 3.1 실제 게이트웨이 호출 검증 완료 (2026-09-07)

`scripts/test_llm_gateway.py`(모델 `gemini-3.5-flash-lite`)로 더미 슬라이드 3개 + 더미 발화로 `analyze_consistency()` 실제 호출 성공.

- **strict 모드 JSON 스키마(제일 큰 리스크였음): 통과 확인.** checks 배열, script_diff(옵셔널) 안에 또 배열이 있는 중첩 구조까지 게이트웨이가 그대로 지켜서 응답함. `_strict_json_schema()` 정규화 방식이 이 게이트웨이/모델 조합에서 유효함이 확인됨.
- **세그먼트 인덱스 → ms 치환 정상 동작.** `evidence_at_ms`가 실제 해당 세그먼트의 start_ms와 정확히 일치.
- **consistency/script_diff 품질: 더미 데이터 기준 양호.** 3개 슬라이드 전부 정확히 SUPPORTED 판정, 대본에 없는 애드립 구간과 단어 추가("그")를 script_diff가 정확히 "추가"로 잡음.
- **그룹B 판단은 판정 자체는 정상 동작(스키마·후보 매핑 다 맞물림)하지만, 실제 정확도는 이 테스트만으론 결론 내리기 어려움.** 테스트에 넣은 "정상 용법" 예시("저희 서비스는 그 사용자 이탈률을...")를 LLM이 필러로 판정했는데, 돌이켜보면 이 예문 자체가 "그"가 가리키는 대상이 불분명해서 깔끔한 반례가 아니었음(진짜 지시대명사라면 "그 사람이 말한 대로"처럼 명확한 지시 대상이 있어야 함). 필러성 사용("그리고 음 그 그게...")은 정확히 잡음. → 결론: **파이프라인/스키마 검증은 끝났고, 그룹B 판정 정확도 자체는 여전히 실제 사람 발화 라벨링 데이터로 확인 필요**(아래 §5, 더 명확한 정상/필러 대조쌍으로 재테스트 권장).

## 4. 파이프라인 개요

```
1. SQS에서 잡 수신 (presentation_id, slide_s3_key, audio_s3_key, script)
2. S3에서 pptx·오디오 다운로드
3. [병렬] PPTX 파싱 (python-pptx, slide_index는 0-based) ┃ Clova STT(/recognizer/upload)
      → 슬라이드 텍스트                                    → 발화 전문 + word-level 타임스탬프
   (서로 입력이 안 겹쳐서 동시 실행 — run_analysis.py 1단계)
4. [병렬] 전달 지표 계산(WPM/침묵/그룹A 채움말/성량, §2) ┃ LLM 호출(정합성+그룹B 채움말, §2/§3)
   (둘 다 3번의 STT 결과만 있으면 되고 서로 필요 없어서 동시 실행 — run_analysis.py 2단계)
5. 4번의 그룹A(delivery.fillers) + 그룹B(llm_result.group_b_fillers) 채움말을 합쳐서 report_json으로 조립
6. Spring에 콜백 (POST /api/internal/analysis-results) — 실패 시 자체 재시도 후 SQS에 위임
7. 로컬 임시파일 삭제
```

**구현 완료 (2026-09-07, `app/pipeline/run_analysis.py`):** 위 3~5번 그대로 구현. `ThreadPoolExecutor`로 3번(PPTX∥STT), 4번(전달지표∥LLM) 두 단계를 각각 병렬 실행 — asyncio나 멀티프로세스까지는 필요 없다고 판단(LLM 호출은 네트워크 I/O로 대기 중 GIL 해제, Silero VAD는 PyTorch 텐서 연산 중 GIL 해제라 스레드 두 개로 실질적인 이득이 있음). 어느 단계든 실패하면 해당 예외(PptxParseError/SttError/LlmError 등)가 그대로 전파됨(all-or-nothing, §3.2와 동일 원칙). `app/parsing/pptx_parser.py`도 이때 같이 구현 — 슬라이드 도형들을 화면 위치(top/left) 기준으로 정렬해서 대략적인 읽는 순서로 텍스트를 이어붙임(그룹 도형 재귀 처리, 표는 `셀 | 셀` 형식, 발표자 노트는 제외 — 청중이 보는 내용과 발화를 비교하는 게 목적이라). "주장(claim) 단위로 나눌지" 미정이었던 부분은 슬라이드 텍스트를 통째로 LLM에 넘기고 claim 추출은 LLM 프롬프트가 담당하는 쪽으로 확정(이미 `gateway_client.py` 시스템 프롬프트에 반영되어 있었음). 단위 테스트: `tests/test_pptx_parser.py`(python-pptx로 즉석 pptx 생성해서 왕복 검증 — 텍스트박스/표/그룹도형/발표자노트/빈슬라이드/파일없음/깨진파일 등), `tests/test_run_analysis.py`(4단계 전부 mock으로 대체해서 조립 로직·예외 전파·**실제 병렬 실행 여부(타이밍 기반 테스트)** 검증).

### 4.1 전체 파이프라인 실측 검증 완료 (2026-09-07)

`scripts/test_run_analysis.py`로 `run_analysis()`를 로컬 파일 경로(`test01.m4a` + 실측 내용 기반 placeholder `test01_slides.pptx`)로 실제 끝까지 실행 — Clova(실제 API) → [Silero VAD(그룹A) ∥ LLM 게이트웨이(그룹B+정합성)] 순서로 총 5.3초 소요, 에러 없이 성공.

- **transcript**: 이전에 실측한 CLOVA 결과와 동일(재현성 확인).
- **delivery.fillers**: 그룹A 14개(이전 스팟체크로 오탐 없음 확인된 것과 동일한 결과) + 그룹B 2개("그" @14230ms, @22290ms — CLOVA word 리스트상 실제로 "그"가 있는 지점과 정확히 일치) = 총 16개. **그룹B가 이번엔 명확한 결과를 냄** — §3.1에서 애매했던 테스트 문장과 달리 실제 발표 음성에서는 "그"를 정확히 채움말로 잡아냄.
- **consistency**: 슬라이드0(SUPPORTED, 근거 12000ms) / 슬라이드1(SUPPORTED, 근거 24400ms) 둘 다 실제 발화 내용과 정확히 일치하는 근거를 정확한 세그먼트에서 찾음. 슬라이드2("감사합니다...")는 실제로 발화되지 않았으므로 NOT_MENTIONED — 정확한 판정.
- **off_topic/logic_gaps**: 빈 배열 — 실제로 이탈 발화나 논리 비약이 없는 짧은 인트로였으므로 정확.
- **script_diff**: null(대본 미제공 — 의도대로 동작).

버그 1건 발견 및 수정: `scripts/test_run_analysis.py`에 `load_dotenv()` 호출이 빠져 있어서 `.env`에 키가 다 있어도 `CLOVA_INVOKE_URL/CLOVA_SECRET_KEY가 설정되지 않음` 에러가 났음(`app/stt/clova_client.py`/`app/llm/gateway_client.py`는 자체적으로 `.env`를 로드하지 않고 `os.environ`만 읽으므로, 호출 전에 어디선가 `load_dotenv()`가 실행돼야 함 — `test_llm_gateway.py`는 이미 하고 있었는데 새 스크립트에서 빠뜨림). 스크립트 최상단에 `load_dotenv()` 추가해서 해결, 재실행 후 정상 동작 확인.

**결론: 4단계 파이프라인 전체가 실제 데이터로 처음부터 끝까지 정확하게 동작함을 확인.**

### 4.2 인프라 레이어(S3/SQS 워커) 구현 완료 (2026-09-07)

`app/clients/s3_client.py` + `app/clients/sqs_consumer.py`로 위 1・2・6・7번(S3 다운로드, SQS 폴링, 콜백, 임시파일 정리)까지 마저 구현 — `run_analysis()`를 감싸는 인프라 레이어가 전부 갖춰짐. `jeehwanhee/presentation-coach` 깃허브의 백엔드 실제 DTO(`SqsJobMessage.java`, `AnalysisCallbackRequest.java`, `PresentationService.java`)와 대조해서 `app/schemas/job.py`가 필드명·타입 다 정확히 일치함을 재확인함.

- **`s3_client.py`**: `boto3` 기본 자격증명 체인 사용(backend `S3Config.java`의 `DefaultCredentialsProvider`와 동일 방식 — EC2 배포 시 인스턴스 IAM role 자동 사용, 로컬은 `aws configure`). `S3_BUCKET` 미설정/404/기타 실패를 구분해서 `S3Error`로 래핑.
- **`sqs_consumer.py`**: `poll_forever()`가 SQS 롱폴링(20초) → 메시지 1개씩 처리 → `_process_job()`이 pptx/오디오 S3 다운로드 → 오디오 길이 계산(librosa) → `run_analysis()` 호출 → 예외를 §1 `error.code`로 매핑(`PptxParseError`→`PPTX_PARSE_FAILED`, `SttError`→`STT_FAILED`, `LlmError`→`LLM_FAILED`, S3 다운로드 실패는 슬라이드면 `PPTX_PARSE_FAILED`/오디오면 `AUDIO_NOT_FOUND`, 예상 못 한 예외는 `TIMEOUT`) → `_post_callback()`이 `X-Worker-Secret` 헤더로 콜백 POST, 실패 시 백오프(2s/5s/15s) 재시도 → **콜백 200 성공했을 때만** SQS 메시지 삭제(실패하면 그대로 둬서 재배달·DLQ에 맡김, §3.2 원칙 그대로). `python -m app.clients.sqs_consumer`로 단독 실행 가능(systemd 서비스 등록 예정).
- 단위 테스트 21개 추가(`tests/test_s3_client.py` 5개, `tests/test_sqs_consumer.py` 16개) — 전부 mock 기반(S3/HTTP/파이프라인 실호출 없음). 예외→ErrorCode 매핑, 콜백 재시도/전체실패, 콜백 성공시에만 메시지 삭제 등 검증. **전체 테스트 93개 통과.**
- **`WORKER_SECRET` 실제 백엔드 대조 검증 완료 (2026-09-07)** — `scripts/test_worker_secret.py`로 실존하지 않는 `presentation_id`(999999999)에 콜백을 보내봐서 확인. 응답이 `404 PRESENTATION_NOT_FOUND`로 나옴 → 시크릿 검증은 통과했고 id가 없어서 나는 정상 응답(백엔드 `PresentationErrorCode.java` 기준 시크릿이 틀리면 `403 INVALID_WORKER_SECRET`이 나왔을 것). 실제 데이터는 안 건드림. **콜백 경로(네트워크+인증)는 이제 실배포 백엔드 기준으로 검증 완료.**
- **아직 실제로 못 돌려본 이유**: `S3_BUCKET` 실제 값이 아직 없음 — 이거 하나만 받으면 SQS 워커 전체를 실사용 가능한 상태.

### 4.3 실배포 백엔드 통한 SQS 종단 실측 검증 완료 (2026-09-08)

`scripts/test_e2e_via_backend.py`로 실제 배포된 백엔드(CloudFront) 상대로 **발표 생성 → S3 업로드 → submit(SQS push) → 워커(`python -m app.clients.sqs_consumer`)가 실제로 잡을 처리 → 콜백 → GET 폴링으로 최종 리포트 수신**까지 전 구간을 처음으로 실측 성공(`presentation_id=6`).

- **`/submit` 500 버그 원인 규명 및 해결.** 업스트림 커밋 `submit에 result token 추가`(2026-09-08 병합)로 `POST .../submit`이 `X-Result-Token` 헤더를 필수 파라미터로 요구하도록 바뀜. 백엔드 `GlobalExceptionHandler`는 `BusinessException`/`MethodArgumentNotValidException` 이외의 모든 예외를 `@ExceptionHandler(Exception.class)` catch-all이 잡아서 그냥 `500 INTERNAL_ERROR`로 응답하도록 되어 있어서, 헤더 누락 시 Spring이 던지는 `MissingRequestHeaderException`도 제대로 된 400/403이 아니라 500으로 나왔던 것. 이전에 재현했던 `id=4`/`id=5`의 500(당시 IAM 권한 문제로 추정)도 사실은 이 헤더 누락이었을 가능성이 높음 — 헤더를 추가하자 바로 202로 정상 동작함. 결과적으로 팀원이 처음 제기했던 "X-Result-Token 헤더가 없어서" 가설이 맞았음(당시엔 컨트롤러 코드에 해당 파라미터가 없어서 반박했었는데, 그 사이 팀원이 실제로 그 기능을 추가·머지함).
- `test_e2e_via_backend.py` 개정: 1단계(발표 생성) 응답의 `result_token`을 그대로 3단계 submit 헤더(`X-Result-Token`)에 실어 보내도록 수정, 새로 구현된 `GET /api/presentations/{id}`(§2.3)로 4단계 상태 폴링(최대 5회, 3초 간격)을 추가.
- **결과(id=6)**: submit 202 → 워커가 잡 처리 → GET 폴링 3회 PROCESSING 후 4회째 DONE, 최종 리포트 정상 수신. transcript/필러/정합성 판정 내용은 §4.1의 로컬 실행 결과와 동일(같은 `test01.m4a` 입력이므로 재현성 확인됨 — 그룹A 14개, 그룹B "그" 2개 @14230ms·@22290ms, consistency 2/3 SUPPORTED 등 전부 일치).
- **이걸로 §5의 "백엔드 `/submit` 500 블로킹" 이슈 해소.** ai-service 쪽에서 실측 가능한 전체 검증(로컬 파이프라인 + 실배포 SQS 종단)이 전부 완료됨.

## 5. 남은 이슈

- [x] Clova STT word-level 타임스탬프 지원 확인 (§1.1, 2026-09-07)
- [x] Clova STT 채움말 텍스트 보존 여부 확인 → 안 됨, VAD 우회로 확정 (§1.1/§2, 2026-09-07)
- [x] `docs/API_명세서.md` `filler.type` enum 확장 (2026-09-07)
- [x] 그룹A VAD 로직 구현 (`app/audio/delivery_metrics.py`, 2026-09-07) — gap 임계값·VAD 파라미터는 여전히 추정치, 아래 항목으로 검증 필요
- [x] **그룹A VAD 실측 1차 검증 완료 (2026-09-07)** — `scripts/test_delivery_metrics.py`로 실제 녹음(`test01.m4a`)에 Silero VAD 실행, 필러 후보 14개 검출(`GAP_CANDIDATE_THRESHOLD_MS`=250ms/`VAD_MIN_SPEECH_MS`=60ms 기본값 그대로). 사용자가 직접 오디오 들으면서 스팟체크(14개 중 대표로 4개, 290ms~1850ms 길이 다양하게 골라서 확인) → **전부 실제 필러 맞음, 오탐 없음.** 기본 임계값이 최소한 이 녹음에서는 잘 맞는다는 뜻 — 다만 놓친 필러(false negative)가 있는지는 아직 확인 안 함, 다른 녹음으로 추가 검증하면 더 좋음. 일단 임계값은 현재 값 유지.
- [x] 그룹A 필러의 `text` 필드 처리 방식 확정 → `"[VAD 감지 · 원문 미상]"` 고정 placeholder (§2)
- [x] 그룹B 필러 판단 LLM 프롬프트 설계·구현 (`app/llm/gateway_client.py`, 2026-09-07) — 실데이터로 정확도 검증은 아직 필요
- [x] `app/stt/clova_client.py`의 `transcribe()` 정식 구현 (§1.2, 2026-09-07)
- [x] `analyze_consistency()` 실제 게이트웨이 호출 검증 (§3.1, 2026-09-07) — strict 스키마·세그먼트 치환·consistency/script_diff 다 정상 동작 확인. 그룹B 판정 정확도는 실데이터 검증 여전히 필요(아래 항목).
- [x] **그룹B 필러 판단 정확도 재검증 완료 (2026-09-08)** — `test01.m4a` 실측 결과에서 나온 그룹B 필러 2개(`"그"` @14230ms "...소개를 드리자면 **그** 저희는...", `"그"` @22290ms "...필요하냐면 **그** 사람들이...")를 사용자(네이티브)가 직접 판단 → **둘 다 진짜 채움말 맞음, 오탐 없음(2/2).** §3.1의 애매한 더미 예문 대신 실제 발화로 재검증해서 결론 냄.
- [x] `app/parsing/pptx_parser.py` 구현 (§4, 2026-09-07)
- [x] `app/pipeline/run_analysis.py` 최종 조립 — 4단계를 ThreadPoolExecutor로 2쌍씩 병렬 실행, 그룹A+그룹B 필러 병합 (§4, 2026-09-07)
- [x] **전체 파이프라인 실측 종단 검증 완료 (§4.1, 2026-09-07)** — `test01.m4a` + placeholder pptx로 Clova→VAD∥LLM 전체를 실제로 5.3초 만에 성공 실행, transcript/필러/정합성 판정 모두 실제 내용과 정확히 일치 확인
- [ ] LLM 최종 모델 선정 — 게이트웨이는 확정, 구체 모델은 개발 중 실데이터 테스트로 확정 예정(§3.1에서 flash-lite로 1차 실호출 성공은 확인)
- [ ] EC2 스펙 확인 — LLM을 오픈소스로 자체호스팅할지 여부를 가를 요인이라 여전히 필요. STT는 Clova로 가면서 이 서버의 컴퓨트 부담에서는 빠짐.
- [x] **백엔드 배포 완료 확인 (2026-09-07, 깃허브 `jeehwanhee/presentation-coach` 클론해서 확인)** — `.github/workflows/deploy.yml`로 EC2(`54.116.144.17`)에 SSH 자동배포(`systemctl restart coach`), 단 **backend만** 대상이고 ai-service 배포 파이프라인은 아직 레포에 없음(별도로 지환희와 상의 필요). `docs/aws.md`에 §3.2 콜백 URL 추가 확인: `http://54.116.144.17:8080/api/internal/analysis-results` — CloudFront 아직 없이 EC2 IP 직결. `.env.example`의 `CALLBACK_URL`에 반영함.
- [x] **AWS 자격증명 방식 확인** — backend의 `S3Config.java`/`SqsConfig.java`가 `DefaultCredentialsProvider.create()` 사용(자격증명 하드코딩 없음, EC2 인스턴스 IAM role 자동 사용) → ai-service도 같은 EC2에 배포하면 `boto3` 기본 자격증명 체인으로 동일하게 맞추면 됨. 로컬 개발 중엔 `coach-ai` IAM 유저의 access key를 `aws configure`로 별도 설정 필요.
- [x] `app/clients/s3_client.py`/`sqs_consumer.py` 구현 완료 (§4.2, 2026-09-07) — `S3_BUCKET`/`WORKER_SECRET` 값만 받으면 바로 실사용 가능
- [x] `S3_BUCKET` 실제 값 확보 완료 (2026-09-08) — `.env`에 반영됨
- [x] `WORKER_SECRET` 값 확보 + 실배포 백엔드 대조 검증 완료 (2026-09-07, §4.2) — `scripts/test_worker_secret.py`로 404 PRESENTATION_NOT_FOUND 확인(시크릿 일치)
- [x] **`script_diff` 실측 검증 완료 (2026-09-08)** — 실제 발화 내용 기반으로 일부러 변경/추가/생략 3종류를 섞은 대본(`scripts/test_scripts/test01_script.txt`)으로 전체 파이프라인 실행. 결과: **변경 2건(그 중 1건은 의도한 것, 1건은 사소한 어미 차이까지 잡은 것이라 판단 애매) + 생략 1건(의도한 것, 정확) 검출**, 하지만 **의도적으로 심은 "추가" 케이스(실제 발화에만 있고 대본엔 없는 "예 그렇습니다")는 완전히 놓침** — `추가` 타입 검출이 프롬프트 단에서 약한 것으로 보임, 추후 개선 필요.
- [ ] ai-service 자체 배포 방식 결정 — `.github/workflows/deploy.yml`은 현재 backend만 대상. 같은 EC2에 systemd로 올릴지, Docker로 할지, 배포 파이프라인은 어떻게 붙일지 지환희와 상의 필요.
- [x] **S3 다운로드 실측 검증 완료 (2026-09-08)** — `scripts/test_s3_download.py 4`로 `presentations/4/slides.pptx`·`.../audio.webm`(백엔드 `/submit` 실패 전에 이미 presigned URL로 업로드됐던 실제 객체) 다운로드 성공. `S3_BUCKET` 설정 + `coach-ai` IAM 자격증명(boto3 기본 체인) 실동작 확인.
- [x] **백엔드 `POST /submit` 500 에러 원인 규명 + 해결 (2026-09-08)** — 처음엔 `sendJobMessage()`(SQS 전송) 단 IAM 권한 문제로 추정했었으나, 실제 원인은 백엔드에 새로 추가된 `X-Result-Token` 헤더 필수화(§4.3)였음. 헤더를 추가해서 보내자 즉시 202로 정상 동작 확인.
- [x] **실배포 백엔드 통한 SQS 종단 실측 검증 완료 (§4.3, 2026-09-08)** — `test_e2e_via_backend.py`로 발표 생성→S3 업로드→submit→워커 처리→콜백→GET 폴링까지 전 구간 실측 성공(`id=6`, 202→DONE). 이걸로 ai-service 쪽에서 미리 해볼 수 있는 검증은 다 끝남: 전체 파이프라인(Clova→VAD∥LLM) 실측 성공, script_diff 실측 검증 완료, 그룹B 필러 정확도 실측 검증 완료(2/2), S3 다운로드 실측 검증 완료, WORKER_SECRET 실배포 대조 검증 완료, 실배포 SQS 종단 검증 완료.
- [x] `docs/API_명세서.md`에 반영된 계약(§presentation_id 정수화, PROCESSING 전이, error.code 등)에 맞춰 구현 — `job.py`/`report.py` 스키마가 백엔드 실제 DTO와 필드 단위로 일치함을 깃허브 코드 대조로 확인(§4.2)
