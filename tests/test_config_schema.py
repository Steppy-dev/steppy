"""Drift guard for the committed config JSON Schema (FLAWS §7.2).

``steppy-config.schema.json`` is generated from ``SteppyConfig`` and referenced
from ``config.yaml`` via a ``$schema`` header. If a model field is added,
renamed, or re-typed without regenerating the file, editor autocomplete goes
stale and ``steppy init`` would write a schema that disagrees with the model.
This test fails on any such drift — mirroring the coverage store's
``SCHEMA_VERSION`` guard (tests/test_coverage_store.py).
"""

from pathlib import Path

from steppy.config_schema import _canonicalise, generate_config_schema, schema_json

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "steppy-config.schema.json"

_REGEN_HINT = (
    "steppy-config.schema.json is out of sync with SteppyConfig. Regenerate it:\n"
    '  python -c "from steppy.config_schema import schema_json; '
    "open('steppy-config.schema.json','w',encoding='utf-8',newline='\\n').write(schema_json())\""
)


def test_committed_schema_matches_model():
    assert _SCHEMA_PATH.is_file(), f"missing {_SCHEMA_PATH}"
    committed = _SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == schema_json(), _REGEN_HINT


def test_schema_is_canonicalised_so_the_guard_survives_a_pydantic_upgrade():
    """The committed file is only a meaningful record of the models if its bytes
    depend on the models alone. ``pydantic>=2.0.0`` is unbounded, and two of its
    outputs vary across 2.x — ``additionalProperties: true`` on open dicts, and
    integral floats in numeric constraints. Both used to leak into the file, so
    the guard failed for anyone whose pydantic differed from whoever last
    regenerated it. ``_canonicalise`` removes them; this pins that it still does.
    """
    schema = generate_config_schema()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not (key == "additionalProperties" and value is True), (
                    "additionalProperties: true survived canonicalisation — newer pydantic emits it, "
                    "older omits it, and the byte guard cannot tell that apart from a real change"
                )
                # Only INTEGRAL floats are volatile (`0.0` vs `0`); a genuine
                # fractional bound like PdfConfig.scale's 0.1 is stable and
                # must survive untouched.
                assert not (
                    key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
                    and isinstance(value, float)
                    and value.is_integer()
                ), f"{key} is an integral float ({value!r}); pydantic types these inconsistently across versions"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def test_canonicalise_leaves_meaningful_content_alone():
    """It must not become a way to hide a genuine schema change."""
    source = {
        "additionalProperties": False,
        "properties": {"a": {"minimum": 1.5, "type": "number"}},
        "anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}],
    }
    assert _canonicalise(source) == {
        "additionalProperties": False,  # a real constraint, kept
        "properties": {"a": {"minimum": 1.5, "type": "number"}},  # non-integral, kept
        "anyOf": [{"type": "object"}, {"type": "null"}],  # the no-op default, dropped
    }


def test_schema_is_object_requiring_apps():
    schema = generate_config_schema()
    assert schema["type"] == "object"
    # ``apps`` is the one required top-level block.
    assert schema.get("required") == ["apps"]
    # extra = "forbid" ⇒ additionalProperties false on the root object.
    assert schema.get("additionalProperties") is False
