"""Regression tests for the coordinator's worker-wait loop.

The historical bug: ``docker compose ps -q steppy-worker`` (without ``-a``)
hides containers in ``Status=exited``. After a worker drained the queue
and exited cleanly, ``_get_worker_container_id`` returned ``None``
forever and ``_wait_for_workers`` spun in a silent infinite loop. Users
saw the live renderer sit there with every worker row stuck on "idle".

Fix: pass ``-a`` to the ps invocation AND cache the container ID once at
startup so the wait loop polls ``docker inspect`` directly. These tests
pin both behaviors with a mocked ``subprocess.run`` — Docker is not
required.
"""

from __future__ import annotations

import json

from steppy import coordinator


def test_get_worker_container_id_includes_all_flag(monkeypatch):
    """The fix: ``ps -q`` must use ``-a`` so exited workers are returned."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))

        class R:
            stdout = "abc123\n"
            returncode = 0

        return R()

    monkeypatch.setattr(coordinator.subprocess, "run", fake_run)
    cid = coordinator._get_worker_container_id("steppy-run-foo-w1")
    assert cid == "abc123"
    assert len(calls) == 1
    assert "-a" in calls[0]
    # And the rest of the shape is preserved.
    assert calls[0][:4] == ["docker", "compose", "-p", "steppy-run-foo-w1"]
    assert calls[0][-1] == "steppy-worker"


def test_resolve_worker_container_ids_returns_map(monkeypatch):
    """Container IDs are resolved once per project; map is keyed by project name."""
    by_project = {"p1": "cid-1", "p2": "cid-2"}

    def fake_run(cmd, **kw):
        project = cmd[cmd.index("-p") + 1]

        class R:
            stdout = by_project[project] + "\n"
            returncode = 0

        return R()

    monkeypatch.setattr(coordinator.subprocess, "run", fake_run)
    out = coordinator._resolve_worker_container_ids(["p1", "p2"])
    assert out == {"p1": "cid-1", "p2": "cid-2"}


def test_resolve_worker_container_ids_fails_fast_on_missing(monkeypatch):
    """If any project can't be resolved, raise ``_StackError`` immediately —
    silently looping forever (the pre-fix behavior) is far worse."""

    def fake_run(cmd, **kw):
        class R:
            stdout = ""  # not visible → None from _get_worker_container_id
            returncode = 0

        return R()

    monkeypatch.setattr(coordinator.subprocess, "run", fake_run)
    import pytest

    with pytest.raises(coordinator._StackError):
        coordinator._resolve_worker_container_ids(["p1"])


def test_wait_for_workers_polls_via_cached_ids(monkeypatch):
    """``_wait_for_workers`` should poll ``docker inspect`` on the cached
    container IDs, not call ``compose ps`` per poll. We verify by ensuring
    no ``compose ps`` lookup runs from inside the wait loop."""
    states = {
        "cid-1": [
            {"Status": "running"},
            {"Status": "exited", "ExitCode": 0},
        ],
        "cid-2": [
            {"Status": "exited", "ExitCode": 1},
        ],
    }
    call_log: list[list[str]] = []

    def fake_run(cmd, **kw):
        call_log.append(list(cmd))

        class R:
            returncode = 0

        if cmd[0] == "docker" and cmd[1] == "inspect":
            cid = cmd[-1]
            next_state = states[cid].pop(0) if states[cid] else {"Status": "exited", "ExitCode": 0}
            R.stdout = json.dumps(next_state)
            return R
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")

    monkeypatch.setattr(coordinator.subprocess, "run", fake_run)
    # Skip the sleep — we don't want the test to wait real seconds.
    monkeypatch.setattr(coordinator.time, "sleep", lambda *_a, **_kw: None)

    from rich.console import Console

    exit_codes = coordinator._wait_for_workers({"p1": "cid-1", "p2": "cid-2"}, Console(quiet=True))
    assert exit_codes == [0, 1]
    # Sanity: no compose ps calls happened from inside the loop.
    for cmd in call_log:
        assert "ps" not in cmd, f"unexpected ps call inside wait loop: {cmd}"
