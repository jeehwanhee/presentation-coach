"""app/pipeline/run_analysis.py 단위 테스트.

4개 하위 단계(pptx_parser/clova_client/delivery_metrics/gateway_client)는 전부
monkeypatch로 가짜 함수로 대체한다 — 여기서 검증하려는 건 그 단계들 자체의
정확도가 아니라 (1) 조립 로직(그룹A+그룹B 필러 병합, transcript 필드 매핑),
(2) 실패 시 예외가 그대로 전파되는지, (3) 실제로 병렬 실행되는지(타이밍) 세 가지.
"""

from __future__ import annotations

import time

import pytest

from app.llm.gateway_client import ConsistencyAnalysisResult, LlmError
from app.parsing.pptx_parser import PptxParseError, Slide
from app.pipeline import run_analysis as ra
from app.schemas.report import (
    Consistency,
    Delivery,
    Filler,
    FillerType,
    VolumeVariation,
)
from app.schemas.job import TranscriptSegment
from app.stt.clova_client import SttError, TranscriptResult, WordTiming


def _fake_slides() -> list[Slide]:
    return [Slide(slide_index=0, text="슬라이드 0")]


def _fake_transcript() -> TranscriptResult:
    return TranscriptResult(
        full_text="안녕하세요",
        words=[WordTiming(text="안녕하세요", start_ms=0, end_ms=1000)],
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="안녕하세요")],
    )


def _fake_delivery(fillers: list[Filler] | None = None) -> Delivery:
    return Delivery(
        wpm=100.0,
        silence_total_ms=500,
        long_pauses=[],
        fillers=fillers or [],
        filler_count=len(fillers or []),
        volume_variation=VolumeVariation(relative_std=0.1, note="테스트"),
    )


def _fake_llm_result(group_b_fillers: list[Filler] | None = None) -> ConsistencyAnalysisResult:
    return ConsistencyAnalysisResult(
        consistency=Consistency(checks=[], supported_count=0, total_claims=0),
        off_topic=[],
        logic_gaps=[],
        script_diff=None,
        group_b_fillers=group_b_fillers or [],
    )


@pytest.fixture
def basic_input(tmp_path):
    return ra.AnalysisInput(
        pptx_path=tmp_path / "a.pptx",
        audio_path=tmp_path / "a.wav",
        audio_duration_ms=60_000,
        script=None,
    )


class TestAssembly:
    def test_happy_path_merges_group_a_and_group_b_fillers(self, monkeypatch, basic_input):
        group_a = Filler(type=FillerType.ETC, text="[VAD 감지 · 원문 미상]", at_ms=100, duration_ms=300)
        group_b = Filler(type=FillerType.GEU, text="그", at_ms=2000, duration_ms=200)

        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())
        monkeypatch.setattr(
            ra, "compute_delivery_metrics", lambda stt, audio_path, dur: _fake_delivery([group_a])
        )
        monkeypatch.setattr(
            ra, "analyze_consistency", lambda slides, stt, script: _fake_llm_result([group_b])
        )

        result = ra.run_analysis(basic_input)

        assert result.report.delivery.filler_count == 2
        assert result.report.delivery.fillers == [group_a, group_b]

    def test_transcript_output_is_stt_segments(self, monkeypatch, basic_input):
        transcript = _fake_transcript()
        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: transcript)
        monkeypatch.setattr(
            ra, "compute_delivery_metrics", lambda stt, audio_path, dur: _fake_delivery()
        )
        monkeypatch.setattr(
            ra, "analyze_consistency", lambda slides, stt, script: _fake_llm_result()
        )

        result = ra.run_analysis(basic_input)

        assert result.transcript == transcript.segments

    def test_report_fields_come_from_llm_result(self, monkeypatch, basic_input):
        llm_result = _fake_llm_result()
        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())
        monkeypatch.setattr(
            ra, "compute_delivery_metrics", lambda stt, audio_path, dur: _fake_delivery()
        )
        monkeypatch.setattr(
            ra, "analyze_consistency", lambda slides, stt, script: llm_result
        )

        result = ra.run_analysis(basic_input)

        assert result.report.consistency == llm_result.consistency
        assert result.report.off_topic == llm_result.off_topic
        assert result.report.logic_gaps == llm_result.logic_gaps
        assert result.report.script_diff == llm_result.script_diff

    def test_correct_args_passed_to_each_stage(self, monkeypatch, basic_input):
        calls = {}

        def fake_parse_pptx(path):
            calls["pptx_path"] = path
            return _fake_slides()

        def fake_transcribe(path):
            calls["audio_path_stt"] = path
            return _fake_transcript()

        def fake_delivery_wrapper(stt, audio_path, dur):
            calls["audio_path_delivery"] = audio_path
            calls["duration"] = dur
            return _fake_delivery()

        def fake_llm(slides, stt, script):
            calls["script"] = script
            calls["slides"] = slides
            return _fake_llm_result()

        monkeypatch.setattr(ra, "parse_pptx", fake_parse_pptx)
        monkeypatch.setattr(ra, "transcribe", fake_transcribe)
        monkeypatch.setattr(ra, "compute_delivery_metrics", fake_delivery_wrapper)
        monkeypatch.setattr(ra, "analyze_consistency", fake_llm)

        basic_input.script = "대본입니다"
        ra.run_analysis(basic_input)

        assert calls["pptx_path"] == basic_input.pptx_path
        assert calls["audio_path_stt"] == basic_input.audio_path
        assert calls["audio_path_delivery"] == basic_input.audio_path
        assert calls["duration"] == basic_input.audio_duration_ms
        assert calls["script"] == "대본입니다"
        assert calls["slides"] == _fake_slides()


