"""Drift guard for the committed ``report.json`` schema.

Mirrors ``tests/test_bundle_schema.py``, and for the same reason: ``report.json``
is the published shape a control plane (or any non-Python consumer) reads, so it
cannot be allowed to drift from the models that emit it.
"""

from pathlib import Path

import pytest

from steppy.contracts.report import (
    ACCEPTED_REPORT_VERSIONS,
    REPORT_SCHEMA_VERSION,
    ReportModel,
    ReportSourceInfo,
    accepts,
    generate_report_schema,
    parse_report,
    report_bytes,
    schema_json,
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "report.schema.json"

_REGEN_HINT = (
    "contracts/report.schema.json is out of sync with steppy.contracts.report. Regenerate it:\n"
    '  python -c "from steppy.contracts.report import schema_json; '
    "open('contracts/report.schema.json','w',encoding='utf-8',newline='\\n').write(schema_json())\""
)

_MINIMAL = {
    "steppy_report": 1,
    "source": {"kind": "tree", "label": "output"},
    "run": {"run_id": "r1", "created_at": "2026-08-14T10:00:00Z", "steppy_version": "0.3.0"},
}


def test_committed_schema_matches_the_models():
    assert _SCHEMA_PATH.is_file(), f"missing {_SCHEMA_PATH}"
    assert _SCHEMA_PATH.read_text(encoding="utf-8") == schema_json(), _REGEN_HINT


def test_schema_advertises_its_version_and_what_it_accepts():
    schema = generate_report_schema()
    assert schema["x-steppy-report-version"] == REPORT_SCHEMA_VERSION
    assert schema["x-steppy-report-accepted"] == list(ACCEPTED_REPORT_VERSIONS)


def test_unknown_fields_survive_a_round_trip():
    """The inverse of the config models' ``extra="forbid"``, deliberately: a newer
    producer's extra field must reach an older consumer, not crash it."""
    payload = dict(_MINIMAL)
    payload["future_top_level"] = {"anything": 1}
    payload["totals"] = {"tests": 1, "future_total": 9}
    model = parse_report(payload)
    dumped = model.model_dump(mode="json")
    assert dumped["future_top_level"] == {"anything": 1}
    assert dumped["totals"]["future_total"] == 9


def test_unknown_outcome_and_status_values_are_data_not_errors():
    payload = dict(_MINIMAL)
    payload["totals"] = {"verdict": "vanished"}
    payload["scenarios"] = [
        {
            "slug": "s",
            "outcome": "vanished",
            "variants": [
                {"nodeid": "n", "outcome": "vanished", "steps": [{"step": 1, "status": "quantum"}]},
            ],
        }
    ]
    model = parse_report(payload)
    assert model.totals.verdict == "vanished"
    assert model.scenarios[0].variants[0].steps[0].status == "quantum"


@pytest.mark.parametrize("version,match", [(2, "not accepted"), ("1", "missing the integer")])
def test_unsupported_versions_are_rejected_with_an_actionable_message(version, match):
    payload = dict(_MINIMAL)
    payload["steppy_report"] = version
    with pytest.raises(ValueError, match=match):
        parse_report(payload)


def test_a_minimal_report_round_trips():
    model = parse_report(dict(_MINIMAL))
    assert model.steppy_report == REPORT_SCHEMA_VERSION
    assert model.run.run_id == "r1"
    assert model.scenarios == []
    assert model.coverage.tier == "none"
    assert accepts(REPORT_SCHEMA_VERSION)


def test_required_fields_are_not_quietly_widened():
    """``run`` and ``source`` are what make a report attributable. Widening either
    to optional would let a report exist that cannot say which run it describes —
    the same class of mistake that once made ``BundleTest.scenario`` nullable and
    got a whole bundle refused downstream."""
    schema = generate_report_schema()
    assert set(schema["required"]) == {"steppy_report", "source", "run"}


def test_report_bytes_keeps_nulls_and_is_deterministic():
    """``failed_step: null`` is a real answer — nothing was open when the test died
    — and dropping the key would make it indistinguishable from a producer that
    never recorded steps."""
    model = ReportModel(steppy_report=1, source=ReportSourceInfo(kind="tree"), run=_MINIMAL["run"])
    first = report_bytes(model)
    assert first == report_bytes(model)
    assert first.endswith(b"\n")
    assert b'"failed_step"' not in first  # no variants at all here
    text = report_bytes(parse_report(dict(_MINIMAL, scenarios=[{"slug": "s", "variants": [{"nodeid": "n"}]}])))
    assert b'"failed_step": null' in text


def test_report_json_carries_no_render_time_clock():
    """A generated-at stamp would make two renders of one bundle differ, which
    defeats diffing a report in CI. The run's own created_at is the timestamp."""
    schema = generate_report_schema()
    assert "generated_at" not in schema["properties"]
