"""전달 지표 계산 — WPM, 침묵(VAD), 채움말(그룹A), 성량 변화.

STT 결과(word-level 타임스탬프)에 의존하므로 stt/clova_client.transcribe() 다음 단계.
app.schemas.report.Delivery를 그대로 채워서 반환하는 게 목표.

설계 근거: AI_설계.md §2 "채움말(filler) 탐지 전략 — 2026-09-07 확정".
- 그룹A("음"/"어"): CLOVA가 전사 텍스트에서 지워버리지만, 인접 단어 타임스탬프
  사이의 빈틈(gap)은 정확하게 남는다(§1.1 실측 확인). gap이 일정 길이 이상이면
  그 구간만 원본 오디오에서 잘라 Silero VAD로 "목소리 있음"인지 확인해서 필러
  후보로 잡는다. 전체 파일에 VAD를 돌리지 않고 CLOVA가 못 채운 gap에만
  타겟팅하는 구조라 계산량이 적고 로직이 단순하다(§2 1~2단계).
- 같은 gap 분류 결과를 침묵 지표(silence_total_ms/long_pauses)에도 그대로 쓴다:
  VAD로 "목소리 없음"이 확인된 gap은 필러가 아니라 순수 침묵으로 집계한다.
- 그룹B("그"/"저"/"뭐"/"뭔가"/"좀"/"막"/"그냥"/"같다" 등 실단어 채움말)는 여기서
  다루지 않는다. STT 텍스트에 항상 남아있고 정상 용법과 구분하려면 문맥 판단이
  필요해서 app/llm/gateway_client.py(LLM 호출)에서 처리하고 파이프라인에서 병합.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.schemas.report import Delivery, Filler, FillerType, LongPause, VolumeVariation
from app.stt.clova_client import TranscriptResult, WordTiming

# --- 튜닝 가능한 임계값 ---
# AI_설계.md §2/§5: 전부 실측 예상치. 실제 라벨링 데이터(사람이 직접 필러 위치
# 표시)로 정밀도 검증 후 조정 필요 — 남은 이슈로 문서에도 명시되어 있음.

# 이 값 미만인 단어-간 간격은 정상적인 조음 간격으로 보고 VAD 없이 침묵으로만 집계.
# 실측 데이터 기준 자연스러운 간격은 0~150ms, 필러 의심 구간은 500ms 이상이었음
# (AI_설계.md §2-1) — 200~300ms 중간값으로 시작.
GAP_CANDIDATE_THRESHOLD_MS = 250

# 이 값 이상의 침묵 구간(VAD로 "목소리 없음" 확인된 gap)은 long_pauses에 별도 기록.
LONG_PAUSE_THRESHOLD_MS = 1000

# VAD 슬라이스에 앞뒤로 붙이는 패딩(ms). gap 경계를 딱 맞춰 자르면 발화 시작/끝
# 부분이 잘려서 VAD가 놓칠 수 있어 여유를 둔다(§2-2).
VAD_SLICE_PADDING_MS = 100

# 슬라이스 안에서 이만큼 이상 음성이 감지되어야 "목소리 있음"으로 확정.
# 너무 낮으면 순간적인 잡음/숨소리를 필러로 오탐할 수 있음.
VAD_MIN_SPEECH_MS = 60

# Silero VAD 요구사항.
SAMPLE_RATE = 16_000


@dataclass
class _Gap:
    """단어와 단어 사이(또는 오디오 시작/끝과 첫/마지막 단어 사이)의 빈 구간."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@lru_cache(maxsize=1)
def _get_vad_model():
    """Silero VAD 모델을 프로세스당 한 번만 로드한다.

    `pip install silero-vad`로 가중치가 패키지에 포함되어 배포되므로 별도로
    GitHub를 클론하거나 파일을 내려받을 필요가 없다 — requirements.txt에
    silero-vad(+torch)만 추가하면 이 함수가 알아서 로드한다.
    """
    from silero_vad import load_silero_vad

    return load_silero_vad()


def _load_audio_16k_mono(audio_path: Path) -> np.ndarray:
    """오디오를 Silero VAD 요구사항(16kHz, 모노, float32)에 맞춰 로드한다.

    m4a/webm 등 압축 포맷 디코딩은 librosa가 내부적으로 audioread/ffmpeg를
    사용하므로, 실행 환경에 ffmpeg 바이너리가 설치되어 있어야 한다.
    """
    import librosa

    wav, _ = librosa.load(str(audio_path), sr=SAMPLE_RATE, mono=True)
    return wav.astype(np.float32)


def compute_word_gaps(words: list[WordTiming], audio_duration_ms: int) -> list[_Gap]:
    """단어 사이(+ 오디오 시작/끝) 간격을 전부 나열한다.

    여기서는 필터링하지 않고 다 나열만 한다 — 임계값 판단은 classify_gaps에서.
    words는 CLOVA 응답 순서를 신뢰하지 않고 start_ms 기준으로 재정렬한다.
    """
    if audio_duration_ms <= 0:
        return []
    if not words:
        return [_Gap(0, audio_duration_ms)]

    ordered = sorted(words, key=lambda w: w.start_ms)
    gaps: list[_Gap] = []

    if ordered[0].start_ms > 0:
        gaps.append(_Gap(0, ordered[0].start_ms))

    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start_ms > prev.end_ms:
            gaps.append(_Gap(prev.end_ms, nxt.start_ms))

    last = ordered[-1]
    if audio_duration_ms > last.end_ms:
        gaps.append(_Gap(last.end_ms, audio_duration_ms))

    return [g for g in gaps if g.duration_ms > 0]


