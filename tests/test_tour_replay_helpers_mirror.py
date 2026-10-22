"""``templates/_steppy_tours.py`` mirrors the contract it cannot import.

The generated suite is standalone, so the replay helper carries its own copy of
the overlay's names: the host tag, events, kill switch, probe binding, bubble
attributes and ``part`` names. Each must equal ``steppy.naming`` /
``steppy.tour_spec``, which the TypeScript side is in turn synced against.
"""

import ast
import importlib.util
import re
from pathlib import Path

from steppy import naming, tour_spec

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "templates" / "_steppy_tours.py"
SOURCE = HELPER.read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("_steppy_tours_mirror", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_helper_imports_only_the_stdlib():
    imported = set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports would tie the helper to the package layout"
            imported.add(node.module.split(".")[0])
    assert imported <= {"json", "os", "time"}, imported


def test_names_equal_the_python_contract():
    module = _module()
    assert module.TOUR_HOST_TAG == naming.TOUR_HOST_TAG
    assert module.TOURS_KILL_SWITCH_GLOBAL == naming.TOURS_KILL_SWITCH_GLOBAL
    assert module.TOUR_PROBE_BINDING == naming.TOUR_PROBE_BINDING
    assert module.TOUR_EVENTS == tour_spec.TOUR_EVENTS
    assert module.ATTR_STEP_ID == tour_spec.ATTR_STEP_ID


def test_the_snapshot_reads_the_contract_attributes():
    module = _module()
    for attribute in (tour_spec.ATTR_STEP_ID, tour_spec.ATTR_STATE):
        assert f"getAttribute('{attribute}')" in module._SNAPSHOT_JS


def test_every_part_the_helper_locates_exists():
    used = set(re.findall(r'\[part~=\\?"([a-z-]+)\\?"\]', SOURCE))
    assert used and used <= set(tour_spec.TOUR_PARTS), used - set(tour_spec.TOUR_PARTS)


def test_states_reasons_and_kinds_are_contract_values():
    assert set(re.findall(r'== "(shown|fallback)"', SOURCE)) <= set(tour_spec.BUBBLE_STATES)
    hints = re.search(r"hints = \{(.*?)\}", SOURCE, re.S).group(1)
    assert set(re.findall(r'"([a-z-]+)": ', hints)) <= set(tour_spec.TOUR_ERROR_REASONS)
    assert '"target-timeout"' in SOURCE
    kinds = re.findall(r'kind (?:==|in) \(?((?:"[a-z]+",? ?)+)\)?', SOURCE)
    seen = {k for group in kinds for k in re.findall(r'"([a-z]+)"', group)}
    assert seen and seen <= set(tour_spec.ADVANCE_KINDS), seen


def test_the_conftest_kill_switch_and_role_marker_agree():
    conftest = (ROOT / "templates" / "conftest.py.j2").read_text(encoding="utf-8")
    generator = (ROOT / "src" / "steppy" / "generator.py").read_text(encoding="utf-8")
    assert f'"globalThis.{naming.TOURS_KILL_SWITCH_GLOBAL} = true;"' in conftest
    assert "_steppy_tour_role" in conftest and "._steppy_tour_role = " in generator
