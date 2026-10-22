"""JSON Schema generation for ``config.yaml`` (FLAWS §7.2).

``SteppyConfig`` uses Pydantic ``extra = "forbid"`` everywhere, so any typo in
``config.yaml`` is a hard failure with no autocomplete. Pydantic hands us the
antidote for free: dump ``SteppyConfig.model_json_schema()`` to a committed
``steppy-config.schema.json`` and reference it from the top of ``config.yaml``
with a ``# yaml-language-server: $schema=...`` line — the VS Code YAML extension
then provides autocomplete + inline typo squiggles, flipping ``extra="forbid"``
from hostile to helpful.

This module is the single source of truth for that schema, consumed by both
``steppy init`` (which writes the schema into a fresh project) and the drift
guard test (``tests/test_config_schema.py``), so the committed file can never
silently fall out of sync with the model.
"""

from __future__ import annotations

import json

from .models import SteppyConfig

# Bumped only when we intentionally change the emitted schema shape (e.g. adopt
# a newer JSON Schema dialect). Consumers should not care about this value; it
# exists so a regeneration is an explicit, reviewable diff.
SCHEMA_TITLE = "Steppy configuration (config.yaml)"

#: Numeric constraint keywords whose value pydantic may emit as ``0`` or ``0.0``
#: depending on its version. JSON Schema treats the two as the same number.
_NUMERIC_KEYWORDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"})


def _canonicalise(node):
    """Strip the parts of pydantic's output that vary by pydantic version.

    The committed schema is an *artifact*, and the guard test asserts it matches
    the models byte for byte — which only means anything if the bytes are a
    function of the models. They were not: ``pydantic>=2.0.0`` is unbounded, and
    two things it emits differently across 2.x releases leaked straight into the
    file, so the guard failed on a machine whose pydantic simply differed from
    the one that last regenerated it. Neither difference carries meaning:

    * ``additionalProperties: true`` on an open ``dict`` field — ``true`` is the
      JSON Schema default, so emitting it and omitting it say the same thing.
    * ``exclusiveMinimum: 0.0`` vs ``0`` — the same number, differently typed.

    Normalising both here makes the artifact reproducible, which is the property
    a committed generated file needs. Add a rule when a future pydantic
    introduces another purely-cosmetic difference; do NOT reach for it to paper
    over a real schema change.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "additionalProperties" and value is True:
                continue
            if key in _NUMERIC_KEYWORDS and isinstance(value, float) and value.is_integer():
                out[key] = int(value)
                continue
            out[key] = _canonicalise(value)
        return out
    if isinstance(node, list):
        return [_canonicalise(item) for item in node]
    return node


def generate_config_schema() -> dict:
    """Return the JSON Schema for ``config.yaml`` as a plain dict.

    Deterministic: same model in ⇒ same bytes out (see :func:`schema_json`), so
    the drift guard can assert byte-equality against the committed file.
    """
    schema = _canonicalise(SteppyConfig.model_json_schema())
    # A stable, self-describing title so the generated file reads well and the
    # editor shows something meaningful on hover at the root.
    schema["title"] = SCHEMA_TITLE
    return schema


def schema_json() -> str:
    """Return the canonical serialized form written to disk / compared in tests.

    ``sort_keys=True`` + 2-space indent + trailing newline, over a
    :func:`_canonicalise`-d schema, so the output depends on the models and not
    on which pydantic 2.x happens to be installed.
    """
    return json.dumps(generate_config_schema(), indent=2, sort_keys=True) + "\n"
