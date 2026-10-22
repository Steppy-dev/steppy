"""Circuit breaker: abort a parallel run once nearly everything is failing.

When the app is down, auth is misconfigured, or a seed step broke, every
scenario fails and the user pays the full wall clock to find out. The breaker
watches results arrive and aborts early.

Both thresholds are load-bearing and are tested as such: the ratio alone would
trip on the first failed scenario of a run, and the count alone would trip on a
suite that is merely red. A breaker with a reputation for false positives gets
turned off, which is why the default is conservative.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from steppy import coordinator
from steppy._parallel.queue import Queue, ScenarioResult
from steppy.models import CircuitBreakerConfig


def _queue_with(tmp_path: Path, outcomes: list[int]) -> Queue:
    """A queue holding one result per entry in ``outcomes`` (exit codes)."""
    q = Queue(root=tmp_path)
    slugs = [f"s{i}" for i in range(len(outcomes))]
    q.seed(slugs)
    for slug, code in zip(slugs, outcomes):
        q.write_result(
            ScenarioResult(
                slug=slug,
                worker_id="w1",
                exit_code=code,
                duration_s=1.0,
                started_at=0.0,
                finished_at=1.0,
            )
        )
    return q


def _breaker(tmp_path, outcomes, *, cfg=None, fail_fast=None):
    return coordinator._make_circuit_breaker(
        cfg or CircuitBreakerConfig(),
        fail_fast,
        _queue_with(tmp_path, outcomes),
        Console(force_terminal=False, no_color=True),
    )


def test_trips_when_everything_failed(tmp_path):
    check = _breaker(tmp_path, [1] * 10)
    with pytest.raises(coordinator._CircuitBreakerTripped) as exc:
        check()
    assert "10/10" in str(exc.value)


def test_does_not_trip_below_min_completed(tmp_path):
    """9 of 9 failed is 100% — but too little evidence. Waiting one more
    scenario costs seconds; a false abort costs trust."""
    _breaker(tmp_path, [1] * 9)()  # no raise


def test_does_not_trip_below_fail_ratio(tmp_path):
    """A merely-red suite (50% failing) must run to completion."""
    _breaker(tmp_path, [1] * 10 + [0] * 10)()  # no raise


def test_trips_at_exactly_the_threshold(tmp_path):
    """9 failed of 10 = 90%, which is `>=` the default ratio."""
    with pytest.raises(coordinator._CircuitBreakerTripped):
        _breaker(tmp_path, [1] * 9 + [0])()


def test_disabled_by_config_returns_no_callback(tmp_path):
    assert _breaker(tmp_path, [1] * 20, cfg=CircuitBreakerConfig(enabled=False)) is None


def test_cli_no_fail_fast_overrides_enabled_config(tmp_path):
    assert _breaker(tmp_path, [1] * 20, fail_fast=False) is None


def test_cli_fail_fast_overrides_disabled_config(tmp_path):
    check = _breaker(tmp_path, [1] * 20, cfg=CircuitBreakerConfig(enabled=False), fail_fast=True)
    assert check is not None
    with pytest.raises(coordinator._CircuitBreakerTripped):
        check()


def test_custom_thresholds_are_honoured(tmp_path):
    cfg = CircuitBreakerConfig(min_completed=3, fail_ratio=0.5)
    with pytest.raises(coordinator._CircuitBreakerTripped):
        _breaker(tmp_path, [1, 1, 0], cfg=cfg)()


def test_wait_loop_propagates_the_trip(monkeypatch):
    """The breaker rides on _wait_for_workers' poll cycle — if the hook isn't
    called while workers are pending, the feature silently does nothing."""
    monkeypatch.setattr(coordinator, "_inspect_container_state", lambda cid: {"Status": "running"})
    monkeypatch.setattr(coordinator.time, "sleep", lambda s: None)

    def _boom():
        raise coordinator._CircuitBreakerTripped("nope")

    with pytest.raises(coordinator._CircuitBreakerTripped):
        coordinator._wait_for_workers(
            {"p1": "cid-1"},
            Console(force_terminal=False, no_color=True),
            on_poll=_boom,
        )


def test_wait_loop_without_hook_is_unchanged(monkeypatch):
    """No breaker ⇒ the historic wait behaviour, exit codes and all."""
    monkeypatch.setattr(coordinator, "_inspect_container_state", lambda cid: {"Status": "exited", "ExitCode": 0})
    codes = coordinator._wait_for_workers({"p1": "cid-1"}, Console(force_terminal=False, no_color=True))
    assert codes == [0]
