"""Unit tests for parallel + guide-mode env-var propagation.

The parallel coordinator (``steppy.coordinator.run_parallel``) is
supposed to mirror ``cli.run``'s logic for resolving ``--guide``,
``--no-guide``, ``--locales``, and ``--layouts`` before it spawns Docker
workers. These tests pin that contract without touching Docker: every
Docker-adjacent call is monkeypatched to a no-op, and we capture the
kwargs handed to :func:`render_override` (the function that emits each
worker container's environment block) plus the slugs handed to
:meth:`Queue.seed`.

Why this matters: a single-worker ``steppy run --guide`` and a parallel
``steppy run --workers N --guide`` must produce identical screenshot
trees under ``output/screenshots/``. That requires every ``STEPPY_*``
env var to flow through the coordinator with the same auto-enable /
override rules as ``cli.run``. There is no Docker-based integration
test in CI (see ``tests/integration/README.md``); these unit tests are
the contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from steppy import coordinator
from steppy._parallel.compose import ComposeIntrospection
from steppy._parallel.queue import Queue, ScenarioResult


# ── Project layout helper ───────────────────────────────────────────────────


def _make_project(
    tmp_path: Path,
    *,
    locales: list[str] | None = None,
    layouts: dict[str, dict[str, int]] | None = None,
    scenarios: dict[str, bool] | None = None,
) -> Path:
    """Materialize a minimal Steppy project under ``tmp_path/proj/``.

    Args:
        locales: written to ``config.yaml``'s ``locales:`` list. Default ``["fr", "en"]``.
        layouts: written to ``config.yaml``'s ``screenshot.layouts:`` map; each value is
            ``{"width": int, "height": int}``. ``None`` (or empty) omits the block.
        scenarios: ``{slug: guide_enabled_bool}`` — one ``scenarios/<slug>.md``
            per entry, with the given ``guide.enabled`` flag.

    Returns the project root. Does NOT chdir — the caller does that via
    ``monkeypatch.chdir(project_root)`` so the cleanup is automatic.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()
    scenarios_dir = project_root / "scenarios"
    scenarios_dir.mkdir()
    (project_root / "tests" / "e2e").mkdir(parents=True)
    # docker-compose.yml must exist (coordinator checks early at line 97).
    # Content is irrelevant because introspect_user_compose is monkeypatched.
    (project_root / "docker-compose.yml").write_text("services:\n  app:\n    image: alpine\n", encoding="utf-8")

    locales_list = locales if locales is not None else ["fr", "en"]
    default_locale = "fr" if "fr" in locales_list else locales_list[0]

    layouts_block = ""
    if layouts:
        layouts_lines = "\n".join(
            f"    {name}:\n      width: {v['width']}\n      height: {v['height']}" for name, v in layouts.items()
        )
        layouts_block = f"  layouts:\n{layouts_lines}\n"

    config_yaml = (
        "apps:\n"
        "  editor:\n"
        "    name: Editor\n"
        "    url: http://localhost:4200\n"
        "    internal_url: http://editor-app:3000\n"
        "    auth:\n"
        "      mode: auth0\n"
        "      credentials:\n"
        "        username: u\n"
        "        password: p\n"
        f"locales: {locales_list}\n"
        f"default_locale: {default_locale}\n"
        "screenshot:\n"
        "  viewport:\n"
        "    width: 1280\n"
        "    height: 720\n"
        f"{layouts_block}"
        "parallel:\n"
        "  workers: 2\n"
        "  compose_file: docker-compose.yml\n"
    )
    (project_root / "config.yaml").write_text(config_yaml, encoding="utf-8")

    for slug, guide_enabled in (scenarios or {}).items():
        scenario_md = f"---\ntitle: {slug}\nguide:\n  enabled: {'true' if guide_enabled else 'false'}\n---\n"
        (scenarios_dir / f"{slug}.md").write_text(scenario_md, encoding="utf-8")

    return project_root


# ── Docker-surface neutralizer ──────────────────────────────────────────────


