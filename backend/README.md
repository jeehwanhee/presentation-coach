# Backend

발표 리허설 코치의 REST API 서버. 발표 생성/업로드 URL 발급, 분석 제출(SQS 발행), 결과 조회, AI 워커 콜백 수신을 담당.

## 기술 스택

- Java 17, Spring Boot 4.1.1
- Spring Data JPA + MariaDB
- AWS SDK v2 (S3, SQS)
- Gradle

## 패키지 구조

```
com.konkuk.coach
├── config/       # S3Client/SqsClient 빈, CORS 설정
├── controller/    # REST 엔드포인트
├── service/       # 비즈니스 로직
├── domain/        # JPA 엔티티
├── repository/     # Spring Data JPA 리포지토리
├── dto/
│   ├── request/    # 요청 바디
│   └── response/    # 응답 바디
├── exception/      # 공통 예외 처리 (BusinessException + CustomErrorCode)
└── scheduler/      # 만료 데이터 정리 배치
```

## API

전체 스펙은 [`docs/API_명세서.md`](../docs/API_명세서.md) 참고.

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| POST | `/api/presentations` | 발표 생성 + 업로드 URL 발급 | - |
| POST | `/api/presentations/{id}/submit` | 분석 제출 (SQS push) | `X-Result-Token` |
| GET | `/api/presentations/{id}` | 상태 폴링 / 리포트 조회 | `X-Result-Token` |
| POST | `/api/internal/analysis-results` | AI 워커 결과 콜백 | `X-Worker-Secret` |

## 로컬 실행

`src/main/resources/application-local.properties`에 아래 값 채우고 실행:

```properties
DB_URL=jdbc:mariadb://localhost:3306/coach?serverTimezone=Asia/Seoul&characterEncoding=UTF-8
DB_USERNAME=
DB_PASSWORD=
S3_BUCKET=
SQS_QUEUE_URL=
WORKER_SECRET=
FRONT_BASE_URL=
BACKEND_BASE_URL=
```

AWS 자격증명은 `aws configure`로 로컬에 설정

```bash
./gradlew bootRun
```

## 테스트 코드 실행

```bash
./gradlew test
```

## 배포

EC2에 systemd 서비스(`coach.service`)로 상시 구동. `main` 브랜치에 push되면 GitHub Actions가 서버에 SSH 접속해 `git pull` → `./gradlew bootJar` → `systemctl restart coach`까지 자동으로 처리 (`.github/workflows/deploy.yml`)

앞단에 CloudFront를 붙여 HTTPS로 서빙
