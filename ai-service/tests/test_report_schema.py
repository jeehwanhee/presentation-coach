import json
from pathlib import Path

from app.schemas.report import AnalysisReport

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_report_without_script_parses():
    report = AnalysisReport.model_validate(_load("sample_report_no_script.json"))
    assert report.script_diff is None
    assert report.consistency.supported_count == 7
    assert report.consistency.checks[1].verdict.value == "NOT_MENTIONED"
    assert report.delivery.fillers[1].type.value == "기타"


def test_report_with_script_parses():
    report = AnalysisReport.model_validate(_load("sample_report_with_script.json"))
    assert report.script_diff is not None
    assert report.script_diff.matched_ratio == 0.82
    assert report.script_diff.deviations[0].kind.value == "생략"


def test_empty_arrays_stay_empty_not_none():
    """API_명세서 §2.3.1: 결과 없는 배열 필드는 [] (null 금지)."""
    data = _load("sample_report_no_script.json")
    data["off_topic"] = []
    data["logic_gaps"] = []
    report = AnalysisReport.model_validate(data)
    assert report.off_topic == []
    assert report.logic_gaps == []


def test_dump_keeps_snake_case_field_names():
    """Spring이 SNAKE_CASE로 직렬화하므로(backend application.properties 참고),
    다시 dump했을 때도 필드명이 snake_case 그대로여야 계약이 맞는다."""
    report = AnalysisReport.model_validate(_load("sample_report_no_script.json"))
    dumped = report.model_dump(mode="json")
    assert "supported_count" in dumped["consistency"]
    assert "evidence_at_ms" in dumped["consistency"]["checks"][0]
    assert "silence_total_ms" in dumped["delivery"]
