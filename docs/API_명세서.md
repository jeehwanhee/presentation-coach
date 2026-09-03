# 발표 리허설 코치 — API 명세서

> 원티드 AI Championship 2026 · Spring(백엔드) ↔ Python(AI 워커) 개발 계약
> 필드명·enum을 바꾸면 반드시 이 문서부터 고칠 것.

---

## 0. 아키텍처 전제 (확정)

- **인증 없음.** 세션 생성 시 `result_token`(추측 불가능한 랜덤) 발급 → URL에 담아 사용자에게 제공 → 그 URL로 **3일간 조회**. 로그인·삭제 없음.
- **디스패치**: Spring → **SQS** 메시지로 잡 전달. **결과 회수**: 워커 → **Spring 내부 콜백 API**로 전송, Spring이 MySQL 기록.
- **경계**: Python은 **S3(읽기) + SQS(소비) + 콜백(HTTP)**만 사용. MySQL 직접 접근 안 함 → Java↔Python 경계 = 이 JSON 계약.
- **PPTX 파싱은 Python에서 담당.** pptx의 S3 키만 전달.
- **대본은 텍스트 붙여넣기**(파일 아님), **발표(presentation) 단위**로 보관.
- **슬라이드별 소요시간·슬라이드 전환 캡처 없음.** (녹음 UI는 시작/정지만.)
- 파일(오디오·pptx)은 **presigned URL로 클라이언트 → S3 직접 업로드.**

---

## 1. 공통 규약

- **Base URL**: `https://<cloudfront-domain>/api`
- **Content-Type**: `application/json` (파일만 S3 presigned PUT)
- **인증 수단 둘**:
  - `X-Result-Token: <token>` — 결과 조회의 **유일한 열쇠**(소지 = 조회 권한).
  - `X-Worker-Secret: <secret>` — Python → Spring 내부 콜백 전용, 외부 비노출. 그냥 우리끼리 정하는 문자열
- **에러 포맷**: `{ "error": { "code": "PRESENTATION_EXPIRED", "message": "..." } }`
- **상태 코드**: 200 · 201 생성 · 202 접수 · 400 · 403 토큰 불일치 · 404 · **410 만료** · 500

### 접근 제어
- `result_token` **소지 = 조회 권한.** 로그인이 없어 "작성자"와 "링크를 건네받은 제3자"를 구분 못 함 — URL 가진 사람은 누구나 조회.
- **리포트 삭제 없음.** 데이터는 생성 **3일 후 자동 만료**로만 소멸.
- 토큰은 **256비트(base64url ≈ 43자) 랜덤.** presentation_id는 순차여도 무방(보안은 토큰이 담당).

### Enum

| 이름 | 값 |
|---|---|
| `presentation.status` | `PENDING` · `PROCESSING` · `DONE` · `FAILED` |
| `consistency.verdict` | `SUPPORTED`(뒷받침됨) · `NOT_MENTIONED`(미언급) · `NO_BASIS`(근거없음) |
| `filler.type` | `음` · `어` · `그` · `기타` |

---

## 2. 프론트엔드 ↔ Spring REST API

### 2.1 발표 생성 + 슬라이드 업로드 URL + 대본(텍스트) 수신
`POST /api/presentations`
자료(pptx)와 대본은 발표 단위 정보라 여기서 함께 처리.
```json
// Request
{ "title": "졸업 발표 리허설", "script": "발표 대본 전문(텍스트, 선택, 없으면 null)" }
// Response 201
{
  "presentation_id": "p_456",
  "slide_upload_url": "https://s3...&X-Amz-Signature=...",   // 여기에 pptx PUT
  "slide_s3_key": "presentations/p_456/slides.pptx",
  "audio_upload_url": "https://s3...&X-Amz-Signature=...",   // 여기에 오디오 PUT
  "audio_s3_key": "presentations/p_456/audio.webm",
  "result_token": "rt_A8f3C9d2E5...",
  "result_url": "https://<프론트>/r/p_456?token=rt_A8f3C9d2E5...",
  "expires_at": "2026-09-06T12:00:00Z"
}
```
→ 클라이언트가 `slide_upload_url`로 pptx를 S3에 직접 PUT. 대본은 이 요청 바디로 이미 서버에 저장됨.
- 토큰/URL은 여기서 미리 발급(오디오 업로드·상태 조회에 필요). **사용자에게 링크를 노출하는 시점은 분석 완료 후 결과 화면**이고, 그때 "이 링크 저장하세요, 3일 후 만료, 링크 가진 사람은 열람 가능" 안내.