def _is_voiced(wav: np.ndarray, gap: _Gap) -> bool:
    """gap 구간(+패딩)만 오디오에서 잘라 Silero VAD로 목소리 유무를 확인한다."""
    import torch
    from silero_vad import get_speech_timestamps

    pad = VAD_SLICE_PADDING_MS
    start_sample = max(0, int((gap.start_ms - pad) * SAMPLE_RATE / 1000))
    end_sample = min(len(wav), int((gap.end_ms + pad) * SAMPLE_RATE / 1000))
    if end_sample <= start_sample:
        return False

    slice_tensor = torch.from_numpy(wav[start_sample:end_sample])
    speech_spans = get_speech_timestamps(
        slice_tensor,
        _get_vad_model(),
        sampling_rate=SAMPLE_RATE,
        return_seconds=False,
    )
    total_speech_samples = sum(span["end"] - span["start"] for span in speech_spans)
    total_speech_ms = total_speech_samples * 1000 / SAMPLE_RATE
    return total_speech_ms >= VAD_MIN_SPEECH_MS


def classify_gaps(
    wav: np.ndarray, gaps: list[_Gap]
) -> tuple[list[Filler], int, list[LongPause]]:
    """gap들을 순회하며 그룹A 필러 후보 / 침묵(+장시간 정지)으로 나눈다.

    Returns:
        (fillers, silence_total_ms, long_pauses)
    """
    fillers: list[Filler] = []
    long_pauses: list[LongPause] = []
    silence_total_ms = 0

    for gap in gaps:
        if gap.duration_ms < GAP_CANDIDATE_THRESHOLD_MS:
            # 정상적인 단어 간 간격 — VAD를 돌릴 정도로 의심스럽지 않음, 침묵으로만 집계.
            silence_total_ms += gap.duration_ms
            continue

        if _is_voiced(wav, gap):
            # CLOVA가 텍스트로 남기지 않은 발성 = 그룹A 필러 후보.
            # 정확히 "음"인지 "어"인지는 CLOVA 원문이 없어 구분할 수 없으므로
            # 기타(ETC)로 잡고 text에 표시를 남긴다.
            # (AI_설계.md §2 "미정" 항목 잠정 처리 — placeholder 포맷 확정되면 교체)
            fillers.append(
                Filler(
                    type=FillerType.ETC,
                    text="[VAD 감지 · 원문 미상]",
                    at_ms=gap.start_ms,
                    duration_ms=gap.duration_ms,
                )
            )
        else:
            silence_total_ms += gap.duration_ms
            if gap.duration_ms >= LONG_PAUSE_THRESHOLD_MS:
                long_pauses.append(LongPause(start_ms=gap.start_ms, duration_ms=gap.duration_ms))

    return fillers, silence_total_ms, long_pauses


def compute_wpm(word_count: int, audio_duration_ms: int) -> float:
    """분당 단어 수. word_count는 CLOVA words 배열 길이(어절 단위) 기준."""
    if audio_duration_ms <= 0:
        return 0.0
    minutes = audio_duration_ms / 60_000
    return round(word_count / minutes, 1) if minutes > 0 else 0.0


def compute_volume_variation(wav: np.ndarray, frame_ms: int = 200) -> VolumeVariation:
    """RMS(진폭) 기반 성량 변화. VAD/CLOVA 결과와 무관하게 오디오 자체로만 계산.

    frame_ms 단위로 잘라 프레임별 RMS를 구하고, 변동계수(상대표준편차 = std/mean)를
    지표로 쓴다. 값이 클수록 성량 기복이 크다는 뜻이지만, "적당한" 절대 기준값은
    아직 없음 — 실데이터 몇 건 돌려보고 감을 잡아야 함(§5 남은 이슈와 별개로 추가된
    TODO).
    """
    frame_len = int(SAMPLE_RATE * frame_ms / 1000)
    if frame_len <= 0 or len(wav) < frame_len:
        return VolumeVariation(relative_std=0.0, note="오디오가 너무 짧아 계산 불가")

    n_frames = len(wav) // frame_len
    trimmed = wav[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(trimmed**2, axis=1))
    mean_rms = float(np.mean(rms))
    if mean_rms == 0:
        return VolumeVariation(relative_std=0.0, note="오디오 전체가 무음으로 감지됨")

    relative_std = float(np.std(rms) / mean_rms)
    note = (
        f"{frame_ms}ms 프레임 RMS 기준 변동계수 {relative_std:.2f}"
        " (값이 클수록 성량 기복이 큼 · 절대 기준값은 실데이터로 보정 필요)"
    )
    return VolumeVariation(relative_std=round(relative_std, 3), note=note)


def compute_delivery_metrics(
    transcript: TranscriptResult, audio_path: Path, audio_duration_ms: int
) -> Delivery:
    """WPM/침묵/그룹A 채움말/성량변화를 계산해 Delivery 모델로 반환한다.

    그룹B 채움말("그"/"저"/"뭐" 등, 문맥 판단 필요)은 여기서 다루지 않는다 —
    app/llm/gateway_client.py에서 정합성 판정과 같이 처리한 뒤 파이프라인
    (run_analysis.py)에서 delivery.fillers에 병합한다.
    """
    wav = _load_audio_16k_mono(audio_path)
    gaps = compute_word_gaps(transcript.words, audio_duration_ms)
    fillers, silence_total_ms, long_pauses = classify_gaps(wav, gaps)

    return Delivery(
        wpm=compute_wpm(len(transcript.words), audio_duration_ms),
        silence_total_ms=silence_total_ms,
        long_pauses=long_pauses,
        fillers=fillers,
        filler_count=len(fillers),
        volume_variation=compute_volume_variation(wav),
    )
