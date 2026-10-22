"""Regression: ``steppy generate`` must drop ``_steppy_events.py`` next to
``conftest.py`` so the parallel-mode pytest plugin can be loaded by the
opt-in line in the generated conftest."""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from steppy.generator import SteppyGenerator
from steppy.models import AppAuthConfig, AppConfig, AuthCredentials, SteppyConfig


def _config() -> SteppyConfig:
    return SteppyConfig(
        apps={
            "editor": AppConfig(
                name="Editor",
                url="http://localhost",
                auth=AppAuthConfig(
                    mode="auth0",
                    credentials=AuthCredentials(username="u", password="p"),
                ),
            )
        }
    )


def test_generate_all_writes_events_plugin_alongside_conftest(tmp_path: Path):
    output_dir = tmp_path / "tests" / "e2e"
    gen = SteppyGenerator(config=_config())
    results = gen.generate_all(scenarios=[], output_dir=output_dir, force=True)
    events_file = output_dir / "_steppy_events.py"
    assert events_file.exists(), f"missing {events_file}; results: {results}"
    content = events_file.read_text(encoding="utf-8")
    assert "STEPPY-EVT::" in content
    assert "pytest_runtest_logreport" in content


def test_events_plugin_unchanged_on_second_generate(tmp_path: Path):
    """Idempotency: re-running ``steppy generate`` doesn't churn the file."""
    output_dir = tmp_path / "tests" / "e2e"
    gen = SteppyGenerator(config=_config())
    gen.generate_all(scenarios=[], output_dir=output_dir, force=True)
    second = gen.generate_all(scenarios=[], output_dir=output_dir, force=False)
    events_path = output_dir / "_steppy_events.py"
    assert second[str(events_path)] == "unchanged"


def test_conftest_opt_in_references_events_module(tmp_path: Path):
    """The conftest must conditionally import the events plugin's hook
    functions when ``STEPPY_EMIT_EVENTS=1`` is set — but never declare
    ``pytest_plugins`` (pytest 8 hard-fails on that in a non-rootdir
    conftest). The gate keeps single-worker output byte-identical."""
    output_dir = tmp_path / "tests" / "e2e"
    gen = SteppyGenerator(config=_config())
    gen.generate_all(scenarios=[], output_dir=output_dir, force=True)
    conftest = (output_dir / "conftest.py").read_text(encoding="utf-8")
    assert "STEPPY_EMIT_EVENTS" in conftest
    assert "from _steppy_events import" in conftest
    assert "pytest_runtest_logreport" in conftest
    # pytest_plugins in a non-rootdir conftest is a fail() in pytest 8 —
    # the opt-in path uses direct import instead. The substring may appear
    # in a comment explaining why; what matters is no top-level assignment.
    code_lines = [line for line in conftest.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not any(line.lstrip().startswith("pytest_plugins") for line in code_lines), (
        "pytest_plugins = [...] would trip pytest 8's non-rootdir check"
    )


def test_generated_conftest_loads_plugin_under_emit_events_env(tmp_path: Path, monkeypatch):
    """End-to-end regression: with ``STEPPY_EMIT_EVENTS=1`` set, the
    regenerated conftest must be importable and must pull the events
    plugin's hook functions into its module namespace.

    Catches the failure mode where the conftest declared
    ``pytest_plugins = [...]`` (which pytest 8 rejects in a non-rootdir
    conftest) or where ``_steppy_events.py`` is missing from sys.path."""
    output_dir = tmp_path / "tests" / "e2e"
    gen = SteppyGenerator(config=_config())
    gen.generate_all(scenarios=[], output_dir=output_dir, force=True)

    # Conftest opens ``config.yaml`` at import time (the runtime config
    # source). Stub one so the import doesn't fail for unrelated reasons.
    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor:\n    name: Editor\n    url: http://localhost\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("STEPPY_EMIT_EVENTS", "1")
    # Isolate sys.path mutations to this test — the conftest inserts its
    # parent dir on import, and we don't want that leaking.
    monkeypatch.setattr(sys, "path", list(sys.path))

    spec = importlib.util.spec_from_file_location("steppy_generated_conftest_under_test", output_dir / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        pytest.fail(f"regenerated conftest failed to import: {e!r}")

    # The pytest hook functions must be present in the conftest's namespace
    # — that's how pytest discovers them in a non-rootdir conftest without
    # the now-banned ``pytest_plugins`` declaration.
    for hook in (
        "pytest_collection_finish",
        "pytest_runtest_logstart",
        "pytest_runtest_logreport",
        "pytest_sessionfinish",
    ):
        assert hasattr(mod, hook), f"hook {hook} missing from conftest after import"


def test_missing_events_plugin_emits_warning_event(tmp_path: Path, monkeypatch):
    """When ``_steppy_events.py`` is missing (stale generated dir) but
    ``STEPPY_EMIT_EVENTS=1`` is set, the conftest's import block must emit
    a ``STEPPY-EVT::warning::events-plugin-missing`` line on stdout. The
    coordinator's EventBus routes that through the raw-log sink so the
    user can see why the live counters are degraded."""
    output_dir = tmp_path / "tests" / "e2e"
    gen = SteppyGenerator(config=_config())
    gen.generate_all(scenarios=[], output_dir=output_dir, force=True)

    # Simulate the stale-conftest failure mode: remove the plugin file.
    (output_dir / "_steppy_events.py").unlink()

    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor:\n    name: Editor\n    url: http://localhost\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("STEPPY_EMIT_EVENTS", "1")
    monkeypatch.setattr(sys, "path", list(sys.path))
    # A previous test in this module already imported ``_steppy_events`` from
    # a different tmp_path. The module object is cached in ``sys.modules``,
    # so unlinking the file isn't enough to force an ImportError — we have
    # to evict the cache entry too.
    monkeypatch.delitem(sys.modules, "_steppy_events", raising=False)

    spec = importlib.util.spec_from_file_location(
        "steppy_generated_conftest_missing_plugin", output_dir / "conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    captured = io.StringIO()
    with redirect_stdout(captured):
        spec.loader.exec_module(mod)

    out = captured.getvalue()
    assert "STEPPY-EVT::warning::events-plugin-missing" in out, f"expected warning event on stdout, got: {out!r}"
