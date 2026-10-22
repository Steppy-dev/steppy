"""Filesystem work-stealing queue for parallel scenario execution.

Layout under the run's tmp dir (mounted into every worker at /steppy/coord):
    queue/<task_id>.task     — marker, written by the coordinator; never moved
    claims/<task_id>.claim   — atomic O_EXCL create by a worker; content = worker_id
    results/<worker_id>/<slug>.json — per-SCENARIO results, written by workers

A task is one scenario by default, and ``task_id`` is then the bare slug and the
marker file is empty — exactly as it has always been. An **exclusion group**
(frontmatter ``exclusive: <name>``) collapses its members into a single task whose
id is the group name and whose marker carries one line of JSON naming its members;
the worker claiming it runs them sequentially, one pytest process each. That is the
whole mechanism: mutual exclusion is a property of how the queue was seeded, not a
run-time protocol, so there is no lock to leak and no worker ever waits on another.

Results, logs and JUnit stay keyed **per scenario** even for a batch, which is what
lets every downstream consumer (orphan detection, retry classification, failure
artifacts, history, the renderer) keep its per-slug key unchanged.

Claiming is atomic via ``os.open(claim_path, O_WRONLY|O_CREAT|O_EXCL)``,
which is atomic on both POSIX and Windows (battle-tested mail-spool
pattern). When two workers race for the same task, exactly one O_EXCL
succeeds and the other gets ``FileExistsError`` and tries the next
candidate.

We chose O_EXCL over the rename-claim variant because ``os.rename`` on
Windows under heavy concurrency does not provide the source-side mutual
exclusion we need (observed ~20% double-claim rate in the test).

Workers loop:

    while True:
        task = queue.claim_task(worker_id)
        if task is None:
            break  # queue empty
        for slug in task.slugs:   # exactly one, unless it is an exclusion group
            result = run_scenario(slug)
            queue.write_result(result)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..exclusive import Task


TASK_SUFFIX = ".task"
CLAIM_SUFFIX = ".claim"
RESULT_SUFFIX = ".json"

# Version stamped into a batch marker's payload. Ungrouped tasks carry no payload
# at all, so this only ever appears in a queue that declares an exclusion group.
TASK_PAYLOAD_VERSION = 1

# Worker key used to report a batch whose claim file was created but never written
# — a worker killed between the claim's ``os.open`` and its ``os.write``. Its real
# id is unrecoverable, and every member is unstarted by construction (the id is
# written before any scenario runs), so they are all orphans of nobody.
UNKNOWN_WORKER = "(unknown)"


class TaskPayloadError(Exception):
    """A batch marker exists but could not be read or parsed.

    The claim already succeeded when this is raised, so the task is claimed with no
    results: its members surface as orphans and are retried. The worker reports it
    and moves on rather than dying, because one unreadable marker must not cost the
    rest of the queue.
    """

    def __init__(self, task_id: str, reason: str):
        super().__init__(f"unreadable task payload for {task_id!r}: {reason}")
        self.task_id = task_id
        self.reason = reason


# Task files are named ``NNNN-<slug>.task``. Claiming walks the directory in
# sorted order, so the prefix is what makes the queue honour the order the
# coordinator seeded rather than the alphabet — which lets it put the slowest
# scenarios first (LPT) so no worker is left running a 3-minute scenario alone
# after everyone else has finished. Claim/result files stay keyed by the bare
# slug, so nothing downstream has to know about this.
_ORDER_PREFIX_RE = re.compile(r"^\d{4}-")
_ORDER_PREFIX_WIDTH = 4


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario's outcome, written to results/<worker_id>/<slug>.json."""

    slug: str
    worker_id: str
    exit_code: int
    duration_s: float
    started_at: float  # epoch seconds
    finished_at: float

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "worker_id": self.worker_id,
            "exit_code": self.exit_code,
            "duration_s": self.duration_s,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.exit_code == 0,
        }


