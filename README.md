# 발표 리허설 코치

> 대본과의 일치도, 채움말 빈도, 발화 속도등의 리포트로 발표 리허설을 분석해주는 웹 서비스  

> 원티드 AI Championship 2026 출품작

발표 자료(PPTX)와 발표 녹음본을 올리면, AI가 "슬라이드의 각 주장이 발화에서 근거를 갖고 전달됐는지"를 원문 인용과 함께 판정하고, 주제 이탈, 논리 비약, 전달 지표 리포트를 만들어 줍니다.

---

## 리포트 핵심 기능

- **발표자료 정합 검사** — 슬라이드 주장별로 `뒷받침됨 / 미언급 / 근거없음`을 발화 원문 인용과 함께 판정
- **주제 이탈 감지** — 슬라이드 범위를 벗어난 발화 구간
- **논리 비약 감지** — 근거가 빠진 논리 전개 구간
- **전달 지표** — 발화 속도(WPM), 침묵 구간, 채움말(음·어·그) 빈도, 성량 변화(상대)
- **문장 끝 처리** 문장을 끝낼 때, 말 끝을 흐리는 지
- **대본 대조** *(대본 업로드 시)* — 대본과 실제 발화의 차이

---
<img width="1000" alt="Image" src="https://github.com/user-attachments/assets/1fcc0f7a-542f-414c-8bd8-781440d52f1d" />

## 레포 구조

| 폴더 | 역할 | 스택 |
|---|---|---|
| [`frontend/`](./frontend) | 녹음,업로드,리포트 화면 | React |
| [`backend/`](./backend) | REST API,인증,잡 오케스트레이션 | Spring |
| [`ai-service/`](./ai-service) | STT,음성 분석,LLM 정합 검사 | Python |
| [`docs/`](./docs) | 설계 문서 | Markdown |

---

## 기술 스택

**Frontend** : React, TypeScript  
**Backend(Server)** : Spring, AWS EC2, SQS, S3  
**ai-service** : Python, FastAPI, STT, LLM  
**Data** : MySQL  

---

## 처리 흐름

1. 발표 생성 → pptx·오디오 업로드 URL + 결과 URL(토큰) 발급
2. 브라우저에서 pptx·녹음을 S3에 직접 업로드
3. 분석 제출 → 백엔드가 SQS에 잡 등록
4. ai-service에서 STT → 음성 분석 → LLM 정합 검사 → 결과 콜백
5. 결과 URL로 리포트 조회 (3일 후 만료)


---

## 인프라

- **EC2** (ap-northeast-2) — Spring 백엔드를 systemd 서비스(`coach.service`)로 상시 구동, MariaDB도 같은 인스턴스에 설치
- **CloudFront** — EC2 앞단에서 HTTPS 종단.
- **S3** — pptx/오디오 파일 저장. 브라우저 → S3 직접 업로드(presigned URL), 분석 완료 후 오디오는 즉시 삭제, 만료된 발표는 배치가 pptx까지 정리
- **SQS** — 분석 잡 큐. Spring이 발행, AI 워커가 소비
- **IAM** — 백엔드는 EC2 인스턴스 역할(S3 PutObject/DeleteObject, SQS SendMessage)로 동작, 정적 액세스 키 미사용. AI 워커용 IAM 계정은 별도로 최소 권한(S3 GetObject, SQS Receive/Delete/GetQueueAttributes)만 부여
- **CI/CD** — GitHub Actions가 `main` push 시 SSH로 EC2에 접속해 pull/build/재시작까지 자동 처리


---

## 역할

- 지환희 : **Backend / Infra**
- 함영찬 : **AI / 음성 분석**
- **Frontend / Design** — AI 활용
