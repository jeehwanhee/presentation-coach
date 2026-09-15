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
# (AI_설계.md §2-1) — 200~300ms 중간값으로 시작했으나, 2026-09-15 실배포 리포트에서
# 채움말이 비정상적으로 많이(19회) 잡히는 과탐이 확인되어 350ms로 상향.
# (2026-09-15 추가 확인) 이후 실배포 리포트(id=50, 대본 일치율 100%인 "잘한" 버전)에서
# 여전히 14건이 잡혔는데, 14건 전부 gap 길이가 370~650ms로 이미 350ms를 넘는 값들이라
# 이 임계값을 더 올려도 구조적으로 걸러지지 않음 — 사용자가 직접 확인한 결과 14건
# 전부 호흡이었음(실제 "음"/"어" 발화 0건). 즉 과탐의 원인은 이 임계값이 아니라
# VAD "목소리 있음" 판정 쪽(VAD_SLICE_PADDING_MS/VAD_SPEECH_PROB_THRESHOLD)이라는
# 게 실측으로 확정됨 — 이 값을 추가로 올리는 시도는 하지 않음.
GAP_CANDIDATE_THRESHOLD_MS = 350

# 이 값 이상의 침묵 구간(VAD로 "목소리 없음" 확인된 gap)은 long_pauses에 별도 기록.
LONG_PAUSE_THRESHOLD_MS = 3000

# VAD 슬라이스에 앞뒤로 붙이는 패딩(ms). gap 경계를 딱 맞춰 자르면 발화 시작/끝
# 부분이 잘려서 VAD가 놓칠 수 있어 여유를 둔다(§2-2). 기존 100ms는 인접 단어의
# 말꼬리 음절까지 슬라이스에 끌어들여 speech duration을 부풀리는 것으로 의심됨
# (2026-09-15, id=50 리포트에서 필러 14건 전부 호흡으로 확인된 뒤 낮춤) — 단어
# 경계를 완전히 놓치지 않을 최소한만 남기고 40ms로 축소. 미검증 — 재실행 필요.
VAD_SLICE_PADDING_MS = 40

# 슬라이스 안에서 이만큼 이상 음성이 감지되어야 "목소리 있음"으로 확정.
# 너무 낮으면 순간적인 잡음/숨소리를 필러로 오탐할 수 있음. 기존 60ms는 VAD_SLICE_
# PADDING_MS(100ms)로 앞뒤 단어의 말꼬리/날숨이 슬라이스에 섞여 들어왔을 때도 쉽게
# 넘는 값이라 과탐 원인으로 의심됨 — 2026-09-15 120ms로 상향.
# (2026-09-15 추가 확인) 그래도 여전히 호흡을 필러로 오탐(14건/14건) — duration
# 기준만으로는 호흡과 짧은 발화를 못 가른다고 보고, VAD_SPEECH_PROB_THRESHOLD를
# 별도로 추가함(아래). 이 값 자체는 일단 유지.
VAD_MIN_SPEECH_MS = 120

# Silero VAD가 "음성"이라고 판단하는 프레임 확률 컷오프(get_speech_timestamps의
# threshold 파라미터, 기본값 0.5). 기본값을 쓰면 호흡처럼 발화보다 확신도가 낮은
# 소리도 "음성"으로 잡힐 수 있어 보수적으로 올림(2026-09-15, id=50 리포트의 필러
# 14건이 전부 호흡으로 확인된 뒤 추가) — 0.65는 첫 시도값.
# (2026-09-15 추가 확인) VAD_SLICE_PADDING_MS 40ms + 이 값 0.65 조합으로 id=52
# 재실행 결과 14건 → 7건으로 절반은 줄었으나(효과는 있었음), 남은 7건도 사용자가
# 직접 들어보고 전부 호흡으로 확인 — Silero의 "음성 확률"만으로는 호흡과 유성
# 필러("음"/"어")를 완전히 못 가른다는 게 재확인됨. 그래서 아래 pitch 기반 2차
# 검사(_gap_has_voiced_pitch)를 추가하고, 이 값 자체는 더 안 올림(계속 올리면
# 진짜 필러까지 놓칠 위험만 커지고 근본 해결이 안 됨).
VAD_SPEECH_PROB_THRESHOLD = 0.65

