"""CI gate: the shipped example scenarios must always validate (FLAWS §7.4).

`scenarios/` is the in-repo corpus that feeds `steppy init`, teaches the format,
and backs newcomers' first green run. Nothing else validated it, so a broken
example could ship silently. This test parses every shipped scenario against the
repo's own `config.yaml` roles — so a bad selector, unknown role, or malformed
step fails the suite.
"""

from pathlib import Path

import pytest

from steppy.config import load_config
from steppy.parser import parse_scenarios_from_directory, validate_scenario_roles

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios"
_CONFIG = _REPO_ROOT / "config.yaml"


def test_scenarios_dir_is_non_empty():
    files = list(_SCENARIOS_DIR.glob("*.md"))
    assert files, "expected shipped example scenarios in scenarios/"


def test_all_example_scenarios_parse_and_roles_are_configured():
    config = load_config(_CONFIG)
    configured_roles = set(config.apps.keys())

    scenarios = parse_scenarios_from_directory(_SCENARIOS_DIR)
    assert scenarios, "no scenarios parsed"

    errors: list[str] = []
    for scenario in scenarios:
        errors.extend(validate_scenario_roles(scenario, configured_roles))

    assert not errors, "example scenarios reference unconfigured roles:\n" + "\n".join(errors)


@pytest.mark.parametrize("scenario_file", sorted(_SCENARIOS_DIR.glob("*.md")))
def test_each_scenario_file_parses(scenario_file):
    # parse_scenarios_from_directory raises ParseError on any malformed file;
    # parametrizing per-file gives a precise failure name.
    from steppy.parser import parse_scenario_file

    parse_scenario_file(scenario_file)
