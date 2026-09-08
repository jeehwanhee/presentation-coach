"""네이버 클로바 스피치(STT) 클라이언트.

`/recognizer/upload`(장문 인식, 로컬 파일 직접 업로드) 방식. 호출 파라미터·응답
형태는 `scripts/test_clova_upload.py`로 실측 검증됨(AI_설계.md §1.1):
- word-level 타임스탬프 지원 확인(`segments[].words`: [시작ms, 종료ms, "단어"]).
- 채움말("음"/"어") 텍스트는 `noiseFiltering:false`로도 사라짐 — 대신 인접 단어
  타임스탬프 사이 빈틈이 정확히 남아서 app/audio/delivery_metrics.py가 VAD로 우회.
- `diarization`은 요청값과 무관하게 도메인 설정(화자 인식 미사용)을 따라감 —
  아래서 False로 명시하는 건 의도를 명확히 하기 위함이지 실제로 결과를 바꾸진 않음.
- 도메인을 막 생성한 직후엔 일시적으로 400(speaker detect is off)이 날 수 있음
  (프로비저닝 지연으로 추정) — 그런 경우 잠깐 기다렸다 재시도.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.schemas.job import TranscriptSegment


@dataclass
class WordTiming:
    text: str
    start_ms: int
    end_ms: int


@dataclass
class TranscriptResult:
    full_text: str
    words: list[WordTiming] = field(default_factory=list)  # word-level 타임스탬프.
    # app/audio/delivery_metrics.py(그룹A VAD gap 계산)와
    # app/llm/gateway_client.py(그룹B 채움말 후보 탐지)가 사용.
    segments: list[TranscriptSegment] = field(default_factory=list)  # CLOVA 응답의
    # segments[]를 그대로 옮긴 것 — start_ms/end_ms/text 단위의 문장급 청크.
    # app/llm/gateway_client.py가 LLM 프롬프트의 근거 단위로 쓰고, §3.2 콜백의
    # transcript 필드도 이 리스트를 그대로 사용(run_analysis.py에서 변환 불필요).


class SttError(Exception):
    """STT 실패. app.schemas.job.ErrorCode.STT_FAILED에 대응."""


_UPLOAD_PATH = "/recognizer/upload"
_TIMEOUT_SECONDS = 120


def _request_params() -> dict:
    return {
        "language": "ko-KR",
        "completion": "sync",
        "wordAlignment": True,
        "fullText": True,
        "noiseFiltering": False,
        "diarization": {"enable": False},
    }


def transcribe(audio_path: Path) -> TranscriptResult:
    """클로바 스피치 API(/recognizer/upload)로 오디오를 전사한다.

    .env의 CLOVA_INVOKE_URL/CLOVA_SECRET_KEY를 사용.

    Raises:
        SttError: 설정 누락, 파일 없음, 네트워크 오류, 4xx/5xx 응답, 응답 파싱
            실패 등 인식 실패 전반.
    """
    invoke_url = os.environ.get("CLOVA_INVOKE_URL", "").rstrip("/")
    secret_key = os.environ.get("CLOVA_SECRET_KEY", "")
    if not invoke_url or not secret_key:
        raise SttError("CLOVA_INVOKE_URL/CLOVA_SECRET_KEY가 설정되지 않음(.env 확인)")
    if not audio_path.exists():
        raise SttError(f"오디오 파일을 찾을 수 없음: {audio_path}")

    url = f"{invoke_url}{_UPLOAD_PATH}"
    headers = {"X-CLOVASPEECH-API-KEY": secret_key}

    try:
        with open(audio_path, "rb") as f:
            files = {
                "media": (audio_path.name, f, "application/octet-stream"),
                "params": (None, json.dumps(_request_params()), "application/json"),
            }
            resp = httpx.post(url, headers=headers, files=files, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise SttError(f"CLOVA 요청 실패: {exc}") from exc

    if resp.status_code != 200:
        raise SttError(f"CLOVA 응답 실패 (status={resp.status_code}): {resp.text}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise SttError(f"CLOVA 응답 파싱 실패: {exc}") from exc

    return _parse_response(data)


def _parse_response(data: dict) -> TranscriptResult:
    """CLOVA `/recognizer/upload` 응답(dict)을 TranscriptResult로 변환한다.

    segments[]가 없거나 words가 없는 경우도 방어적으로 처리 — 짧은 오디오나
    무음 구간만 있는 경우 등.
    """
    full_text = data.get("text", "") or ""
    segments: list[TranscriptSegment] = []
    words: list[WordTiming] = []

    for seg in data.get("segments", []) or []:
        start = seg.get("start")
        end = seg.get("end")
        if start is None or end is None:
            continue
        segments.append(TranscriptSegment(start_ms=start, end_ms=end, text=seg.get("text", "")))

        for w in seg.get("words", []) or []:
            # CLOVA 포맷: [시작ms, 종료ms, "단어"]
            if not isinstance(w, list) or len(w) < 3:
                continue
            w_start, w_end, w_text = w[0], w[1], w[2]
            words.append(WordTiming(text=w_text, start_ms=w_start, end_ms=w_end))

    return TranscriptResult(full_text=full_text, words=words, segments=segments)
