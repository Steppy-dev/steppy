"""Drift guard for the committed in-app tour JSON Schemas.

Mirrors ``tests/test_bundle_schema.py``. ``contracts/tours-manifest.schema.json``
and ``contracts/tour.schema.json`` publish the tour data contract for
consumers that are not Python (a separate-repo framework adapter, a guide
host's CI). They must describe exactly what the compiler emits.
"""

from pathlib import Path

from steppy.contracts.tours import (
    generate_manifest_schema,
    generate_tour_schema,
    manifest_schema_json,
    tour_schema_json,
)
from steppy.tour_spec import TOUR_SCHEMA_VERSION

_CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"

_REGEN_HINT = (
    "contracts/{name} is out of sync with steppy.contracts.tours. Regenerate both:\n"
    '  python -c "from steppy.contracts.tours import manifest_schema_json, tour_schema_json; '
    "open('contracts/tours-manifest.schema.json','w',encoding='utf-8',newline='\\n').write(manifest_schema_json()); "
    "open('contracts/tour.schema.json','w',encoding='utf-8',newline='\\n').write(tour_schema_json())\""
)


def test_committed_manifest_schema_matches_the_models():
    path = _CONTRACTS / "tours-manifest.schema.json"
    assert path.read_text(encoding="utf-8") == manifest_schema_json(), _REGEN_HINT.format(name=path.name)


def test_committed_tour_schema_matches_the_models():
    path = _CONTRACTS / "tour.schema.json"
    assert path.read_text(encoding="utf-8") == tour_schema_json(), _REGEN_HINT.format(name=path.name)


def test_schemas_advertise_the_contract_version():
    assert generate_manifest_schema()["x-steppy-tour-schema"] == TOUR_SCHEMA_VERSION
    assert generate_tour_schema()["x-steppy-tour-schema"] == TOUR_SCHEMA_VERSION


def test_published_schemas_stay_open_for_forward_compatibility():
    # The player ignores unknown keys within a schema major; the published
    # contract must say so too, even though the producer's own check is strict.
    for text in (manifest_schema_json(), tour_schema_json()):
        assert '"additionalProperties": false' not in text
