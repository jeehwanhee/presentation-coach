# AI 서비스 설계 (ai-service)

> 발표 리허설 코치 · AI 파트(STT·음성분석·LLM 정합검사) 설계 기록.
> Spring↔Python 계약(JSON 필드/enum)은 [`docs/API_명세서.md`](../docs/API_명세서.md) 참고. 여기는 그 계약을 채우는 내부 구현 설계.

---

## 1. STT

**결정: 네이버 클로바 스피치(CLOVA Speech) API 사용.**

- 무료 사용량은 월 20분(그 이후 15초 단위 과금, 대략 분당 30~45원 수준)이라 완전 무료는 아니지만, 별도 서버 컴퓨트 없이 바로 붙일 수 있고 한국어 인식 품질이 안정적이라는 점을 우선함.
- 자체호스팅 오픈소스 STT(faster-whisper 등)는 후보에서 제외 — Clova로 가면서 EC2에 STT용 컴퓨트(GPU 필요 여부 등)를 따로 고민할 필요가 없어짐.
- **확인 필요**: Clova가 word-level(또는 그에 준하는) 타임스탬프를 제공하는지, 채움말("음"/"어"/"그" 등)을 전사 텍스트에서 지우지 않고 남기는지 — 리포트 스키마의 `evidence_at_ms`, `fillers[].at_ms`/`text` 필드가 여기 의존함. 붙이기 전에 샘플 오디오로 먼저 테스트해볼 것.
- **비용 관리**: 월 20분 무료 초과분은 종량제라, 개발 중 반복 테스트 + 대회 당일 실사용을 감안해 최소한의 예산을 잡아두고 사용량 알림을 걸어둘 것.

## 2. LLM — 아직 미정

정합성 판정(`consistency`) / 주제이탈(`off_topic`) / 논리비약(`logic_gaps`) / 대본대조(`script_diff`)를 구조화된 JSON으로 뽑아내는 역할. 후보와 트레이드오프:

| 후보 | 비용 | 데이터 학습 이슈 | 비고 |
|---|---|---|---|
| Gemini(Flash) 무료 티어 | 무료 | 무료 티어는 학습에 사용됨(구글 공식 정책) | 발표 발화 전문이 나가므로 개인정보 관점에서 걸림 |
| Gemini 유료 전환 | 저렴 | 유료는 학습에 미사용 | 코드 변경 거의 없이 무료 티어의 문제만 해결 |
| OpenAI(GPT-4o-mini급) | 저렴 | API 기본값이 학습에 미사용 | Structured Outputs 안정적, 현재 1순위 후보 |
| Anthropic Claude(Haiku급) | OpenAI보다 약간 비쌈 | 학습에 미사용 | 마찬가지로 안전한 선택지 |
| 네이버 Clova Studio / 업스테이지 Solar | 미확인(콘솔에서 직접 확인 필요) | — | 한국어 특화, "국산 AI 활용" 스토리 가능 |
| 오픈소스 모델 자체호스팅 | EC2 컴퓨트만 | 외부 전송 자체가 없음(가장 안전) | EC2에 GPU 없으면 속도·품질 리스크 큼, 엔지니어링 비용 큼 |

**결정 필요 시점**: STT 파이프라인(Clova 연동) 붙이고 나서, 트랜스크립트 포맷 확정되면 바로 이어서 결정.

## 3. 파이프라인 개요

```
1. SQS에서 잡 수신 (presentation_id, slide_s3_key, audio_s3_key, script)
2. S3에서 pptx·오디오 다운로드
3. PPTX 파싱 (python-pptx) → 슬라이드별 텍스트/주장 추출, slide_index는 0-based
4. Clova Speech로 STT → 발화 전문 + 타임스탬프
5. 전달 지표 계산: WPM, 침묵 구간(VAD), 채움말(type+text+at_ms), 성량 변화
6. LLM 호출: (슬라이드 주장 + 발화 전문 + 대본[선택]) → consistency/off_topic/logic_gaps/script_diff 구조화 JSON
7. 5+6 결과를 report_json으로 조립
8. Spring에 콜백 (POST /api/internal/analysis-results) — 실패 시 자체 재시도 후 SQS에 위임
9. 로컬 임시파일 삭제
```

## 4. 남은 이슈

- Clova STT의 타임스탬프/채움말 보존 여부 검증 (§1)
- LLM 최종 선정 (§2)
- EC2 스펙 확인 — LLM을 오픈소스로 자체호스팅할지 여부를 가를 요인이라 여전히 필요. STT는 Clova로 가면서 이 서버의 컴퓨트 부담에서는 빠짐.
- `docs/API_명세서.md`에 반영된 계약(§presentation_id 정수화, PROCESSING 전이, error.code 등)에 맞춰 구현
