"""Coordinator-level behaviour of exclusion groups.

Drives ``run_parallel`` end to end with every Docker call neutered, controlling
outcomes by writing result files into whichever queue the code seeded. The three
things pinned here are the ones adversarial review found could each report
success for a run that executed nothing:

  * a selection that collapses to ONE task must still run (never ``return 0``),
  * a scenario that was queued and never claimed must fail the build, on both
    exit-code branches, and
  * the retry pass must regroup, and must not label a never-started scenario
    FLAKY once it recovers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steppy import coordinator
from steppy._parallel.compose import ComposeIntrospection
from steppy._parallel.queue import ScenarioResult
from steppy.exclusive import Task


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

SCENARIO = "---\ntitle: {slug}\n{extra}---\n"


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, groups: dict[str, str | None]) -> Path:
    root = tmp_path / "proj"
    (root / "scenarios").mkdir(parents=True)
    (root / "tests" / "e2e").mkdir(parents=True)
    (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (root / "docker-compose.yml").write_text("services:\n  app:\n    image: nginx\n", encoding="utf-8")
    for slug, group in groups.items():
        extra = f"exclusive: {group}\n" if group else ""
        (root / "scenarios" / f"{slug}.md").write_text(SCENARIO.format(slug=slug, extra=extra), encoding="utf-8")
        (root / "tests" / "e2e" / f"test_{slug}.py").write_text("# generated", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _neuter(monkeypatch: pytest.MonkeyPatch, *, outcomes_per_pass: list[dict]) -> dict:
    """No-op Docker; each simulated pass writes the outcomes it was given.

    ``outcomes_per_pass[i]`` maps slug -> exit code for pass i. A slug absent
    from the mapping produces **no result at all**, which is how "queued but
    never claimed" is modelled.
    """
    state: dict = {"seeded": [], "batches": [], "stacks": []}

    monkeypatch.setattr(coordinator, "check_compose_version", lambda: (2, 30, 0))
    monkeypatch.setattr(
        coordinator,
        "introspect_user_compose",
        lambda compose_file: ComposeIntrospection(services_with_ports=["app"], networks=[]),
    )
    monkeypatch.setattr(coordinator, "ensure_worker_image", lambda **kw: ("steppy-worker:test", "cached"))

    def _bring_up(**kw):
        state["stacks"].append(kw["worker_count"])
        return [f"steppy-run-{kw['stack_id']}-w{i}" for i in range(1, kw["worker_count"] + 1)]

    monkeypatch.setattr(coordinator, "_bring_up_all_stacks", _bring_up)
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

    # Patch the class object each module holds, not the one this file imported —
    # tests/test_no_parallel_import_on_default_path.py purges steppy._parallel.*
    # from sys.modules, so a later re-import binds a different class.
    queues: list = []
    for queue_cls in {coordinator.Queue, retry_mod.Queue}:
        real_seed = queue_cls.seed

        def _spy_seed(self, slugs, *, batches=None, _real=real_seed):
            queues.append(self)
            state["seeded"].append(list(slugs))
            state["batches"].append(dict(batches) if batches else None)
            _real(self, slugs, batches=batches)

        monkeypatch.setattr(queue_cls, "seed", _spy_seed)

    def _fake_wait(container_ids, console, on_poll=None):
        queue = queues[-1]
        index = len(queues) - 1
        outcomes = outcomes_per_pass[index] if index < len(outcomes_per_pass) else {}
        for task_id in state["seeded"][-1]:
            members = (state["batches"][-1] or {}).get(task_id, [task_id])
            for slug in members:
                if slug not in outcomes:
                    continue  # never started
                queue.write_result(
                    ScenarioResult(
                        slug=slug,
                        worker_id="w1",
                        exit_code=outcomes[slug],
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
    defaults: dict = {
        "workers": 4,
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
        "retry_pass": 0,
    }
    defaults.update(kwargs)
    return coordinator.run_parallel(**defaults)


# ── Seeding ─────────────────────────────────────────────────────────────────


def test_a_group_is_seeded_as_one_task(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, {"a": "billing", "b": "billing", "c": None})
    state = _neuter(monkeypatch, outcomes_per_pass=[{"a": 0, "b": 0, "c": 0}])

    assert _run() == 0
    assert sorted(state["seeded"][0]) == ["billing", "c"]
    assert state["batches"][0] == {"billing": ["a", "b"]}


def test_no_groups_seeds_bare_slugs_and_passes_no_batches(tmp_path, monkeypatch):
    """Inertness at the call site: the keyword is omitted entirely, which is what
    keeps every existing ``Queue.seed`` spy — declared ``(self, slugs)`` — working."""
    _project(tmp_path, monkeypatch, {"a": None, "b": None})
    state = _neuter(monkeypatch, outcomes_per_pass=[{"a": 0, "b": 0}])

    assert _run() == 0
    assert sorted(state["seeded"][0]) == ["a", "b"]
    assert state["batches"][0] is None


def test_colliding_task_ids_abort_the_run(tmp_path, monkeypatch):
    """A group named after an UNGROUPED scenario yields two tasks with one id.
    Merging them would drop a scenario silently, so this must be an error."""
    _project(tmp_path, monkeypatch, {"checkout": None, "refund": "checkout"})
    _neuter(monkeypatch, outcomes_per_pass=[{}])

    assert _run() == 1


def test_a_group_named_after_its_own_member_is_allowed(tmp_path, monkeypatch):
    """`exclusive: checkout` on a scenario slugged `checkout` is the most natural
    name there is: it yields ONE task id, so it must not be rejected."""
    _project(tmp_path, monkeypatch, {"checkout": "checkout", "refund": "checkout"})
    state = _neuter(monkeypatch, outcomes_per_pass=[{"checkout": 0, "refund": 0}])

    assert _run() == 0
    assert state["seeded"][0] == ["checkout"]


# ── The collapse must still run (D1) ────────────────────────────────────────


def test_full_collapse_runs_on_one_stack_instead_of_exiting_zero(tmp_path, monkeypatch):
    """Every scenario in one group is the most obvious first use of the feature.
    Capping workers at the task count makes ``effective_workers < 2`` reachable,
    and the branch there used to ``return 0`` — reporting success for a run that
    executed nothing."""
    _project(tmp_path, monkeypatch, {"a": "db", "b": "db", "c": "db"})
    state = _neuter(monkeypatch, outcomes_per_pass=[{"a": 0, "b": 0, "c": 0}])

    assert _run(workers=4) == 0
    assert state["stacks"] == [1], "one stack, because there is one claimable task"
    assert state["seeded"][0] == ["db"]


def test_full_collapse_still_reports_a_failure(tmp_path, monkeypatch):
    """The corollary: having fallen through, the run's exit code must reflect its
    results rather than the early-return's unconditional 0."""
    _project(tmp_path, monkeypatch, {"a": "db", "b": "db"})
    _neuter(monkeypatch, outcomes_per_pass=[{"a": 0, "b": 1}])

    assert _run(workers=4) == 1


