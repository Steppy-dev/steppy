"""``steppy validate`` warns when a scenario ``timeout:`` reads like a whole-test limit.

Since 0.3.0 the scenario ``timeout:`` is the default timeout of every step (it
did nothing before). Projects had set it to minutes, reading it as a limit for
the whole test, so a failing step now waits that long.
"""

from textwrap import dedent

from typer.testing import CliRunner

from steppy.cli import app
from steppy.models import Scenario, Step
from steppy.parser import lint_scenario_timeout


def _scenario(timeout):
    return Scenario(
        title="T",
        timeout=timeout,
        steps=[Step(action="navigate", role="editor", target="/")],
    )


def test_a_large_timeout_warns_once_with_the_per_step_reading():
    warnings = lint_scenario_timeout(_scenario(180000))
    assert len(warnings) == 1
    assert "applies to every step" in warnings[0]
    assert "180s" in warnings[0]


def test_sixty_seconds_is_still_fine():
    assert lint_scenario_timeout(_scenario(60000)) == []


def test_just_above_warns():
    assert lint_scenario_timeout(_scenario(60001))


def test_unset_is_silent():
    assert lint_scenario_timeout(_scenario(None)) == []


def test_validate_prints_it_and_still_passes(tmp_path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "slow.md").write_text(
        dedent("""\
        ---
        title: Slow
        auth: false
        timeout: 300000
        ---

        ### 1. Open
        - role: editor
        - action: navigate
        - target: /
        """),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["validate", "-d", str(scenarios)])
    out = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert "WARNING slow: timeout: 300000 applies to every step" in out
