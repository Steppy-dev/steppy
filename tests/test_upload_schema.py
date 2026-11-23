"""Drift guard for the committed upload-protocol schema.

Mirrors ``tests/test_bundle_schema.py`` and ``tests/test_report_schema.py``.
``contracts/upload.schema.json`` is what a server in another language (or one
mirroring the models, as Steppy Cloud does) builds against, so it cannot drift
from the models the client sends and reads.
"""

from pathlib import Path

from steppy.contracts.upload import (
    API_VERSION,
    REPORT_KIND,
    REPORT_PATH,
    CreateRunResponse,
    ErrorBody,
    FinalizeResponse,
    KeyResponse,
    generate_upload_schema,
    schema_json,
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "upload.schema.json"

_REGEN_HINT = (
    "contracts/upload.schema.json is out of sync with steppy.contracts.upload. Regenerate it:\n"
    '  python -c "from steppy.contracts.upload import schema_json; '
    "open('contracts/upload.schema.json','w',encoding='utf-8',newline='\\n').write(schema_json())\""
)


def test_committed_schema_matches_the_models():
    assert _SCHEMA_PATH.is_file(), f"missing {_SCHEMA_PATH}"
    assert _SCHEMA_PATH.read_text(encoding="utf-8") == schema_json(), _REGEN_HINT


def test_schema_names_every_call_and_its_models():
    schema = generate_upload_schema()
    assert schema["x-steppy-api-version"] == API_VERSION == "v1"
    endpoints = schema["x-steppy-endpoints"]
    assert endpoints["GET /v1/key"]["response"] == "#/$defs/KeyResponse"
    assert endpoints["POST /v1/runs"]["request"] == "#/$defs/CreateRunRequest"
    assert endpoints["POST /v1/runs"]["response"] == "#/$defs/CreateRunResponse"
    assert endpoints["POST /v1/runs"]["headers"] == ["Idempotency-Key"]
    assert endpoints["POST /v1/runs/{run_id}/finalize"]["response"] == "#/$defs/FinalizeResponse"
    assert schema["x-steppy-error"] == "#/$defs/ErrorBody"
    assert schema["x-steppy-report"] == {"path": REPORT_PATH, "kind": REPORT_KIND}
    for ref in [v for shape in endpoints.values() for v in shape.values() if str(v).startswith("#/$defs/")]:
        assert ref.split("/")[-1] in schema["$defs"]


def test_the_bundle_manifest_is_the_request_body():
    """One manifest contract, not a second description of the same files."""
    request = generate_upload_schema()["$defs"]["CreateRunRequest"]
    assert request["properties"]["manifest"] == {"$ref": "#/$defs/BundleManifest"}


def test_unknown_fields_survive_and_absent_ones_default():
    """The contracts' charter: a newer server's extra field must reach an older
    client, and an older server's missing field must not crash a newer one."""
    created = CreateRunResponse.model_validate({"run_id": "R1", "future": 1, "uploads": [{"sha256": "a", "url": "u"}]})
    assert created.model_dump()["future"] == 1
    assert created.number is None and created.uploads[0].headers == {}

    assert FinalizeResponse.model_validate({}).url is None
    assert KeyResponse.model_validate({"project": {"id": "P1"}}).project.name == ""
    assert ErrorBody.model_validate({"detail": [{"loc": ["body"], "msg": "x"}]}).detail[0]["msg"] == "x"