# ── Nothing may be silently skipped (D5) ────────────────────────────────────


def test_never_started_scenario_fails_the_run(tmp_path, monkeypatch):
    """A task with no claim file and no result is invisible to orphan detection
    and to result collection alike, so without an explicit backstop the run would
    report success having executed nothing."""
    _project(tmp_path, monkeypatch, {"a": None, "b": None})
    _neuter(monkeypatch, outcomes_per_pass=[{"a": 0}])  # b never runs

    assert _run(workers=2) == 1


def test_never_started_fails_even_when_a_retry_verdict_exists(tmp_path, monkeypatch):
    """The verdict branch deliberately ignores worker exit codes, so the backstop
    has to be applied there too or a never-run scenario rides out green."""
    _project(tmp_path, monkeypatch, {"a": None, "b": None})
    _neuter(monkeypatch, outcomes_per_pass=[{"a": 1}, {"a": 0}])  # b never runs, ever

    assert _run(workers=2, retry_pass=1) == 1


def test_never_started_then_recovered_is_not_flaky(tmp_path, monkeypatch, capsys):
    """A scenario that never ran and then passed is not flaky. Folding it into
    ``failed_slugs`` would mark it so and poison the append-only history that
    ``steppy flaky`` ranks from — for a scenario that is not even faulty."""
    _project(tmp_path, monkeypatch, {"a": None, "b": None})
    _neuter(monkeypatch, outcomes_per_pass=[{"a": 0}, {"b": 0}])  # b never started, then passed

    assert _run(workers=2, retry_pass=1) == 0
    assert "Flaky" not in capsys.readouterr().out


# ── Retry regrouping ────────────────────────────────────────────────────────


def test_retry_pass_regroups_the_exclusion_group(tmp_path, monkeypatch):
    """``retry.workers`` defaults to 1 but is configurable, so a retry that seeded
    the failed members as separate tasks would run them concurrently — on exactly
    the scenarios most likely to be contention-sensitive."""
    _project(tmp_path, monkeypatch, {"a": "billing", "b": "billing"})
    state = _neuter(monkeypatch, outcomes_per_pass=[{"a": 1, "b": 1}, {"a": 0, "b": 0}])

    assert _run(workers=2, retry_pass=1) == 0
    assert state["seeded"][1] == ["billing"], "the retry re-seeds ONE task, not two"
    assert state["batches"][1] == {"billing": ["a", "b"]}


# ── Longest-first ordering over tasks ───────────────────────────────────────


def test_task_ordering_weights_a_batch_by_the_sum_of_its_members(tmp_path, monkeypatch, capsys):
    """A batch is one scheduling unit, so a group of several short scenarios can
    outweigh a single long one — otherwise a large group claimed last becomes the
    run's tail while every other worker idles."""
    from rich.console import Console

    tasks = [
        Task(task_id="long", slugs=("long",), group=None),
        Task(task_id="g", slugs=("g1", "g2", "g3"), group="g"),
    ]
    monkeypatch.setattr(
        "steppy.history.median_durations",
        lambda project_root: {"long": 50.0, "g1": 20.0, "g2": 20.0, "g3": 20.0},
    )
    ordered = coordinator._order_tasks_longest_first(tasks, tmp_path, Console(force_terminal=False, no_color=True))
    assert [t.task_id for t in ordered] == ["g", "long"]


def test_task_ordering_is_a_no_op_without_history(tmp_path, monkeypatch):
    from rich.console import Console

    tasks = [Task("a", ("a",)), Task("b", ("b",))]
    monkeypatch.setattr("steppy.history.median_durations", lambda project_root: {})
    assert (
        coordinator._order_tasks_longest_first(tasks, tmp_path, Console(force_terminal=False, no_color=True)) == tasks
    )
