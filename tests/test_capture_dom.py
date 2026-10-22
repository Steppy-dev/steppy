"""Tests for `capture` with `from.type: dom` (Cmd 8).

`capture` read from redis/http/shell but not the DOM, so projects invented
DOM-capture-and-reuse via `globals()[...]` (grab an order ID/OTP from a
`data-testid`, reuse it later). DOM capture stores into `_vars` (interpolatable)
instead. redis/http/shell output stays byte-identical.
"""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import Step
from steppy.parser import parse_scenario_file


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


# ── Model ───────────────────────────────────────────────────────────────────


def test_capture_dom_minimal_text():
    step = Step(
        action="capture",
        role="editor",
        store="oid",
        **{"from": {"type": "dom", "selector": "[data-testid^='orders-']"}},
    )
    assert step.from_["type"] == "dom"


def test_capture_dom_attribute_extract():
    step = Step(
        action="capture",
        role="editor",
        store="oid",
        **{"from": {"type": "dom", "selector": "#x", "extract": "attribute", "attribute": "data-testid"}},
    )
    assert step.from_["extract"] == "attribute"


def test_capture_dom_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="capture", role="editor", store="oid", **{"from": {"type": "dom"}})


def test_capture_dom_requires_role():
    with pytest.raises(ValueError, match="requires 'role'"):
        Step(action="capture", store="oid", **{"from": {"type": "dom", "selector": "#x"}})


def test_capture_dom_attribute_requires_attribute_field():
    with pytest.raises(ValueError, match="requires 'attribute'"):
        Step(
            action="capture",
            role="editor",
            store="oid",
            **{"from": {"type": "dom", "selector": "#x", "extract": "attribute"}},
        )


def test_capture_dom_regex_requires_regex_field():
    with pytest.raises(ValueError, match="requires 'regex'"):
        Step(
            action="capture",
            role="editor",
            store="oid",
            **{"from": {"type": "dom", "selector": "#x", "extract": "regex"}},
        )


def test_capture_dom_rejects_bad_extract():
    with pytest.raises(ValueError, match="extract"):
        Step(
            action="capture",
            role="editor",
            store="oid",
            **{"from": {"type": "dom", "selector": "#x", "extract": "bogus"}},
        )


# ── Codegen ─────────────────────────────────────────────────────────────────


def _gen(temp_scenario_file, from_block: str) -> str:
    content = f"""
    ---
    title: Capture
    auth: false
    ---

    ### 1. Capture a value
    - role: editor
    - action: capture
    - store: order_id
    - from:
{from_block}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


def test_codegen_dom_calls_capture_dom_with_page(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        "        type: dom\n"
        "        selector: '[data-testid^=\"orders-list-status-\"]'\n"
        "        extract: attribute\n"
        "        attribute: data-testid\n"
        "        regex: 'orders-list-status-(.+)'",
    )
    assert "capture_dom(" in src
    assert "editor_page" in src
    assert "_vars['order_id'] = capture_dom(" in src


def test_codegen_shell_still_uses_capture(temp_scenario_file):
    src = _gen(temp_scenario_file, "        type: shell\n        command: 'echo hi'")
    assert "_vars['order_id'] = capture(" in src
    assert "capture_dom(" not in src
