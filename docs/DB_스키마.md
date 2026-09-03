# 발표 리허설 코치 — DB 스키마 (MySQL)

> API 명세서 기준. 로그인 없음, 삭제 없음, 결과 토큰 조회, 3일 만료.

---

## 0. 설계 방침

- **테이블은 `presentation` 하나.** 로그인·회차 재활용이 없으니 발표와 녹음이 1:1 → 별도 `session` 테이블 불필요.
- **분석 결과(리포트·전사)는 JSON 컬럼에 통째로 저장.** Pythono이 한 번 쓰고 프론트가 통째로 한 번 읽을 뿐, 내부 검색·집계 없음 → 정규화는 오버엔지니어링.
- **파일(오디오·pptx)은 S3.** DB엔 S3 키(경로 문자열)만.
- **id는 순차(AUTO_INCREMENT)로 안전.** URL 보안은 `result_token`이 담당.

---

## 1. DDL

```sql
CREATE TABLE presentation (
    id                 BIGINT        NOT NULL AUTO_INCREMENT,
    title              VARCHAR(255)  NOT NULL,
    script             TEXT          NULL,            -- 대본(텍스트, 선택)

    slide_s3_key       VARCHAR(512)  NOT NULL,        -- pptx 경로 (생성 시 서버가 확정)
    audio_s3_key       VARCHAR(512)  NOT NULL,        -- 오디오 경로 (생성 시 확정, 업로드는 이후)

    status             ENUM('PENDING','PROCESSING','DONE','FAILED') NOT NULL DEFAULT 'PENDING',
    result_token       CHAR(43)      NOT NULL,        -- 256bit base64url, 유일한 접근 열쇠
    audio_duration_ms  INT           NULL,            -- submit 시 채움

    transcript_json    JSON          NULL,            -- Pythono이 채움
    report_json        JSON          NULL,            -- Pythono이 채움 (API 2.3.1)
    error_code         VARCHAR(64)   NULL,
    error_message      TEXT          NULL,

    expires_at         DATETIME      NOT NULL,        -- created_at + 3일
    created_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_presentation_token (result_token),   -- 토큰 조회용
    KEY idx_presentation_expires (expires_at)          -- 만료 정리 배치용
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## 2. 컬럼 노트

| 컬럼 | 설명 |
|---|---|
| `script` | 대본(텍스트, 선택). 발표 생성(API 2.1) 요청 바디로 저장. 워커 잡에 실려 `script_diff` 계산에 사용. |
| `slide_s3_key` / `audio_s3_key` | 파일 실물은 S3, DB엔 경로만. 생성 시 서버가 `presentations/{id}/slides.pptx`·`.../audio.webm`로 확정(업로드는 이후 presigned PUT). |
| `status` | 생성 직후 기본 `PENDING`. submit → 큐잉, 워커 시작 시 `PROCESSING`, 콜백으로 `DONE`/`FAILED`. |
| `result_token` | 256비트 랜덤 → base64url 43자. 조회의 유일한 열쇠. 유니크 인덱스라 토큰 조회 빠름. |
| `audio_duration_ms` | 생성 시엔 없음(NULL), **submit 요청에서 채움**(10분 하드리밋 검증에 사용). |
| `transcript_json` / `report_json` | 워커 콜백으로 채워짐. 프론트는 `report_json`을 통째로 받아 렌더. `consistency.slide_index`는 pptx 파싱 산물이라 그대로 들어감. |
| `expires_at` | `created_at + INTERVAL 3 DAY`. 조회 시 `now > expires_at`이면 410. |

---

## 3. 주요 쿼리 패턴

```sql
-- 조회 (토큰 + 만료 동시 검증) : API 2.3
SELECT status, report_json, error_code, error_message, expires_at
FROM presentation
WHERE id = :id AND result_token = :token;
--   행 없음 → 403(토큰 불일치),  now > expires_at → 410

-- 분석 제출 : API 2.2 (이후 SQS push)
UPDATE presentation
SET audio_duration_ms = :dur, status = 'PENDING'
WHERE id = :id;

-- 워커 결과 반영 : API 3.2 콜백
UPDATE presentation
SET status = :status, transcript_json = :tj, report_json = :rj,
    error_code = :ec, error_message = :em
WHERE id = :id;

-- 만료 정리 배치 (스케줄러: 매시간 등)
DELETE FROM presentation WHERE expires_at < NOW();
```

---

## 4. 만료·정리 메커니즘

- **읽기 차단**: 조회 시 `expires_at` 비교로 410(데이터가 남아 있어도 노출 안 함).
- **실제 삭제 배치**: Spring `@Scheduled`(예: 매시간) `expires_at < now` 행 삭제.
- **파일 정리**: 오디오는 분석 직후 S3에서 파기. pptx는 S3 라이프사이클로 3일 뒤 자동 삭제 걸어두면 이중 안전.