def _neuter_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op every Docker-touching call in :mod:`steppy.coordinator` and
    :mod:`steppy._parallel` so ``run_parallel`` completes end-to-end without
    contacting Docker.

    Patched at the import site (``steppy.coordinator``) because that module
    binds ``from .X import Y`` at top — replacing the symbol on the source
    module wouldn't be seen by ``run_parallel``.
    """
    monkeypatch.setattr(coordinator, "check_compose_version", lambda: (2, 30, 0))
    monkeypatch.setattr(
        coordinator,
        "introspect_user_compose",
        lambda compose_file: ComposeIntrospection(services_with_ports=["app"], networks=[]),
    )
    monkeypatch.setattr(coordinator, "ensure_worker_image", lambda **kw: ("steppy-worker:test", "cached"))
    monkeypatch.setattr(
        coordinator,
        "_bring_up_all_stacks",
        lambda **kw: [f"steppy-run-{kw['stack_id']}-w{i}" for i in range(1, kw["worker_count"] + 1)],
    )
    # Container-ID resolution is a real docker compose ps call — return a
    # synthetic ID per project so the wait loop has something to inspect.
    monkeypatch.setattr(
        coordinator,
        "_resolve_worker_container_ids",
        lambda project_names: {p: f"cid-{p}" for p in project_names},
    )
    # A worker that exits 0 has DRAINED the queue, so model that rather than
    # returning success having claimed nothing: the coordinator now reports a
    # queued-but-never-claimed scenario as a failure, which is the whole point
    # of that check. Recording the seeded Queue is how the fake reaches it.
    drained: list[Queue] = []
    real_seed = Queue.seed

    def _seed_and_record(self, slugs, **kwargs):
        drained.append(self)
        return real_seed(self, slugs, **kwargs)

    monkeypatch.setattr(Queue, "seed", _seed_and_record)

    # ``on_poll`` is the circuit breaker's hook; accept and ignore it — with
    # every worker "exiting" immediately there is nothing to abort.
    def _fake_wait(container_ids, console, on_poll=None):
        if drained:
            queue = drained[-1]
            while True:
                task = queue.claim_task("w1")
                if task is None:
                    break
                for slug in task.slugs:
                    queue.write_result(
                        ScenarioResult(
                            slug=slug,
                            worker_id="w1",
                            exit_code=0,
                            duration_s=0.0,
                            started_at=0.0,
                            finished_at=0.0,
                        )
                    )
        return [0] * len(container_ids)

    monkeypatch.setattr(coordinator, "_wait_for_workers", _fake_wait)
    monkeypatch.setattr(coordinator, "_down", lambda *a, **kw: None)

    class _NoopTailer:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(coordinator, "WorkerLogTailer", _NoopTailer)

    class _NoopRenderer:
        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(coordinator, "make_renderer", lambda **kw: _NoopRenderer())
    monkeypatch.setattr(coordinator, "render_summary", lambda **kw: None)

    # ``persist_failure_artifacts`` is lazy-imported inside run_parallel —
    # patch at the source module so the late ``from ._parallel.failures
    # import persist_failure_artifacts`` picks up our no-op.
    import steppy._parallel.failures as failures_mod

    monkeypatch.setattr(failures_mod, "persist_failure_artifacts", lambda **kw: None)


# ── Spies ───────────────────────────────────────────────────────────────────


def _spy_render_override(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``coordinator.render_override`` with a spy that captures
    kwargs AND delegates to the real implementation, so each override
    file still contains valid YAML."""
    captured: list[dict[str, Any]] = []
    real = coordinator.render_override

    def _spy(**kwargs):
        captured.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(coordinator, "render_override", _spy)
    return captured