# --- 유성음(pitch) 2차 검사 (2026-09-15 추가) ---
# 호흡은 에너지가 있어도 배음 구조(pitch)가 없는 노이즈인 반면, "음"/"어" 같은
# 유성 필러는 모음처럼 일정 구간 이상 안정된 pitch를 가진다. Silero VAD 확률
# 임계값만 올려서는(위 VAD_SPEECH_PROB_THRESHOLD 이력 참고) 호흡을 못 걸러내는
# 게 실측(id=50: 14/14 호흡, id=52: 잔여 7/7도 호흡)으로 두 번 확인돼서, gap
# 구간에 이 정도 이상 안정적인 pitch가 잡혀야만 필러로 확정하도록 검사를 더한다.
# 사람 육성 기본 주파수(F0) 범위를 넉넉하게 잡음.
VOICED_PITCH_MIN_HZ = 75
VOICED_PITCH_MAX_HZ = 400
# 이 이상 pitch가 검출돼야 "발성 있음"으로 인정 — 미검증 초기값, 실제 필러가
# 있는 샘플로 재검증 필요(너무 높이면 짧은 필러를 놓칠 수 있음).
VOICED_PITCH_MIN_MS = 80
# librosa.pyin 프레임/홉 크기 — 기본값(frame_length=2048)은 350ms 근처의 짧은
# gap 슬라이스엔 시간 해상도가 너무 낮아서 더 작은 값으로 지정.
PITCH_FRAME_LENGTH = 1024
PITCH_HOP_LENGTH = 256

# Silero VAD 요구사항.
SAMPLE_RATE = 16_000


@dataclass
class _Gap:
    """단어와 단어 사이(또는 오디오 시작/끝과 첫/마지막 단어 사이)의 빈 구간."""

    start_ms: int
    end_ms: int
    # True면 오디오 맨 앞(첫 단어 시작 전)/맨 뒤(마지막 단어 끝난 후) gap.
    # 마이크 세팅·녹음 종료 지연 같은 녹음 아티팩트일 뿐 발표 중 침묵이 아니므로
    # classify_gaps에서 silence_total_ms/long_pauses 집계 대상에서 제외한다.
    is_edge: bool = False

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
        return [_Gap(0, audio_duration_ms, is_edge=True)]

    ordered = sorted(words, key=lambda w: w.start_ms)
    gaps: list[_Gap] = []

    if ordered[0].start_ms > 0:
        gaps.append(_Gap(0, ordered[0].start_ms, is_edge=True))

    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start_ms > prev.end_ms:
            gaps.append(_Gap(prev.end_ms, nxt.start_ms))

    last = ordered[-1]
    if audio_duration_ms > last.end_ms:
        gaps.append(_Gap(last.end_ms, audio_duration_ms, is_edge=True))

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
        threshold=VAD_SPEECH_PROB_THRESHOLD,
        return_seconds=False,
    )
    total_speech_samples = sum(span["end"] - span["start"] for span in speech_spans)
    total_speech_ms = total_speech_samples * 1000 / SAMPLE_RATE
    return total_speech_ms >= VAD_MIN_SPEECH_MS


