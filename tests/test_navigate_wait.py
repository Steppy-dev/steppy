"""Tests for the `navigate` action's optional `wait:` strategy (Cmd 9).

`navigate` historically hardcoded `wait_for_load_state("networkidle")`, which
never settles on apps with long-lived SSE/streaming connections — so projects
abandoned `navigate` for bare `page.goto`, losing screenshot/guide integration.
`wait:` lets a step pick the load-state strategy (or `none` to skip the wait).
Omitting it must keep output byte-identical (`networkidle`).
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


def _gen(temp_scenario_file, body: str, navigate_wait: str | None = None) -> str:
    config = load_config()
    if navigate_wait is not None:
        config = config.model_copy(update={"timing": config.timing.model_copy(update={"navigate_wait": navigate_wait})})
    content = f"""
    ---
    title: Navigate
    auth: false
    ---

    ### 1. Go somewhere
    - role: editor
    - action: navigate
    - target: /customers
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(config).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_navigate_accepts_wait():
    step = Step(action="navigate", role="editor", target="/x", wait="domcontentloaded")
    assert step.wait == "domcontentloaded"


def test_navigate_wait_defaults_none():
    step = Step(action="navigate", role="editor", target="/x")
    assert step.wait is None


def test_wait_rejected_on_non_navigate():
    with pytest.raises(ValueError, match="only valid on navigate"):
        Step(action="click", role="editor", selector="#x", wait="load")


def test_navigate_rejects_unknown_wait():
    with pytest.raises(ValueError):
        Step(action="navigate", role="editor", target="/x", wait="bogus")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_default_is_networkidle(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    assert 'wait_for_load_state("networkidle")' in src


def test_codegen_explicit_strategy(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - wait: domcontentloaded")
    assert 'wait_for_load_state("domcontentloaded")' in src
    assert 'wait_for_load_state("networkidle")' not in src


def test_codegen_wait_none_omits_load_state(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - wait: none")
    assert "wait_for_load_state(" not in src
    assert "goto(" in src


# ── The page load itself ────────────────────────────────────────────────────
#
# goto() waits for `load` by default, before any wait_for_load_state runs, so
# a weaker strategy only takes effect when it reaches goto() as `wait_until`.


def test_default_config_passes_no_wait_until(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    assert "editor_page.goto(_interp('/customers', _vars))" in src
    assert "wait_until=" not in src


def test_global_domcontentloaded_reaches_goto(temp_scenario_file):
    src = _gen(temp_scenario_file, "", navigate_wait="domcontentloaded")
    assert "editor_page.goto(_interp('/customers', _vars), wait_until=\"domcontentloaded\")" in src
    assert 'wait_for_load_state("domcontentloaded")' in src


def test_global_none_commits_and_skips_the_load_state(temp_scenario_file):
    src = _gen(temp_scenario_file, "", navigate_wait="none")
    assert 'wait_until="commit"' in src
    assert "wait_for_load_state(" not in src


def test_global_load_passes_no_wait_until(temp_scenario_file):
    src = _gen(temp_scenario_file, "", navigate_wait="load")
    assert "wait_until=" not in src
    assert 'wait_for_load_state("load")' in src


def test_step_load_overrides_a_weaker_global(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - wait: load", navigate_wait="domcontentloaded")
    assert "wait_until=" not in src
    assert 'wait_for_load_state("load")' in src


def test_wait_until_comes_before_the_timeout(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - wait: domcontentloaded\n    - timeout: 10s")
    assert "goto(_interp('/customers', _vars), wait_until=\"domcontentloaded\", timeout=10000)" in src