### 2.2 분석 제출 (업로드 완료 후 큐잉)
`POST /api/presentations/{presentation_id}/submit`
```json
// Request
{ "audio_duration_ms": 210000 }
// Response 202
{ "presentation_id": "p_456", "status": "PENDING" }
```
- 검증: `audio_duration_ms` ≤ **600000(10분 하드리밋)**, 초과 시 400.
- 서버가 SQS에 잡 메시지 push(§3.1).

### 2.3 상태 폴링 + 리포트 조회
`GET /api/presentations/{presentation_id}` — 헤더 `X-Result-Token` 필수.
```json
// 200 분석 중
{ "presentation_id": "p_456", "status": "PROCESSING", "report": null }
// 200 완료
{ "presentation_id": "p_456", "status": "DONE", "report": { /* 2.3.1절 */ } }
// 200 실패
{ "presentation_id": "p_456", "status": "FAILED", "error": { "code": "STT_FAILED", "message": "..." } }
// 403 토큰 불일치 / 410 만료
{ "error": { "code": "PRESENTATION_EXPIRED", "message": "리포트가 만료되었습니다." } }
```
- `now > expires_at` → **410**. 토큰 불일치 → **403**.
- **CloudFront에서 이 경로 캐싱 비활성화 필수.** 폴링 주기 2~3초.

### 2.3.1 리포트 스키마 (제품 핵심)
```json
{
  "consistency": {                         // 발표자료 정합 (코어, 최상단 노출)
    "checks": [
      { "slide_index": 2, "claim": "처리시간 40% 단축", "verdict": "SUPPORTED",
        "evidence_span": "처리시간을 40퍼센트 줄였고요", "evidence_at_ms": 51200 },
      { "slide_index": 3, "claim": "비용 절감 효과", "verdict": "NOT_MENTIONED",
        "evidence_span": null, "evidence_at_ms": null }
    ],
    "supported_count": 7, "total_claims": 12
  },
  "off_topic": [
    { "start_ms": 130000, "end_ms": 148000, "text": "...", "reason": "슬라이드 범위 밖" }
  ],
  "logic_gaps": [
    { "at_ms": 88000, "text": "A라서 C인데 B 근거 없음", "note": "..." }
  ],
  "delivery": {
    "wpm": 132,
    "silence_total_ms": 18400,
    "long_pauses": [ { "start_ms": 61000, "duration_ms": 4200 } ],
    "fillers": [ { "type": "음", "at_ms": 12800, "duration_ms": 600 } ],
    "filler_count": 11,
    "volume_variation": { "relative_std": 0.34, "note": "상대 변화만, 해석 없음" }
  },

  //대본 없을때
  "script_diff": null,

  //대본 있을때
  "script_diff": {
    "matched_ratio": 0.82,
    "deviations": [ { "at_ms": 70000, "script_text": "...", "spoken_text": "...", "kind": "생략|추가|변경" } ]
  }
}
```

---

## 3. Spring ↔ Python 계약

### 3.1 잡 메시지 (Spring → SQS →  Python)
```json
{
  "presentation_id": "p_456",
  "slide_s3_key": "presentations/p_456/slides.pptx",
  "audio_s3_key": "presentations/p_456/audio.webm",
  "script": "대본 전문 또는 null",
  "callback_url": "https://<cloudfront-domain>/api/internal/analysis-results"
}
```
→ Python: S3에서 pptx·오디오 다운로드 → PPTX 파싱(슬라이드별 텍스트·주장) → STT → VAD·정렬·채움말 → LLM 정합/주제이탈/논리비약(+대본 있으면 diff) → 3.2절 콜백.

### 3.2 결과 콜백 (Python → Spring)
`POST /api/internal/analysis-results` — 헤더 `X-Worker-Secret` 필수.
```json
// 성공
{ "presentation_id": "p_456", "status": "DONE",
  "transcript": [ { "start_ms": 0, "end_ms": 3200, "text": "안녕하세요..." } ],
  "report": { /* §2.3.1절 */ } }
// 실패
{ "presentation_id": "p_456", "status": "FAILED", "error": { "code": "STT_FAILED", "message": "..." } }
```
- Spring: 시크릿 검증 → `status` 갱신 + `transcript`·`report` 저장.
- 재시도: 콜백 200 후 Python에서 SQS 메시지 삭제. 실패/타임아웃 → 미삭제 → visibility timeout 후 재배달. 초과분 DLQ.
- 오디오 파기: Spring이 저장 확정 후 오디오 S3 객체 삭제. Python은 로컬 임시파일 삭제.

---

---

## 4. 해야할 것

- [ ] **Enum**(status/verdict/filler) 동결
- [ ] 리포트 스키마(2.3.1절) 동결 — 프론트·워커 공동 계약, 최우선
- [ ] 3.2절 필드 동결
- [ ] `X-Worker-Secret` 공유