class Queue:
    """Filesystem-backed work-stealing queue.

    The coordinator instantiates one Queue, calls ``seed(slugs)`` to populate
    queue/, then bind-mounts the parent dir into every worker container.
    Each worker instantiates a Queue against the in-container path and
    loops on ``claim()``. queue/, claims/, results/ are siblings under
    ``root`` on both sides — the coordinator writes to host:<tmp>/queue/,
    the worker sees the same files at /steppy/coord/queue/.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.queue_dir = self.root / "queue"
        self.claims_dir = self.root / "claims"
        self.results_dir = self.root / "results"
        # task_id -> member slugs, for the batches this Queue seeded. Held as
        # instance state rather than passed to each reader on purpose: orphan
        # detection has three call sites, and a reader that forgot the map would
        # report a group NAME as a failed scenario and retry a test file that does
        # not exist. State cannot be forgotten; a parameter can.
        self._task_slugs: dict[str, list[str]] = {}

    def init_dirs(self) -> None:
        """Create the queue/claims/results directory skeleton."""
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def seed(self, slugs: list[str], *, batches: Optional[dict[str, list[str]]] = None) -> None:
        """Coordinator-only: write one task file per queue entry.

        ``slugs`` is a list of **task ids** — bare scenario slugs unless an entry
        names an exclusion group. Task files carry a zero-padded ordinal prefix so
        ``claim()``'s sorted walk hands them out in **the order given**, not
        alphabetically. The coordinator uses that to seed longest-first from
        recorded durations; callers that don't care can keep passing any order.

        ``batches`` maps a task id to its member slugs and is written into the
        marker as one line of JSON. Ids absent from it keep today's empty
        ``touch()``, so a run with no exclusion group produces a byte-identical
        queue directory. Callers pass ``batches`` only when it is non-empty.
        """
        self.init_dirs()
        batches = batches or {}
        self._task_slugs = {task_id: list(members) for task_id, members in batches.items()}
        for index, task_id in enumerate(slugs):
            path = self.queue_dir / f"{index:0{_ORDER_PREFIX_WIDTH}d}-{task_id}{TASK_SUFFIX}"
            members = batches.get(task_id)
            if members:
                payload = {"v": TASK_PAYLOAD_VERSION, "group": task_id, "slugs": list(members)}
                path.write_text(json.dumps(payload), encoding="utf-8")
            else:
                path.touch()

    @staticmethod
    def _slug_from_task(name: str) -> str:
        """``0007-checkout.task`` → ``checkout``. Tolerates un-prefixed names so
        a queue dir seeded by an older steppy still drains.

        Returns the *task id*, which is a scenario slug unless the entry is a
        batch, in which case it is the exclusion group's name."""
        stem = name[: -len(TASK_SUFFIX)] if name.endswith(TASK_SUFFIX) else name
        return _ORDER_PREFIX_RE.sub("", stem, count=1)

    def _read_task(self, entry: Path, task_id: str) -> Task:
        """Turn a claimed marker file into a :class:`Task`.

        Read **after** the claim succeeds: reading first would waste a read on
        every lost race, and the file is written once at seed time and never
        mutated, so reading it later is race-free.
        """
        try:
            raw = entry.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise TaskPayloadError(task_id, str(e))
        if not raw:
            return Task(task_id=task_id, slugs=(task_id,), group=None)
        try:
            payload = json.loads(raw)
            members = [str(s) for s in payload["slugs"]]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise TaskPayloadError(task_id, str(e))
        if not members:
            raise TaskPayloadError(task_id, "payload declares no scenarios")
        return Task(task_id=task_id, slugs=tuple(members), group=str(payload.get("group") or task_id))

    def claim_task(self, worker_id: str) -> Optional[Task]:
        """Worker-only: atomically claim the next pending task via O_EXCL on
        a per-task claim file. Returns the :class:`Task`, or None if the
        queue is empty for this worker.

        Tasks are seeded once by the coordinator before any worker runs and
        are never removed (task files stay put for orphan detection). A
        single pass through the snapshot is therefore sufficient: if every
        candidate raises ``FileExistsError`` then every queued task has
        already been claimed by another worker and there is nothing for us
        to do — return ``None`` so the caller exits.

        Raises :class:`TaskPayloadError` when a batch marker cannot be read; the
        claim has already succeeded by then, so the caller reports it and moves on
        and the members surface as orphans.
        """
        self.init_dirs()
        try:
            candidates = sorted(e for e in self.queue_dir.iterdir() if e.name.endswith(TASK_SUFFIX))
        except FileNotFoundError:
            return None
        for entry in candidates:
            task_id = self._slug_from_task(entry.name)
            claim_path = self.claims_dir / f"{task_id}{CLAIM_SUFFIX}"
            try:
                fd = os.open(
                    str(claim_path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                )
            except FileExistsError:
                continue  # another worker got it; try the next candidate
            try:
                os.write(fd, worker_id.encode("utf-8"))
            finally:
                os.close(fd)
            return self._read_task(entry, task_id)
        return None  # every candidate already claimed → drained for us

    def claim(self, worker_id: str) -> Optional[str]:
        """Claim the next pending task and return its **task id**.

        A thin wrapper over :meth:`claim_task` so there is one implementation of
        the claim protocol and no drift between them.
        """
        task = self.claim_task(worker_id)
        return task.task_id if task is not None else None

    def write_result(self, result: ScenarioResult) -> None:
        """Worker-only: persist the result to results/<worker_id>/<slug>.json."""
        worker_results_dir = self.results_dir / result.worker_id
        worker_results_dir.mkdir(parents=True, exist_ok=True)
        result_path = worker_results_dir / f"{result.slug}{RESULT_SUFFIX}"
        result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")

    def unclaimed_tasks(self) -> set[str]:
        """Task ids that were queued and never claimed by anybody.

        Reachable only when every worker died with work left — but nothing else in
        the system observes it: ``orphaned_running_tasks`` walks ``claims/`` and
        ``collect_results`` walks ``results/``, so a task with neither file is
        invisible to both. Without this the run can report success having silently
        executed nothing.
        """
        try:
            queued = {self._slug_from_task(p.name) for p in self.queue_dir.iterdir() if p.suffix == TASK_SUFFIX}
        except FileNotFoundError:
            return set()
        try:
            claimed = {p.stem for p in self.claims_dir.iterdir() if p.suffix == CLAIM_SUFFIX}
        except FileNotFoundError:
            claimed = set()
        return queued - claimed

    def _reported_slugs(self) -> set[str]:
        """Every scenario that produced a result, by filename — no JSON parsing."""
        if not self.results_dir.exists():
            return set()
        return {p.stem for p in self.results_dir.glob(f"*/*{RESULT_SUFFIX}")}

    def unclaimed_slugs(self) -> list[str]:
        """Scenarios with no evidence they ever ran.

        :meth:`unclaimed_tasks` expanded through the batch map, minus anything
        that reported a result. A result implies a claim in the real protocol
        (the claim file is created first), so subtracting them changes nothing
        about a live run — but it makes this incapable of a false positive, and
        this set forces the run to fail.
        """
        reported = self._reported_slugs()
        out: list[str] = []
        for task_id in sorted(self.unclaimed_tasks()):
            out.extend(s for s in (self._task_slugs.get(task_id) or [task_id]) if s not in reported)
        return out

    def is_empty(self) -> bool:
        """Coordinator-only: True when no pending tasks remain (every queued
        task has at least been claimed)."""
        return not self.unclaimed_tasks()

    def collect_results(self) -> list[ScenarioResult]:
        """Coordinator-only: read all results written so far."""
        out: list[ScenarioResult] = []
        if not self.results_dir.exists():
            return out
        for worker_dir in self.results_dir.iterdir():
            if not worker_dir.is_dir():
                continue
            for result_file in worker_dir.glob(f"*{RESULT_SUFFIX}"):
                try:
                    data = json.loads(result_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                out.append(
                    ScenarioResult(
                        slug=data["slug"],
                        worker_id=data["worker_id"],
                        exit_code=int(data["exit_code"]),
                        duration_s=float(data["duration_s"]),
                        started_at=float(data["started_at"]),
                        finished_at=float(data["finished_at"]),
                    )
                )
        return out

    def _assert_batch_map_present(self) -> None:
        """Fail loudly if this Queue holds batch markers it cannot interpret.

        A reader that expands claim stems without the map would report an exclusion
        group's NAME as a failed scenario, retry a test file that does not exist,
        and never retry the group's real members. That is a silent, plausible-looking
        wrong answer, so turn the only way it can arise — querying a Queue instance
        that did not seed this directory — into an immediate error.
        """
        if self._task_slugs:
            return
        try:
            entries = [p for p in self.queue_dir.iterdir() if p.name.endswith(TASK_SUFFIX)]
        except FileNotFoundError:
            return
        batched = sorted(self._slug_from_task(p.name) for p in entries if p.stat().st_size > 0)
        if batched:
            raise RuntimeError(
                f"queue at {self.root} holds exclusion-group task(s) {batched} but this "
                "Queue instance has no batch map. Read it through the Queue that seeded "
                "it, or re-seed with `batches=`."
            )

    def orphaned_running_tasks(self) -> dict[str, list[str]]:
        """Coordinator-only: scenarios claimed but never reported (worker crashed
        before writing the result file). Returns ``{worker_id: [slug, ...]}``.

        Always **scenario slugs**, never task ids: a claimed batch is expanded
        through the map, so a worker that died after two of three members still
        reports exactly the one that did not finish. A stem with no map entry is a
        plain scenario and takes the original path verbatim.

        v0.1: these are reported as failures, not requeued.
        """
        orphans: dict[str, list[str]] = {}
        if not self.claims_dir.exists():
            return orphans
        self._assert_batch_map_present()
        for claim_file in self.claims_dir.glob(f"*{CLAIM_SUFFIX}"):
            task_id = claim_file.stem
            members = self._task_slugs.get(task_id)
            try:
                worker_id = claim_file.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not worker_id:
                # Killed between the claim's create and its write. For a single
                # scenario that stays today's skip; for a batch, skipping would
                # lose every member in silence — and the owner id is written
                # before any scenario runs, so none of them started.
                if members:
                    orphans.setdefault(UNKNOWN_WORKER, []).extend(members)
                continue
            for slug in members if members else [task_id]:
                result_path = self.results_dir / worker_id / f"{slug}{RESULT_SUFFIX}"
                if not result_path.exists():
                    orphans.setdefault(worker_id, []).append(slug)
        # Keep slugs sorted for deterministic test output.
        return {wid: sorted(slugs) for wid, slugs in orphans.items()}