def _gap_has_voiced_pitch(wav: np.ndarray, gap: _Gap) -> bool:
    """gap 구간(패딩 없이 순수 gap만)에 안정적인 pitch(유성음)가 있는지 확인한다.

    _is_voiced()는 패딩까지 포함한 슬라이스에 Silero 기준 "음성 에너지"가
    있는지만 보므로 호흡도 자주 통과한다(2026-09-15 실측, 위 상수 이력 참고).
    이 함수는 그 2차 검사로, gap 구간 자체에 사람 목소리다운 pitch가 실제로
    존재하는지 librosa.pyin으로 확인한다 — 호흡은 배음 구조가 없어 pitch가
    거의 안 잡히고, "음"/"어" 같은 유성 필러는 모음처럼 pitch가 잡힌다는
    가정.
    """
    import librosa

    start_sample = max(0, int(gap.start_ms * SAMPLE_RATE / 1000))
    end_sample = min(len(wav), int(gap.end_ms * SAMPLE_RATE / 1000))
    segment = wav[start_sample:end_sample]

    # librosa.pyin은 frame_length보다 짧은 입력에 에러를 내므로, 너무 짧으면
    # pitch 판단을 못 하는 셈 — 이 경우 보수적으로 "필러 후보 유지"(True)로 둔다.
    if len(segment) < PITCH_FRAME_LENGTH:
        return True

    _f0, voiced_flag, _voiced_prob = librosa.pyin(
        segment,
        fmin=VOICED_PITCH_MIN_HZ,
        fmax=VOICED_PITCH_MAX_HZ,
        sr=SAMPLE_RATE,
        frame_length=PITCH_FRAME_LENGTH,
        hop_length=PITCH_HOP_LENGTH,
    )
    if voiced_flag is None or len(voiced_flag) == 0:
        return False

    frame_ms = PITCH_HOP_LENGTH * 1000 / SAMPLE_RATE
    voiced_ms = float(np.count_nonzero(voiced_flag)) * frame_ms
    return voiced_ms >= VOICED_PITCH_MIN_MS


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
        if gap.is_edge:
            # 녹음 시작 전(마이크 세팅 등)/종료 후 무음 — 발표 중 침묵이 아니므로
            # 필러 판정(VAD)도 돌리지 않고 silence_total_ms/long_pauses에서 제외.
            continue

        if gap.duration_ms < GAP_CANDIDATE_THRESHOLD_MS:
            # 정상적인 단어 간 간격 — VAD를 돌릴 정도로 의심스럽지 않음, 침묵으로만 집계.
            silence_total_ms += gap.duration_ms
            continue

        # Silero "음성 에너지 있음"(_is_voiced) + gap 자체에 "pitch 있음"
        # (_gap_has_voiced_pitch) 둘 다 만족해야 필러로 확정 — 전자만으로는
        # 호흡도 통과하는 게 실측으로 확인돼서(2026-09-15) 2차 검사를 추가함.
        if _is_voiced(wav, gap) and _gap_has_voiced_pitch(wav, gap):
            # CLOVA가 텍스트로 남기지 않은 발성 = 그룹A 필러 후보.
            # 정확히 "음"인지 "어"인지는 CLOVA 원문이 없어 구분할 수 없으므로
            # 기타(ETC)로 잡고 text에 표시를 남긴다.
            # (AI_설계.md §2 "미정" 항목 잠정 처리 — placeholder 포맷 확정되면 교체)
            fillers.append(
                Filler(
                    type=FillerType.ETC,
                    # (2026-09-15 임시) 워커가 실제로 이 코드(pitch 2차 검사 포함)로
                    # 돌고 있는지 리포트에서 바로 눈으로 확인하기 위한 카나리 표시.
                    # r/52~54가 pitch 코드 추가 전후로 결과가 완전히 동일해서
                    # (같은 at_ms/duration_ms 7개) 워커가 새 코드를 실제로 읽고
                    # 있는지 의심돼 추가함 — 검증되면 원래 텍스트로 되돌릴 것.
                    text="[VAD+Pitch v2 감지 · 원문 미상]",
                    at_ms=gap.start_ms,
                    duration_ms=gap.duration_ms,
                )
            )
        else:
            silence_total_ms += gap.duration_ms
            if gap.duration_ms >= LONG_PAUSE_THRESHOLD_MS:
                long_pauses.append(LongPause(start_ms=gap.start_ms, duration_ms=gap.duration_ms))

    return fillers, silence_total_ms, long_pauses


def compute_speaking_window_ms(words: list[WordTiming], audio_duration_ms: int) -> int:
    """실제 발화 구간 길이(첫 단어 시작 ~ 마지막 단어 끝).

    녹음 전후 무음(마이크 세팅 등)까지 분모에 넣으면 WPM이 실제보다 낮게
    나오므로, WPM은 오디오 전체 길이가 아니라 이 구간 기준으로 계산한다.
    """
    if not words:
        return audio_duration_ms
    ordered = sorted(words, key=lambda w: w.start_ms)
    return ordered[-1].end_ms - ordered[0].start_ms


def compute_wpm(word_count: int, speaking_window_ms: int) -> float:
    """분당 단어 수. word_count는 CLOVA words 배열 길이(어절 단위) 기준.

    speaking_window_ms는 오디오 전체 길이가 아니라 실제 발화 구간 길이
    (compute_speaking_window_ms 참고) — 녹음 전후 무음에 왜곡되지 않도록.
    """
    if speaking_window_ms <= 0:
        return 0.0
    minutes = speaking_window_ms / 60_000
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
    speaking_window_ms = compute_speaking_window_ms(transcript.words, audio_duration_ms)

    return Delivery(
        wpm=compute_wpm(len(transcript.words), speaking_window_ms),
        silence_total_ms=silence_total_ms,
        long_pauses=long_pauses,
        fillers=fillers,
        filler_count=len(fillers),
        volume_variation=compute_volume_variation(wav),
    )
