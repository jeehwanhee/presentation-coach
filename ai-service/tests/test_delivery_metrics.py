"""app/audio/delivery_metrics.py 단위 테스트.

VAD(Silero)/오디오 디코딩(librosa)이 필요한 부분(_get_vad_model, _is_voiced,
_load_audio_16k_mono)은 무거운 의존성(torch/librosa)이 실제 개발 환경에서만
설치되므로, classify_gaps 테스트에서는 monkeypatch로 _is_voiced를 대체해
"VAD가 이렇게 판단했다면 결과가 맞는가"만 검증한다. 실제 VAD 정확도는
AI_설계.md §5에 남겨둔 대로 라벨링 데이터로 별도 검증 필요.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.audio import delivery_metrics as dm
from app.schemas.report import FillerType
from app.stt.clova_client import WordTiming


def _words(*pairs: tuple[int, int]) -> list[WordTiming]:
    return [WordTiming(text="x", start_ms=s, end_ms=e) for s, e in pairs]


class TestComputeWordGaps:
    def test_no_words_returns_single_full_gap(self):
        gaps = dm.compute_word_gaps([], audio_duration_ms=5000)
        assert [(g.start_ms, g.end_ms) for g in gaps] == [(0, 5000)]

    def test_zero_duration_audio_returns_nothing(self):
        assert dm.compute_word_gaps([], audio_duration_ms=0) == []

    def test_leading_trailing_and_inter_word_gaps(self):
        # 0~100 무음, 100~300 단어, 300~900 gap, 900~1000 단어, 1000~1200 무음(끝)
        words = _words((100, 300), (900, 1000))
        gaps = dm.compute_word_gaps(words, audio_duration_ms=1200)
        assert [(g.start_ms, g.end_ms) for g in gaps] == [
            (0, 100),
            (300, 900),
            (1000, 1200),
        ]

    def test_no_gap_when_words_are_back_to_back(self):
        words = _words((0, 500), (500, 1000))
        gaps = dm.compute_word_gaps(words, audio_duration_ms=1000)
        assert gaps == []

    def test_unordered_words_are_sorted_first(self):
        words = _words((900, 1000), (100, 300))
        gaps = dm.compute_word_gaps(words, audio_duration_ms=1200)
        assert [(g.start_ms, g.end_ms) for g in gaps] == [
            (0, 100),
            (300, 900),
            (1000, 1200),
        ]


class TestClassifyGaps:
    def test_short_gap_below_threshold_is_silence_only_no_vad_call(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("임계값 미만 gap은 VAD를 호출하면 안 됨")

        monkeypatch.setattr(dm, "_is_voiced", _boom)
        gaps = [dm._Gap(0, dm.GAP_CANDIDATE_THRESHOLD_MS - 1)]
        fillers, silence_total_ms, long_pauses = dm.classify_gaps(np.zeros(1), gaps)

        assert fillers == []
        assert long_pauses == []
        assert silence_total_ms == dm.GAP_CANDIDATE_THRESHOLD_MS - 1

    def test_long_voiced_gap_becomes_filler_candidate(self, monkeypatch):
        monkeypatch.setattr(dm, "_is_voiced", lambda wav, gap: True)
        gap_duration = dm.GAP_CANDIDATE_THRESHOLD_MS + 50
        gaps = [dm._Gap(1000, 1000 + gap_duration)]

        fillers, silence_total_ms, long_pauses = dm.classify_gaps(np.zeros(1), gaps)

        assert silence_total_ms == 0
        assert long_pauses == []
        assert len(fillers) == 1
        f = fillers[0]
        assert f.type == FillerType.ETC
        assert f.at_ms == 1000
        assert f.duration_ms == gap_duration
        assert f.text  # 원문 미상이라도 필수 필드는 채워야 함

    def test_long_silent_gap_is_silence_and_long_pause(self, monkeypatch):
        monkeypatch.setattr(dm, "_is_voiced", lambda wav, gap: False)
        gaps = [dm._Gap(0, dm.LONG_PAUSE_THRESHOLD_MS)]

        fillers, silence_total_ms, long_pauses = dm.classify_gaps(np.zeros(1), gaps)

        assert fillers == []
        assert silence_total_ms == dm.LONG_PAUSE_THRESHOLD_MS
        assert len(long_pauses) == 1
        assert long_pauses[0].duration_ms == dm.LONG_PAUSE_THRESHOLD_MS

    def test_candidate_but_short_silent_gap_is_silence_without_long_pause(self, monkeypatch):
        monkeypatch.setattr(dm, "_is_voiced", lambda wav, gap: False)
        gap_duration = dm.GAP_CANDIDATE_THRESHOLD_MS + 10
        gaps = [dm._Gap(0, gap_duration)]

        fillers, silence_total_ms, long_pauses = dm.classify_gaps(np.zeros(1), gaps)

        assert fillers == []
        assert silence_total_ms == gap_duration
        assert long_pauses == []


class TestComputeWpm:
    def test_basic(self):
        # 60초에 단어 150개 -> 분당 150
        assert dm.compute_wpm(word_count=150, audio_duration_ms=60_000) == 150.0

    def test_zero_duration_is_zero(self):
        assert dm.compute_wpm(word_count=10, audio_duration_ms=0) == 0.0


class TestComputeVolumeVariation:
    def test_too_short_audio(self):
        wav = np.zeros(10, dtype=np.float32)
        result = dm.compute_volume_variation(wav, frame_ms=200)
        assert result.relative_std == 0.0

    def test_silence_is_zero_variation(self):
        wav = np.zeros(dm.SAMPLE_RATE * 2, dtype=np.float32)
        result = dm.compute_volume_variation(wav, frame_ms=200)
        assert result.relative_std == 0.0

    def test_varying_amplitude_has_positive_variation(self):
        # 앞 1초는 작은 진폭, 뒤 1초는 큰 진폭인 사인파 -> 변동계수가 0보다 커야 함
        t = np.linspace(0, 1, dm.SAMPLE_RATE, endpoint=False)
        quiet = 0.01 * np.sin(2 * np.pi * 200 * t)
        loud = 0.9 * np.sin(2 * np.pi * 200 * t)
        wav = np.concatenate([quiet, loud]).astype(np.float32)

        result = dm.compute_volume_variation(wav, frame_ms=200)

        assert result.relative_std > 0.5
        assert result.note

    @pytest.mark.parametrize("frame_ms", [50, 200, 500])
    def test_various_frame_sizes_do_not_crash(self, frame_ms):
        wav = np.random.default_rng(0).normal(0, 0.1, dm.SAMPLE_RATE * 3).astype(np.float32)
        result = dm.compute_volume_variation(wav, frame_ms=frame_ms)
        assert result.relative_std >= 0.0
