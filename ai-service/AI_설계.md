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

## 2. LLM

**결정: 건국대 API Gateway(Mindlogic, factchat-cloud.mindlogic.ai) 사용. 구체적 모델은 개발 중 실데이터로 검증하며 확정.**

- OpenAI Chat Completions 포맷과 호환(`/v1/gateway/chat/completions/`), 기존 OpenAI SDK에 base_url만 바꿔서 사용 가능. `response_format`(json_schema, strict) 지원 확인 완료 — 리포트 스키마(2.3.1절)를 그대로 강제 출력 가능. 모델을 나중에 바꿔도 이 방식은 그대로 유지됨(같은 게이트웨이, 같은 스키마).
- **모델 선정 기준: 비용(크레딧 소모) 최우선, 단 정합성 판정처럼 reasoning이 필요한 작업의 품질도 같이 봐야 해서 실제 개발 중 확정.** 일단 제일 저렴한 모델로 시작 → 실데이터로 판정 정확도 테스트 → 부족하면 한 단계 위 모델로 올리는 방식.
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
- LLM 최종 모델 선정 (§2) — 게이트웨이는 확정, 구체 모델은 개발 중 실데이터 테스트로 확정 예정
- EC2 스펙 확인 — LLM을 오픈소스로 자체호스팅할지 여부를 가를 요인이라 여전히 필요. STT는 Clova로 가면서 이 서버의 컴퓨트 부담에서는 빠짐.
- `docs/API_명세서.md`에 반영된 계약(§presentation_id 정수화, PROCESSING 전이, error.code 등)에 맞춰 구현