def _spy_queue_seed(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the slugs passed to ``Queue.seed`` (one entry per run)."""
    captured: list[list[str]] = []
    real_seed = Queue.seed

    def _spy(self, slugs):
        captured.append(list(slugs))
        return real_seed(self, slugs)

    monkeypatch.setattr(Queue, "seed", _spy)
    return captured


# ── Common run_parallel invocation ──────────────────────────────────────────


def _run(**kwargs) -> int:
    """Invoke ``run_parallel`` with sensible defaults for tests; overridable
    via kwargs."""
    defaults: dict[str, Any] = {
        "workers": 2,
        "scenario": None,
        "tag": None,
        "headed": False,
        "screenshots": False,
        "guide": False,
        "no_asserts": False,
        "no_guide": False,
        "locales": None,
        "layouts": None,
        "tests_dir": Path("tests/e2e"),
        "scenarios_dir": Path("scenarios"),
        "pytest_passthrough": [],
        "no_build": False,
        "verbose": False,
    }
    defaults.update(kwargs)
    return coordinator.run_parallel(**defaults)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_run_parallel_passes_guide_env_vars_to_overrides(tmp_path, monkeypatch):
    """--guide with configured locales + layouts populates all five
    STEPPY_* env vars in every per-worker override."""
    project_root = _make_project(
        tmp_path,
        locales=["fr", "nl"],
        layouts={
            "desktop": {"width": 1280, "height": 720},
            "mobile": {"width": 375, "height": 667},
        },
        scenarios={"alpha": True, "beta": True},
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    captured = _spy_render_override(monkeypatch)

    exit_code = _run(workers=2, guide=True)

    assert exit_code == 0
    assert len(captured) == 2  # one per worker
    for call in captured:
        assert call["locales"] == "fr,nl"
        # Dict insertion order is preserved (Python 3.7+), but be permissive
        # about layout ordering since it only depends on config.yaml YAML order.
        assert call["layouts"] in ("desktop,mobile", "mobile,desktop")
        assert call["guide_mode"] == "1"
        assert call["no_asserts"] == "1"
        assert call["screenshots"] == "1"


def test_run_parallel_passes_coverage_env_to_overrides(tmp_path, monkeypatch):
    """--coverage flows into every per-worker override as STEPPY_COVERAGE='1'
    (and stays '' when off) — workers record into the shared output/ mount
    under the coordinator's STEPPY_RUN_ID."""
    project_root = _make_project(
        tmp_path,
        locales=["fr"],
        layouts=None,
        scenarios={"alpha": True, "beta": True},
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    captured = _spy_render_override(monkeypatch)

    exit_code = _run(workers=2, coverage=True)

    assert exit_code == 0
    assert len(captured) == 2
    for call in captured:
        assert call["coverage"] == "1"

    captured.clear()
    assert _run(workers=2) == 0
    for call in captured:
        assert call["coverage"] == ""


def test_run_parallel_guide_empty_layouts_when_unconfigured(tmp_path, monkeypatch):
    """When the project doesn't declare ``screenshot.layouts``, ``--guide``
    still enables the other STEPPY_* flags and passes ``layouts`` as the
    empty string (which the worker conftest reads as "no layout dimension")."""
    project_root = _make_project(
        tmp_path,
        locales=["fr"],
        layouts=None,
        scenarios={"alpha": True, "beta": True},
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    captured = _spy_render_override(monkeypatch)

    exit_code = _run(workers=2, guide=True)

    assert exit_code == 0
    assert len(captured) == 2
    for call in captured:
        assert call["locales"] == "fr"
        assert call["layouts"] == ""
        assert call["guide_mode"] == "1"
        assert call["no_asserts"] == "1"
        assert call["screenshots"] == "1"


def test_run_parallel_explicit_locales_override_guide_defaults(tmp_path, monkeypatch):
    """``--locales`` on the CLI wins over the config-derived default in
    guide mode. Mirrors the ``cli.run`` precedence."""
    project_root = _make_project(
        tmp_path,
        locales=["fr", "nl"],
        layouts={"desktop": {"width": 1280, "height": 720}},
        scenarios={"alpha": True, "beta": True},
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    captured = _spy_render_override(monkeypatch)

    exit_code = _run(workers=2, guide=True, locales="en")

    assert exit_code == 0
    assert len(captured) == 2
    for call in captured:
        assert call["locales"] == "en"  # explicit, not the config's "fr,nl"


def test_run_parallel_explicit_layouts_override_guide_defaults(tmp_path, monkeypatch):
    """``--layouts`` on the CLI wins over the config-derived default in
    guide mode."""
    project_root = _make_project(
        tmp_path,
        locales=["fr"],
        layouts={
            "desktop": {"width": 1280, "height": 720},
            "mobile": {"width": 375, "height": 667},
        },
        scenarios={"alpha": True, "beta": True},
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    captured = _spy_render_override(monkeypatch)

    exit_code = _run(workers=2, guide=True, layouts="mobile")

    assert exit_code == 0
    assert len(captured) == 2
    for call in captured:
        assert call["layouts"] == "mobile"  # explicit, not the config's "desktop,mobile"


def test_run_parallel_guide_filters_to_guide_enabled(tmp_path, monkeypatch):
    """``--guide`` selects only scenarios with ``guide.enabled = true``."""
    project_root = _make_project(
        tmp_path,
        locales=["fr"],
        scenarios={
            "alpha": True,
            "beta": False,
            "gamma": True,
        },
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    seeded = _spy_queue_seed(monkeypatch)

    exit_code = _run(workers=2, guide=True)

    assert exit_code == 0
    assert len(seeded) == 1
    assert sorted(seeded[0]) == ["alpha", "gamma"]
    assert "beta" not in seeded[0]


def test_run_parallel_no_guide_filters_out_guide_enabled(tmp_path, monkeypatch):
    """``--no-guide`` selects only scenarios that are NOT guide-enabled."""
    project_root = _make_project(
        tmp_path,
        locales=["fr"],
        scenarios={
            "alpha": True,
            "beta": False,
            "gamma": False,
        },
    )
    monkeypatch.chdir(project_root)
    _neuter_docker(monkeypatch)
    seeded = _spy_queue_seed(monkeypatch)

    exit_code = _run(workers=2, no_guide=True)

    assert exit_code == 0
    assert len(seeded) == 1
    assert sorted(seeded[0]) == ["beta", "gamma"]
    assert "alpha" not in seeded[0]
