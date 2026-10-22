"""Drift guard for the committed results-bundle JSON Schema.

Mirrors ``tests/test_config_schema.py``. ``contracts/bundle-manifest.schema.json``
is the published shape of ``manifest.json`` for consumers that are not Python —
the control plane's ingester among them. If a contract model changes without
regenerating the file, the published schema quietly stops describing what
``steppy bundle create`` actually emits, which is the one failure mode a
versioned wire format exists to prevent.
"""

from pathlib import Path

from steppy.contracts.bundle import (
    ACCEPTED_BUNDLE_VERSIONS,
    BUNDLE_SCHEMA_VERSION,
    BundleFile,
    BundleManifest,
    BundleRun,
    BundleTest,
    generate_bundle_schema,
    parse_manifest,
    schema_json,
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "bundle-manifest.schema.json"

_REGEN_HINT = (
    "contracts/bundle-manifest.schema.json is out of sync with the contract models. Regenerate it:\n"
    '  python -c "from steppy.contracts.bundle import schema_json; '
    "open('contracts/bundle-manifest.schema.json','w',encoding='utf-8',newline='\\n').write(schema_json())\""
)


def test_committed_schema_matches_the_models():
    assert _SCHEMA_PATH.is_file(), f"missing {_SCHEMA_PATH}"
    assert _SCHEMA_PATH.read_text(encoding="utf-8") == schema_json(), _REGEN_HINT


def test_schema_advertises_the_accepted_versions():
    schema = generate_bundle_schema()
    assert schema["x-steppy-bundle-version"] == BUNDLE_SCHEMA_VERSION
    assert schema["x-steppy-bundle-accepted"] == list(ACCEPTED_BUNDLE_VERSIONS)


def test_wire_models_allow_unknown_fields():
    """The inverse of the config models' ``extra="forbid"``, deliberately: a
    newer producer's extra field must survive an older consumer, not crash it."""
    manifest = parse_manifest(
        {
            "steppy_bundle": 1,
            "run": {
                "run_id": "r",
                "created_at": "t",
                "steppy_version": "0.3.0",
                "some_future_field": {"nested": True},
            },
            "files": [{"path": "p", "kind": "a-kind-from-the-future", "sha256": "x", "size": 1, "novel": 2}],
            "another_future_top_level_key": [1, 2, 3],
        }
    )
    assert manifest.run.some_future_field == {"nested": True}
    assert manifest.files[0].kind == "a-kind-from-the-future"
    assert manifest.another_future_top_level_key == [1, 2, 3]


def test_unsupported_version_is_rejected_with_an_actionable_message():
    import pytest

    with pytest.raises(ValueError, match="not accepted"):
        parse_manifest({"steppy_bundle": 99, "run": {"run_id": "r", "created_at": "t", "steppy_version": "0"}})

    with pytest.raises(ValueError, match="missing the integer"):
        parse_manifest({"run": {"run_id": "r", "created_at": "t", "steppy_version": "0"}})


def test_minimal_manifest_round_trips():
    manifest = BundleManifest(
        steppy_bundle=BUNDLE_SCHEMA_VERSION,
        run={"run_id": "r", "created_at": "t", "steppy_version": "0.3.0"},
    )
    assert parse_manifest(manifest.model_dump(mode="json")).run.run_id == "r"


def test_required_fields_are_not_quietly_widened():
    """The fields a consumer may rely on being present.

    Widening one of these to Optional is invisible here and fatal downstream: an
    older consumer types it as required and rejects the entire manifest, not
    just the row. That is precisely how ``BundleTest.scenario`` broke — the
    producer emitted null for JUnit-only rows and the control plane refused the
    bundle. Loosening any of these is a contract-version decision, not a
    convenience."""
    import pytest

    with pytest.raises(ValueError):
        BundleTest(nodeid="n", outcome="passed")  # missing scenario
    with pytest.raises(ValueError):
        BundleTest(scenario="s", outcome="passed")  # missing nodeid
    with pytest.raises(ValueError):
        BundleFile(path="p", kind="k", sha256="x")  # missing size
    with pytest.raises(ValueError):
        BundleRun(run_id="r", created_at="t")  # missing steppy_version
