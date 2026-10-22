"""Render progress to the user.

Two renderers consume :class:`EventBus` state — the choice depends on
whether stdout is a TTY:

* :class:`LiveProgressRenderer` (TTY) — Rich ``Live`` sticky overlay with
  a single-line aggregate (progress bar + counters) on top and a
  per-worker table below. Refreshes at 4 Hz.
* :class:`MilestoneRenderer` (non-TTY / CI / piped output) — subscribes
  to the bus and prints one plain timestamped line per state change. No
  overwriting, safe to redirect to a file.

:func:`render_summary` is a one-shot final summary printed after the
workers exit, regardless of which live renderer was used.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .event_bus import EventBus, EventBusSnapshot, WorkerState
from .queue import ScenarioResult


_STALLED_THRESHOLD_S = 30.0
_ICONS = {
    "passed": "[green]✓[/green]",
    "failed": "[red]✗[/red]",
    "skipped": "[yellow]⊘[/yellow]",
    "error": "[red]⚠[/red]",
    "interrupted": "[yellow]⛔[/yellow]",
    "worker_died": "[red]☠[/red]",
}


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h{mins:02d}m"


def _aggregate_done(snap: EventBusSnapshot) -> int:
    return sum(snap.counts.get(k, 0) for k in ("passed", "failed", "skipped", "error", "interrupted", "worker_died"))


def _running_count(snap: EventBusSnapshot) -> int:
    return sum(1 for ws in snap.workers.values() if ws.current_test is not None and not ws.finished)


# ── Live (TTY) ──────────────────────────────────────────────────────────────


class LiveProgressRenderer:
    """Rich ``Live`` overlay refreshed at 4 Hz.

    Top row: aggregate counters (done · failed · running · elapsed).
    Bottom: per-worker table (worker · status · scenario · test · elapsed).
    """

    def __init__(self, bus: EventBus, console: Console, total_scenarios: int):
        self._bus = bus
        self._console = console
        self._total_scenarios = total_scenarios
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._live: Optional[Live] = None
        self._started_at = time.time()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="live-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            with Live(self._render(), console=self._console, refresh_per_second=4, transient=False) as live:
                self._live = live
                while not self._stop.wait(0.25):
                    live.update(self._render())
                # Final paint after stop so the last frame reflects reality.
                live.update(self._render())
        except Exception:
            pass

    def _render(self) -> Group:
        snap = self._bus.snapshot()
        return Group(self._header(snap), self._worker_table(snap))

    def _header(self, snap: EventBusSnapshot) -> Text:
        done = _aggregate_done(snap)
        failed = snap.counts.get("failed", 0) + snap.counts.get("error", 0) + snap.counts.get("worker_died", 0)
        running = _running_count(snap)
        elapsed = time.time() - self._started_at
        scenarios_finished = sum(1 for s in snap.scenarios if s.finished_at is not None)
        bar_width = 24
        if self._total_scenarios > 0:
            filled = min(bar_width, int(bar_width * scenarios_finished / self._total_scenarios))
        else:
            filled = 0
        bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
        text = Text()
        text.append(f"{bar} ", style="cyan")
        text.append(f"{scenarios_finished}/{self._total_scenarios}", style="bold")
        text.append(" scenarios · ")
        text.append(f"{done}", style="bold")
        text.append(" tests done · ")
        if failed:
            text.append(f"{failed} failed", style="bold red")
        else:
            text.append("0 failed", style="dim")
        text.append(" · ")
        text.append(f"{running} running", style="cyan" if running else "dim")
        text.append(" · ")
        text.append(_fmt_elapsed(elapsed), style="dim")
        return text

    def _worker_table(self, snap: EventBusSnapshot) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(justify="right", no_wrap=True, style="dim")
        now = time.time()
        for worker_id in sorted(snap.workers.keys()):
            ws = snap.workers[worker_id]
            status, scenario, test, elapsed = self._worker_row(ws, now)
            table.add_row(worker_id, status, scenario, test, elapsed)
        return table

    @staticmethod
    def _worker_row(ws: WorkerState, now: float) -> tuple[str, str, str, str]:
        if ws.finished and ws.current_test is None:
            return "[dim]done[/dim]", "", "", ""
        if ws.current_test is not None:
            elapsed = now - (ws.current_test_started_at or now)
            stalled = elapsed > _STALLED_THRESHOLD_S
            status = "[yellow dim]stalled[/yellow dim]" if stalled else "[green]running[/green]"
            return status, ws.current_scenario or "-", _short_nodeid(ws.current_test), _fmt_elapsed(elapsed)
        if ws.current_scenario is not None:
            elapsed = now - (ws.scenario_started_at or now)
            return "[cyan]setup[/cyan]", ws.current_scenario, "(awaiting test-start)", _fmt_elapsed(elapsed)
        return "[dim]idle[/dim]", "", "", ""


def _short_nodeid(nodeid: str) -> str:
    """Strip the file path from a pytest nodeid for compact display.

    ``tests/e2e/test_login.py::test_login[editor-fr]`` → ``test_login[editor-fr]``.
    """
    if "::" in nodeid:
        return nodeid.split("::", 1)[1]
    return nodeid


# ── Milestone (non-TTY) ─────────────────────────────────────────────────────


class MilestoneRenderer:
    """Subscribe to the bus and print one line per significant state change.

    No ANSI overwriting, no Rich Live. Safe to ``tee`` / redirect / read in
    CI logs. Output is deterministic given the event stream, modulo
    wall-clock timestamps.
    """

    def __init__(self, bus: EventBus, console: Console, total_scenarios: int):
        self._bus = bus
        self._console = console
        self._total_scenarios = total_scenarios
        bus.subscribe(self._on_event)

    def start(self) -> None:
        # Nothing to start — we're purely event-driven.
        pass

    def stop(self) -> None:
        # No background work to stop.
        pass

    def _on_event(self, worker_id: str, kind: str, snap: EventBusSnapshot) -> None:
        if kind == "scenario-start":
            ws = snap.workers.get(worker_id)
            if ws is None or ws.current_scenario is None:
                return
            self._line(f"{worker_id} START scenario {ws.current_scenario}", snap)
        elif kind == "scenario-end":
            # Find the matching scenario record by worker_id (newest unfinished).
            rec = next(
                (s for s in reversed(snap.scenarios) if s.worker_id == worker_id and s.finished_at is not None),
                None,
            )
            if rec is None:
                return
            duration = (rec.finished_at - rec.started_at) if rec.finished_at else 0.0
            tag = "PASS" if rec.exit_code == 0 else f"FAIL(exit={rec.exit_code})"
            color = "green" if rec.exit_code == 0 else "red"
            self._line(f"{worker_id} {tag} scenario {rec.slug} ({_fmt_elapsed(duration)})", snap, style=color)
        elif kind == "test-end":
            rec = next(
                (t for t in reversed(snap.tests) if t.worker_id == worker_id and t.finished_at is not None),
                None,
            )
            if rec is None or rec.outcome is None:
                return
            duration = (rec.finished_at - rec.started_at) if rec.finished_at else 0.0
            outcome_label = rec.outcome.upper()
            color = {
                "passed": "green",
                "failed": "red",
                "skipped": "yellow",
                "error": "red",
                "interrupted": "yellow",
                "worker_died": "red",
            }.get(rec.outcome, "white")
            self._line(
                f"{worker_id} {outcome_label} {_short_nodeid(rec.nodeid)} ({_fmt_elapsed(duration)})",
                snap,
                style=color,
            )

    def _line(self, body: str, snap: EventBusSnapshot, style: Optional[str] = None) -> None:
        done = _aggregate_done(snap)
        failed = snap.counts.get("failed", 0) + snap.counts.get("error", 0) + snap.counts.get("worker_died", 0)
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {body} · {done} done, {failed} failed"
        if style:
            self._console.print(line, style=style, highlight=False, markup=False)
        else:
            self._console.print(line, highlight=False, markup=False)


# ── Final summary ───────────────────────────────────────────────────────────


@dataclass
class _SummaryRow:
    slug: str
    worker_id: str
    exit_code: int
    duration_s: float
    fail_tails: list[tuple[str, str]]  # (nodeid, tail)


def render_summary(
    *,
    console: Console,
    bus: EventBus,
    scenario_results: list[ScenarioResult],
    orphans: dict[str, list[str]],
    tmp_dir,
    project_root=None,
    verdict=None,
    retry_buses=None,
    verbose: bool = False,
) -> None:
    """Print the final summary after every worker has exited.

    Pulls test-level outcomes from the bus and per-scenario exit codes
    from ``scenario_results`` (the queue's source of truth). Failures
    embed the b64-decoded longrepr tails captured during the run.

    When ``project_root`` is provided, the failure / orphan blocks point
    at the persistent ``output/failures/<slug>/`` artifact dirs written
    by :func:`steppy._parallel.failures.persist_failure_artifacts`
    instead of the tmp_dir (which gets wiped on exit).

    ``verdict`` (a ``retry.Verdict``) adds the FLAKY section and narrows the
    Failures block to scenarios that failed every attempt. ``retry_buses`` are
    the retry passes' event buses — the retry's fail tails live there, not in
    the main ``bus``. Both None ⇒ the pre-retry output.

    Failures are grouped by cause. ``verbose`` restores the old behaviour of
    dumping every scenario's tail in full.
    """
    snap = bus.snapshot()
    counts = snap.counts
    total_tests = sum(counts.values())
    passed_tests = counts.get("passed", 0)
    failed_tests = counts.get("failed", 0) + counts.get("error", 0)
    skipped_tests = counts.get("skipped", 0)
    interrupted = counts.get("interrupted", 0)
    worker_died = counts.get("worker_died", 0)

    flaky_slugs = set(verdict.flaky) if verdict else set()
    passed_scen = sum(1 for r in scenario_results if r.exit_code == 0 and r.slug not in flaky_slugs)
    failed_scen = sum(1 for r in scenario_results if r.exit_code != 0)
    orphan_count = sum(len(v) for v in orphans.values())
    wall = time.time() - snap.wall_clock_started

    console.print()
    console.rule("[bold]Run summary[/bold]")
    console.print(
        f"  scenarios:  [bold green]{passed_scen} passed[/bold green]"
        + (f", [bold yellow]{len(flaky_slugs)} flaky[/bold yellow]" if flaky_slugs else "")
        + (f", [bold red]{failed_scen} failed[/bold red]" if failed_scen else "")
        + (f", [yellow]{orphan_count} orphaned[/yellow]" if orphan_count else "")
        + f"  [dim]({len(scenario_results)} total)[/dim]"
    )
    console.print(
        f"  tests:      [bold green]{passed_tests} passed[/bold green]"
        + (f", [bold red]{failed_tests} failed[/bold red]" if failed_tests else "")
        + (f", [yellow]{skipped_tests} skipped[/yellow]" if skipped_tests else "")
        + (f", [yellow]{interrupted} interrupted[/yellow]" if interrupted else "")
        + (f", [red]{worker_died} worker-died[/red]" if worker_died else "")
        + f"  [dim]({total_tests} total)[/dim]"
    )
    console.print(f"  duration:   [bold]{_fmt_elapsed(wall)}[/bold] wall clock")

    # Per-scenario failure block. With a verdict, only scenarios that failed
    # EVERY attempt belong here — a flaky one ended green and gets its own
    # section, so it must not be listed as a failure.
    failure_rows = _build_failure_rows(bus, scenario_results, buses=retry_buses)
    if verdict is not None:
        real_failures = set(verdict.failed)
        failure_rows = [r for r in failure_rows if r.slug in real_failures]

    if failure_rows or orphans:
        if project_root is not None:
            note_dir = project_root / "output" / "failures"
            console.print(
                f"[dim]These artifacts persist after the run; tmp dir is cleaned up. ({note_dir})[/dim]",
                soft_wrap=True,
            )

    _render_flaky_block(console, verdict)

    if failure_rows:
        # Slugs whose tail is already shown in full by the causes section. In
        # verbose mode nothing is elided.
        exemplars = _render_failure_causes(console, failure_rows) if not verbose else set()
        console.print()
        console.rule("[bold red]Failures[/bold red]")
        for row in failure_rows:
            elide = row.slug in exemplars
            console.print(
                f"  [red]✗[/red] [bold]{row.slug}[/bold]  "
                f"[dim]{row.worker_id} · exit={row.exit_code} · {_fmt_elapsed(row.duration_s)}[/dim]"
                + ("  [dim](cause shown above)[/dim]" if elide else "")
            )
            if not elide:
                for nodeid, tail in row.fail_tails:
                    console.print(f"      [dim]{_short_nodeid(nodeid)}[/dim]")
                    for line in tail.splitlines():
                        console.print(f"        {line}", highlight=False, markup=False)
            artifacts_dir = project_root / "output" / "failures" / row.slug if project_root is not None else None
            if artifacts_dir is not None:
                console.print(f"      [cyan]artifacts:[/cyan] [bold]{artifacts_dir}[/bold]", soft_wrap=True)
                pytest_log = artifacts_dir / "pytest.log"
                summary_txt = artifacts_dir / "summary.txt"
                if pytest_log.exists():
                    console.print(f"        [dim]pytest log:[/dim] {pytest_log}", soft_wrap=True)
                if summary_txt.exists():
                    console.print(f"        [dim]summary:   [/dim] {summary_txt}", soft_wrap=True)
            else:
                stack_log = tmp_dir / f"{row.worker_id}-stack.log" if tmp_dir is not None else None
                if stack_log is not None:
                    console.print(f"      [dim]full log: {stack_log}[/dim]", soft_wrap=True)
            console.print()

    # Orphan block.
    if orphans:
        console.print()
        console.rule("[bold yellow]Orphaned (worker died mid-scenario)[/bold yellow]")
        for wid, slugs in orphans.items():
            for slug in slugs:
                console.print(f"  [yellow]⚠[/yellow] {wid}: [bold]{slug}[/bold]")
                if project_root is not None:
                    artifacts_dir = project_root / "output" / "failures" / slug
                    console.print(f"      [cyan]artifacts:[/cyan] [bold]{artifacts_dir}[/bold]", soft_wrap=True)
                    pytest_log = artifacts_dir / "pytest.log"
                    summary_txt = artifacts_dir / "summary.txt"
                    if pytest_log.exists():
                        console.print(f"        [dim]pytest log:[/dim] {pytest_log}", soft_wrap=True)
                    if summary_txt.exists():
                        console.print(f"        [dim]summary:   [/dim] {summary_txt}", soft_wrap=True)


def _render_flaky_block(console: Console, verdict) -> None:
    """List scenarios that failed then passed on retry.

    Kept separate from Failures on purpose: these ended green, so burying them
    in the failure list would make a clean run look broken — and burying them in
    the pass count would hide the thing worth fixing."""
    if verdict is None or not verdict.flaky:
        return
    console.print()
    console.rule("[bold yellow]Flaky (failed, then passed on retry)[/bold yellow]")
    for slug in verdict.flaky:
        attempts = verdict.attempts.get(slug)
        suffix = f"  [dim]passed on attempt {attempts}[/dim]" if attempts else ""
        console.print(f"  [yellow]~[/yellow] [bold]{slug}[/bold]{suffix}")
    console.print(
        "  [dim]These did not fail the build. Rank repeat offenders with: [bold]steppy flaky[/bold][/dim]",
        soft_wrap=True,
    )


def _render_failure_causes(console: Console, failure_rows: list[_SummaryRow]) -> set[str]:
    """Print failures grouped by root cause. Returns the set of slugs whose
    tail was shown in full (the per-group exemplars) so the caller can elide
    them from the detailed block.

    With one broken selector across forty scenarios, this is the difference
    between a three-line summary and forty stack traces.
    """
    from .fingerprint import group_failures

    groups = group_failures(failure_rows)
    if not groups:
        return set()

    console.print()
    plural = "s" if len(groups) != 1 else ""
    total = sum(g.count for g in groups)
    console.rule(f"[bold red]Failure causes[/bold red] [dim]({len(groups)} distinct cause{plural})[/dim]")
    exemplars: set[str] = set()
    for group in groups:
        console.print(
            f"  [red]×{group.count}[/red] [bold]{group.headline}[/bold]  [dim]({group.fingerprint})[/dim]",
            highlight=False,
        )
        slugs = group.slugs
        shown = ", ".join(slugs[:6]) + (f", +{len(slugs) - 6} more" if len(slugs) > 6 else "")
        console.print(f"      [dim]{shown}[/dim]", soft_wrap=True)
        exemplars.update(slugs[1:])  # keep the first scenario's full tail below
    if total > len(groups):
        console.print("  [dim]Repeat occurrences are elided below; --verbose prints every tail.[/dim]")
    return exemplars


def _build_failure_rows(
    bus: EventBus,
    scenario_results: list[ScenarioResult],
    buses=None,
) -> list[_SummaryRow]:
    """Build one row per failed scenario, with its captured fail tails.

    ``buses`` are additional event buses to harvest tails from — the retry pass
    runs on its own bus (a shared one would overwrite attempt 1's records), so
    without this a retried-and-still-failing scenario would print with no
    detail at all. Later buses win, so the tail shown is the final attempt's.
    """
    all_buses = [bus, *(buses or [])]
    # Group failure tails by (worker_id, scenario_slug).
    tails_by_scenario: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for b in all_buses:
        for t in b.snapshot().tests:
            if t.outcome in ("failed", "error", "worker_died") and t.fail_tail:
                tails_by_scenario.setdefault((t.worker_id, t.scenario_slug), []).append((t.nodeid, t.fail_tail))
    rows: list[_SummaryRow] = []
    for r in scenario_results:
        if r.exit_code == 0:
            continue
        tails = tails_by_scenario.get((r.worker_id, r.slug), [])
        rows.append(
            _SummaryRow(
                slug=r.slug,
                worker_id=r.worker_id,
                exit_code=r.exit_code,
                duration_s=r.duration_s,
                fail_tails=tails,
            )
        )
    return rows


def make_renderer(*, bus: EventBus, console: Console, total_scenarios: int):
    """Pick the renderer that fits this console (TTY → Live, else
    Milestone). Returned object has matching ``start()``/``stop()``."""
    if console.is_terminal:
        return LiveProgressRenderer(bus=bus, console=console, total_scenarios=total_scenarios)
    return MilestoneRenderer(bus=bus, console=console, total_scenarios=total_scenarios)
