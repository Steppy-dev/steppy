"""Exit-code policy for parallel runs: lenient FLAKY, strict on request.

The chosen contract:

  * pass-on-retry (FLAKY) ⇒ exit **0** — a pass is a pass
  * ``--strict-flaky``     ⇒ exit 1 when any scenario was flaky
  * failed every attempt   ⇒ exit 1 always
  * circuit breaker tripped ⇒ exit 1 always

This drives CI, so it gets its own file. The tests run ``run_parallel``
end-to-end with every Docker call neutered, and control the outcome by writing
result files into whichever queue the code under test seeds — which also proves
the retry pass is reached through the real code path, not a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steppy import coordinator
from steppy._parallel.compose import ComposeIntrospection
from steppy._parallel.queue import ScenarioResult


CONFIG = """\
apps:
  editor:
    name: Editor
    url: http://localhost:4200
    internal_url: http://app:80
    auth:
      mode: auth0
      credentials:
        username: u
        password: p
parallel:
  compose_file: docker-compose.yml
  workers: 2
"""

# Minimal valid scenario — the coordinator only needs it to parse and yield a
# slug; the tests never execute a step (see tests/test_parallel_guide_integration).
SCENARIO = "---\ntitle: {slug}\n---\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    (root / "scenarios").mkdir(parents=True)
    (root / "tests" / "e2e").mkdir(parents=True)
    (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (root / "docker-compose.yml").write_text("services:\n  app:\n    image: nginx\n", encoding="utf-8")
    for slug in ("alpha", "beta"):
        (root / "scenarios" / f"{slug}.md").write_text(SCENARIO.format(slug=slug), encoding="utf-8")
        (root / "tests" / "e2e" / f"test_{slug}.py").write_text("# generated", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _neuter(monkeypatch: pytest.MonkeyPatch, *, main_outcomes: dict, retry_outcomes: dict | None = None) -> dict:
    """No-op Docker and make each 'worker run' write the prescribed results.

    ``main_outcomes`` / ``retry_outcomes`` map slug -> exit code. Results are
    written into whatever coord dir the code seeded, so the retry pass's private
    subdir is exercised for real.
    """
    state: dict = {"passes": []}

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
    monkeypatch.setattr(
        coordinator,
        "_resolve_worker_container_ids",
        lambda project_names: {p: f"cid-{p}" for p in project_names},
    )
    monkeypatch.setattr(coordinator, "_down", lambda *a, **kw: None)

    class _NoopTailer:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(coordinator, "WorkerLogTailer", _NoopTailer)

    from steppy._parallel import retry as retry_mod

    monkeypatch.setattr(retry_mod, "WorkerLogTailer", _NoopTailer)
    monkeypatch.setattr(retry_mod, "_recreate_worker", lambda **kw: (0, None))

    # Every Queue the code creates is remembered, so "running the workers" can
    # drop results into the most recently seeded one.
    #
    # Patch the class the coordinator holds, not the one this file imported:
    # tests/test_no_parallel_import_on_default_path.py purges steppy._parallel.*
    # from sys.modules, so a later re-import binds a DIFFERENT class object and
    # patching ours would silently no-op (green alone, red in the full suite).
    # The main pass and the retry pass may hold different class objects after
    # a purge, so patch every one we can see.
    seeded: list = []
    for queue_cls in {coordinator.Queue, retry_mod.Queue}:
        real_seed = queue_cls.seed

        def _spy_seed(self, slugs, _real=real_seed):
            seeded.append(self)
            state["passes"].append(list(slugs))
            _real(self, slugs)

        monkeypatch.setattr(queue_cls, "seed", _spy_seed)

    def _fake_wait(container_ids, console, on_poll=None):
        queue = seeded[-1]
        outcomes = main_outcomes if len(seeded) == 1 else (retry_outcomes or {})
        for slug in state["passes"][-1]:
            code = outcomes.get(slug, 0)
            queue.write_result(
                ScenarioResult(
                    slug=slug,
                    worker_id="w1",
                    exit_code=code,
                    duration_s=1.0,
                    started_at=0.0,
                    finished_at=1.0,
                )
            )
        if on_poll is not None:
            on_poll()
        return [0] * len(container_ids)

    monkeypatch.setattr(coordinator, "_wait_for_workers", _fake_wait)
    return state


def _run(**kwargs) -> int:
    defaults = dict(
        workers=2,
        scenario=None,
        tag=None,
        headed=False,
        screenshots=False,
        guide=False,
        no_asserts=False,
        no_guide=False,
        locales=None,
        layouts=None,
        tests_dir=Path("tests/e2e"),
        scenarios_dir=Path("scenarios"),
        pytest_passthrough=[],
    )
    defaults.update(kwargs)
    return coordinator.run_parallel(**defaults)


def test_all_pass_exits_zero(project, monkeypatch):
    _neuter(monkeypatch, main_outcomes={})
    assert _run() == 0


def test_flaky_is_lenient_by_default(project, monkeypatch):
    """beta fails under load, passes alone ⇒ exit 0. This is the decision the
    whole retry pass hangs on."""
    state = _neuter(monkeypatch, main_outcomes={"beta": 1}, retry_outcomes={"beta": 0})
    assert _run(retry_pass=1) == 0
    # The retry pass ran, and only over the failure.
    assert state["passes"] == [["alpha", "beta"], ["beta"]]


def test_strict_flaky_fails_the_build(project, monkeypatch):
    _neuter(monkeypatch, main_outcomes={"beta": 1}, retry_outcomes={"beta": 0})
    assert _run(retry_pass=1, strict_flaky=True) == 1


def test_persistent_failure_exits_one(project, monkeypatch):
    _neuter(monkeypatch, main_outcomes={"beta": 1}, retry_outcomes={"beta": 1})
    assert _run(retry_pass=1) == 1


def test_retry_pass_zero_disables_the_feature(project, monkeypatch):
    state = _neuter(monkeypatch, main_outcomes={"beta": 1})
    assert _run(retry_pass=0) == 1
    assert state["passes"] == [["alpha", "beta"]], "no retry queue should be seeded"


def test_no_retry_when_everything_passed(project, monkeypatch):
    """A green run must not pay for a retry pass it has no work for."""
    state = _neuter(monkeypatch, main_outcomes={})
    assert _run(retry_pass=1) == 0
    assert len(state["passes"]) == 1


def test_retry_stops_early_once_clean(project, monkeypatch):
    """With passes=3 but a clean second attempt, no third attempt is spent."""
    state = _neuter(monkeypatch, main_outcomes={"beta": 1}, retry_outcomes={"beta": 0})
    assert _run(retry_pass=3) == 0
    assert len(state["passes"]) == 2