class TestFailurePropagation:
    def test_pptx_parse_error_propagates(self, monkeypatch, basic_input):
        def boom(path):
            raise PptxParseError("깨진 pptx")

        monkeypatch.setattr(ra, "parse_pptx", boom)
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())

        with pytest.raises(PptxParseError):
            ra.run_analysis(basic_input)

    def test_stt_error_propagates(self, monkeypatch, basic_input):
        def boom(path):
            raise SttError("CLOVA 실패")

        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", boom)

        with pytest.raises(SttError):
            ra.run_analysis(basic_input)

    def test_delivery_metrics_error_propagates(self, monkeypatch, basic_input):
        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())

        def boom(stt, audio_path, dur):
            raise RuntimeError("VAD 실패")

        monkeypatch.setattr(ra, "compute_delivery_metrics", boom)
        monkeypatch.setattr(
            ra, "analyze_consistency", lambda slides, stt, script: _fake_llm_result()
        )

        with pytest.raises(RuntimeError):
            ra.run_analysis(basic_input)

    def test_llm_error_propagates(self, monkeypatch, basic_input):
        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())
        monkeypatch.setattr(
            ra, "compute_delivery_metrics", lambda stt, audio_path, dur: _fake_delivery()
        )

        def boom(slides, stt, script):
            raise LlmError("게이트웨이 실패")

        monkeypatch.setattr(ra, "analyze_consistency", boom)

        with pytest.raises(LlmError):
            ra.run_analysis(basic_input)


class TestConcurrency:
    def test_stage_1_runs_pptx_and_stt_in_parallel(self, monkeypatch, basic_input):
        delay = 0.15

        def slow_parse_pptx(path):
            time.sleep(delay)
            return _fake_slides()

        def slow_transcribe(path):
            time.sleep(delay)
            return _fake_transcript()

        monkeypatch.setattr(ra, "parse_pptx", slow_parse_pptx)
        monkeypatch.setattr(ra, "transcribe", slow_transcribe)
        monkeypatch.setattr(
            ra, "compute_delivery_metrics", lambda stt, audio_path, dur: _fake_delivery()
        )
        monkeypatch.setattr(
            ra, "analyze_consistency", lambda slides, stt, script: _fake_llm_result()
        )

        start = time.monotonic()
        ra.run_analysis(basic_input)
        elapsed = time.monotonic() - start

        # 순차 실행이면 2단계 모두 최소 2*delay가 걸림. 병렬이면 1단계는 delay
        # 하나만큼만 걸려야 함 — 넉넉히 1.5*delay를 기준으로 순차 실행이면
        # 확실히 못 넘기는 값으로 잡음(1단계만 최소 2*delay=0.3, 2단계는 즉시).
        assert elapsed < delay * 1.7, f"병렬로 안 돈 것 같음: {elapsed:.3f}s (delay={delay})"

    def test_stage_2_runs_delivery_and_llm_in_parallel(self, monkeypatch, basic_input):
        delay = 0.15

        monkeypatch.setattr(ra, "parse_pptx", lambda path: _fake_slides())
        monkeypatch.setattr(ra, "transcribe", lambda path: _fake_transcript())

        def slow_delivery(stt, audio_path, dur):
            time.sleep(delay)
            return _fake_delivery()

        def slow_llm(slides, stt, script):
            time.sleep(delay)
            return _fake_llm_result()

        monkeypatch.setattr(ra, "compute_delivery_metrics", slow_delivery)
        monkeypatch.setattr(ra, "analyze_consistency", slow_llm)

        start = time.monotonic()
        ra.run_analysis(basic_input)
        elapsed = time.monotonic() - start

        assert elapsed < delay * 1.7, f"병렬로 안 돈 것 같음: {elapsed:.3f}s (delay={delay})"

    def test_all_four_stages_slow_total_time_is_two_delays_not_four(self, monkeypatch, basic_input):
        delay = 0.12

        def slow(*args, **kwargs):
            time.sleep(delay)

        def slow_parse_pptx(path):
            time.sleep(delay)
            return _fake_slides()

        def slow_transcribe(path):
            time.sleep(delay)
            return _fake_transcript()

        def slow_delivery(stt, audio_path, dur):
            time.sleep(delay)
            return _fake_delivery()

        def slow_llm(slides, stt, script):
            time.sleep(delay)
            return _fake_llm_result()

        monkeypatch.setattr(ra, "parse_pptx", slow_parse_pptx)
        monkeypatch.setattr(ra, "transcribe", slow_transcribe)
        monkeypatch.setattr(ra, "compute_delivery_metrics", slow_delivery)
        monkeypatch.setattr(ra, "analyze_consistency", slow_llm)

        start = time.monotonic()
        ra.run_analysis(basic_input)
        elapsed = time.monotonic() - start

        # 완전 순차면 4*delay=0.48s. 설계상 2단계(각 병렬)라 2*delay=0.24s 근처여야 함.
        assert elapsed < delay * 3, f"4단계가 병렬로 안 겹친 것 같음: {elapsed:.3f}s"
