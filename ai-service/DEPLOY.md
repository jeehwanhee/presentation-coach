# ai-service 서버 배포 가이드 (EC2, systemd)

> 발표 리허설 코치 · ai-service(SQS 워커) 배포 방법.
> 백엔드(`coach.service`)랑 같은 방식(systemd, 같은 EC2)으로 맞췄습니다.
> ai-service 코드/의존성 내용은 함영찬이 정리, 실제 EC2 접속·systemd 등록은 이 문서 보고 진행해주시면 됩니다.

## 배경

- ai-service는 SQS 큐를 계속 폴링하는 **상주 프로세스**(`app/clients/sqs_consumer.py`)입니다. 백엔드처럼 HTTP 요청을 받는 서버가 아니라, 그냥 무한루프 돌면서 SQS 메시지를 처리하는 워커라서 웹서버 설정(nginx/포트 등)은 필요 없습니다.
- 백엔드가 `POST /submit`에서 SQS에 잡을 넣으면, 이 워커가 그걸 집어서 S3 다운로드 → STT/LLM 분석 → 백엔드 콜백(`POST /api/internal/analysis-results`)까지 처리합니다.
- 코드는 이미 `origin/main`에 올라가 있고(`ai-service/` 디렉토리), 실배포 백엔드 기준으로 전체 파이프라인 실측 검증까지 끝난 상태입니다(`ai-service/AI_설계.md` §4.3 참고).

## 0. 사전 확인

```bash
ssh <EC2계정>@54.116.144.17
cd ~/presentation-coach
git pull origin main
free -h && df -h   # 메모리/디스크 여유 확인 — torch/silero-vad 설치가 꽤 무거움(1GB+)
```

## 1. Python 환경 준비

```bash
cd ~/presentation-coach/ai-service

python3 --version
# 없으면: sudo apt update && sudo apt install -y python3 python3-venv python3-pip

which ffmpeg
# 없으면: sudo apt install -y ffmpeg   (librosa가 m4a/webm 오디오 디코딩에 필요)

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # torch 포함이라 설치 시간 좀 걸림
```

## 2. `.env` 생성 (서버 전용 — git에는 절대 안 올라감, `.env.example` 참고해서 작성)

```bash
nano .env
```

```
CLOVA_INVOKE_URL=<NCP 콘솔에서 발급받은 CLOVA Speech Invoke URL>
CLOVA_SECRET_KEY=<CLOVA Secret Key>

LLM_GATEWAY_API_KEY=<건국대 API Gateway 키>
LLM_GATEWAY_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway
LLM_GATEWAY_MODEL=gemini-3.5-flash-lite

AWS_REGION=ap-northeast-2
S3_BUCKET=<실제 S3 버킷명>
SQS_QUEUE_URL=<실제 SQS 큐 URL>

# 워커가 백엔드랑 같은 EC2에 있으니 외부 IP 대신 localhost로 (CloudFront 우회, 더 빠르고 안전)
CALLBACK_URL=http://localhost:8080/api/internal/analysis-results

WORKER_SECRET=<백엔드 application-secret.yml의 worker secret과 동일한 값>
```

- AWS 자격증명(access key)은 `.env`에 안 넣습니다 — EC2 인스턴스 IAM role이 자동으로 잡아줍니다(`S3Config.java`/`SqsConfig.java`랑 동일한 `DefaultCredentialsProvider` 방식). 단, 이 인스턴스 role에 `s3:GetObject`(버킷 대상)와 `sqs:ReceiveMessage`/`sqs:DeleteMessage`/`sqs:SendMessage`(백엔드가 잡 넣을 때 필요) 권한이 있는지 한 번 확인해주세요.
- `WORKER_SECRET`은 백엔드 쪽 설정값과 반드시 일치해야 콜백이 통과합니다(안 맞으면 콜백이 403).

## 3. systemd 유닛 등록

```bash
sudo nano /etc/systemd/system/coach-ai.service
```

```ini
[Unit]
Description=Coach AI Service SQS Worker
After=network.target

[Service]
Type=simple
User=<EC2계정>
WorkingDirectory=/home/<EC2계정>/presentation-coach/ai-service
EnvironmentFile=/home/<EC2계정>/presentation-coach/ai-service/.env
ExecStart=/home/<EC2계정>/presentation-coach/ai-service/.venv/bin/python -m app.clients.sqs_consumer
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coach-ai
sudo systemctl status coach-ai
```

## 4. 정상 동작 확인

```bash
sudo journalctl -u coach-ai -f
```

로그에 `SQS 폴링 시작: https://sqs...`가 찍히면 정상입니다. 이 상태에서 프론트/테스트 스크립트로 발표를 하나 제출하면, 이 로그에 `잡 수신 → 잡 완료, 메시지 삭제`가 순서대로 찍히는지 보면 됩니다(안 되면 `.env`의 `WORKER_SECRET`/`SQS_QUEUE_URL`/`S3_BUCKET`부터 의심).

## (선택) 자동배포 붙이기

지금 `.github/workflows/deploy.yml`은 backend만 대상입니다. 원하면 아래를 추가해서 push할 때마다 ai-service도 같이 갱신되게 할 수 있습니다:

```yaml
            cd ~/presentation-coach/ai-service
            source .venv/bin/activate
            pip install -r requirements.txt
            sudo systemctl restart coach-ai
```
(위 backend 배포 스텝 밑에 이어붙이면 됩니다.)
