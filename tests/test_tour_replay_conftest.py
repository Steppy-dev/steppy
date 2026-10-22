"""The conftest's in-app tours block (``templates/conftest.py.j2``, gated on ``tours_enabled``).

The block rebinds three module-level names at EOF instead of editing their
bodies. These tests pin that each wrapper delegates to what it wraps, and —
through a real ``pytest --collect-only`` over the generated demo suite — that
pytest sees the rebound collection hook.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from steppy import naming
from steppy.cli import app
from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenarios_from_directory

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
DEMO = ROOT / "fixtures" / "tour-demo" / "project"
BLOCK_START = "# ── In-app tours (config.yaml `tours.enabled`)"


def _conftest(config_path: Path) -> str:
    config = load_config(config_path)
    scenarios = parse_scenarios_from_directory(DEMO / "scenarios")
    return SteppyGenerator(config, templates_dir=TEMPLATES).generate_conftest(scenarios)


def test_the_block_renders_only_with_tours_on():
    on = _conftest(DEMO / "config.yaml")
    off = _conftest(ROOT / "config.yaml")
    compile(on, "conftest.py", "exec")
    assert on.count(BLOCK_START) == 1 and BLOCK_START not in off
    # The block is appended: everything before it is the tours-off conftest's text.
    assert on.endswith("\n") and not on.endswith("\n\n")
    assert on.rindex("_new_role_context = _tours_with_kill_switch(_new_role_context)") > on.index(
        "def _new_role_context("
    )


def _block_namespace(**globals_):
    """Exec the rendered block alone, over stub originals."""
    code = _conftest(DEMO / "config.yaml")
    block = code[code.index(BLOCK_START) :]
    calls = []

    def _new_role_context(browser, role, **extra):
        calls.append(("context", browser, role, extra))
        return SimpleNamespace(init_scripts=[], add_init_script=lambda s: context_scripts.append(s))

    def _coverage_stop_and_write(state, request):
        calls.append(("coverage", state, request))

    def pytest_collection_modifyitems(config, items):
        calls.append(("collect", list(items)))

    context_scripts = []
    namespace = {
        "_new_role_context": _new_role_context,
        "_coverage_stop_and_write": _coverage_stop_and_write,
        "pytest_collection_modifyitems": pytest_collection_modifyitems,
        "GUIDE_MODE": False,
        **globals_,
    }
    exec(compile(block, "conftest-block", "exec"), namespace)
    return namespace, calls, context_scripts


def _item(replay_role=None):
    function = SimpleNamespace()
    if replay_role:
        function._steppy_tour_role = replay_role
    return SimpleNamespace(function=function)


def test_every_context_gets_the_kill_switch(tmp_path):
    ns, calls, scripts = _block_namespace()
    ns["_new_role_context"]("browser", "editor", storage_state="s.json")
    assert calls == [("context", "browser", "editor", {"storage_state": "s.json"})]
    assert scripts == [f"globalThis.{naming.TOURS_KILL_SWITCH_GLOBAL} = true;"]


def test_replays_write_no_coverage_record():
    ns, calls, _ = _block_namespace()
    detached = []
    state = {"cdp": SimpleNamespace(detach=lambda: detached.append(True))}
    ns["_coverage_stop_and_write"](state, SimpleNamespace(node=_item("editor")))
    assert calls == [] and detached == [True]
    plain, idle = SimpleNamespace(node=_item()), SimpleNamespace(node=_item("editor"))
    ns["_coverage_stop_and_write"](state, plain)
    ns["_coverage_stop_and_write"](None, idle)  # coverage off: the original's own no-op
    assert calls == [("coverage", state, plain), ("coverage", None, idle)]


def test_a_failing_detach_never_fails_the_test():
    ns, calls, _ = _block_namespace()

    def boom():
        raise RuntimeError("context already closed")

    ns["_coverage_stop_and_write"]({"cdp": SimpleNamespace(detach=boom)}, SimpleNamespace(node=_item("editor")))
    assert calls == []


@pytest.mark.parametrize("guide_mode", [False, True])
def test_guide_runs_deselect_replays_then_defer_to_the_original_hook(guide_mode):
    ns, calls, _ = _block_namespace(GUIDE_MODE=guide_mode)
    deselected = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    scenario, replay = _item(), _item("editor")
    items = [scenario, replay]
    ns["pytest_collection_modifyitems"](config, items)
    expected = [scenario] if guide_mode else [scenario, replay]
    assert items == expected and calls == [("collect", expected)]
    assert deselected == ([replay] if guide_mode else [])


# ── The real thing: pytest collecting a generated suite ────────────────────────


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    project = tmp_path_factory.mktemp("tour-demo")
    shutil.copytree(DEMO, project, dirs_exist_ok=True)
    cwd = os.getcwd()
    os.chdir(project)
    try:
        result = CliRunner().invoke(app, ["generate", "-o", "tests/e2e"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    return project


def _collect(project: Path, *args: str, **env: str) -> list[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("STEPPY_")}
    environ.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/e2e", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=project,
        env=environ,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [line.split("::", 1)[1] for line in proc.stdout.splitlines() if "::" in line]


SCENARIOS = ("create_invoice", "remove_inactive_customers", "send_invoice")


def _ids(*suffixes: str, locale: str = "default") -> list[str]:
    """Collection order: per scenario, per test (scenario then replay), per layout."""
    return [
        f"test_editor__{scenario}{suffix}[{locale}-{size}]"
        for scenario in SCENARIOS
        for suffix in suffixes
        for size in ("desktop", "mobile")
    ]


def test_pytest_collects_each_replay_beside_its_scenario(generated):
    # Every demo scenario declares `layouts: [desktop, mobile]`: its replay runs at both.
    assert _collect(generated) == _ids("", "__tour")


def test_a_replay_is_parametrized_like_its_scenario(generated):
    ids = _collect(generated, STEPPY_LOCALES="en,fr")
    assert "test_editor__create_invoice__tour[en-desktop]" in ids
    assert "test_editor__create_invoice__tour[fr-mobile]" in ids


def test_guide_mode_deselects_every_replay(generated):
    assert _collect(generated, STEPPY_GUIDE_MODE="1") == _ids("")


def test_not_tour_keyword_skips_replays(generated):
    assert _collect(generated, "-k", "not __tour") == _ids("")
