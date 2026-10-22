"""Race-test for the filesystem work-stealing queue.

Spawn N threads claiming from the same queue concurrently. Assert each
task is claimed exactly once. Then assert results are aggregated correctly
and orphan detection works.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from steppy._parallel.queue import (
    UNKNOWN_WORKER,
    Queue,
    ScenarioResult,
    TaskPayloadError,
)


def test_seed_creates_task_files(tmp_path: Path):
    """Task files carry a zero-padded ordinal so the sorted walk in ``claim()``
    hands them out in SEED order (which the coordinator sets longest-first)."""
    q = Queue(root=tmp_path)
    q.seed(["scenario-a", "scenario-b", "scenario-c"])
    files = sorted(p.name for p in q.queue_dir.iterdir())
    assert files == ["0000-scenario-a.task", "0001-scenario-b.task", "0002-scenario-c.task"]


def test_claim_follows_seed_order_not_alphabetical(tmp_path: Path):
    """The reason the prefix exists: a slow scenario seeded first must be
    claimed first, even when its slug sorts last."""
    q = Queue(root=tmp_path)
    q.seed(["zzz-slowest", "aaa-quickest", "mmm-medium"])
    assert [q.claim("w1"), q.claim("w1"), q.claim("w1")] == ["zzz-slowest", "aaa-quickest", "mmm-medium"]


def test_claim_tolerates_unprefixed_task_files(tmp_path: Path):
    """A queue dir seeded by an older steppy (bare ``<slug>.task``) must still
    drain — the prefix is stripped only when present."""
    q = Queue(root=tmp_path)
    q.init_dirs()
    (q.queue_dir / "legacy-slug.task").touch()
    assert q.claim("w1") == "legacy-slug"
    assert (q.claims_dir / "legacy-slug.claim").exists()
    assert q.is_empty()


def test_claim_returns_slug_and_creates_claim_file(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["only-one"])
    slug = q.claim("w1")
    assert slug == "only-one"
    # Task file stays put (under its ordinal name); claim file appears with
    # worker_id as content and is keyed by the BARE slug.
    assert (q.queue_dir / "0000-only-one.task").exists()
    claim = q.claims_dir / "only-one.claim"
    assert claim.exists()
    assert claim.read_text(encoding="utf-8") == "w1"


def test_claim_returns_none_when_empty(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed([])  # creates dirs, no tasks
    assert q.claim("w1") is None


def test_concurrent_claims_no_double_assignment(tmp_path: Path):
    """Spawn 10 threads, 100 tasks. Every task claimed exactly once."""
    q = Queue(root=tmp_path)
    slugs = [f"scenario-{i:03d}" for i in range(100)]
    q.seed(slugs)

    claimed_by: dict[str, list[str]] = {f"w{i}": [] for i in range(10)}
    lock = threading.Lock()

    def worker(worker_id: str):
        while True:
            slug = q.claim(worker_id)
            if slug is None:
                break
            with lock:
                claimed_by[worker_id].append(slug)

    threads = [threading.Thread(target=worker, args=(f"w{i}",), name=f"w{i}") for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    all_claimed = sorted(s for v in claimed_by.values() for s in v)
    assert all_claimed == slugs, "every task must be claimed exactly once"
    assert q.is_empty()


def test_write_result_persists_json(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["scenario-a"])
    q.claim("w1")
    result = ScenarioResult(
        slug="scenario-a",
        worker_id="w1",
        exit_code=0,
        duration_s=4.2,
        started_at=1000.0,
        finished_at=1004.2,
    )
    q.write_result(result)
    saved = q.results_dir / "w1" / "scenario-a.json"
    assert saved.exists()
    # Claim file stays in place; orphan detection compares against results.
    assert (q.claims_dir / "scenario-a.claim").exists()


def test_collect_results_aggregates_across_workers(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.init_dirs()
    for i, wid in enumerate(["w1", "w2", "w1", "w2"]):
        q.seed([f"s{i}"])
        slug = q.claim(wid)
        q.write_result(
            ScenarioResult(
                slug=slug,
                worker_id=wid,
                exit_code=i % 2,
                duration_s=1.0,
                started_at=0.0,
                finished_at=1.0,
            )
        )

    results = q.collect_results()
    assert len(results) == 4
    assert sum(1 for r in results if r.exit_code == 0) == 2
    assert sum(1 for r in results if r.exit_code != 0) == 2


def test_orphaned_running_tasks_detects_unfinished(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["crashed", "healthy"])  # claim order == seed order
    first = q.claim("w1")
    second = q.claim("w2")
    assert first == "crashed"
    assert second == "healthy"
    # w2 finishes; w1 simulates crash (no write_result).
    q.write_result(
        ScenarioResult(
            slug="healthy",
            worker_id="w2",
            exit_code=0,
            duration_s=1.0,
            started_at=0.0,
            finished_at=1.0,
        )
    )
    orphans = q.orphaned_running_tasks()
    assert orphans == {"w1": ["crashed"]}


# ── Exclusion groups ────────────────────────────────────────────────────────
# A group is collapsed into ONE task holding its members; the worker claiming it
# runs them sequentially. Exclusivity is therefore a property of the seed, not a
# run-time protocol — these pin both halves of that.


def _run_worker_loop(queue: Queue, worker_id: str, observed: list, lock: threading.Lock, hold_s: float = 0.01):
    """Mirror ``worker_main``'s loop exactly, recording each scenario's interval.

    The mirroring is the point: if the real loop ever exited while claimable work
    remained, this simulation would exit early too and the assertions below would
    catch it.
    """
    while True:
        task = queue.claim_task(worker_id)
        if task is None:
            break
        for slug in task.slugs:
            started = time.monotonic()
            time.sleep(hold_s)
            finished = time.monotonic()
            with lock:
                observed.append((task.group, slug, started, finished))
            queue.write_result(
                ScenarioResult(
                    slug=slug,
                    worker_id=worker_id,
                    exit_code=0,
                    duration_s=finished - started,
                    started_at=started,
                    finished_at=finished,
                )
            )


def test_group_members_never_overlap_under_concurrency(tmp_path: Path):
    """The load-bearing test: 10 workers, 3 groups. No two members of one group
    may be in flight at the same instant, and every scenario runs exactly once."""
    q = Queue(root=tmp_path)
    groups = {f"g{g}": [f"g{g}-s{i}" for i in range(10)] for g in range(3)}
    loose = [f"loose-{i:02d}" for i in range(20)]
    q.seed([*groups.keys(), *loose], batches=groups)

    observed: list = []
    lock = threading.Lock()
    threads = [
        threading.Thread(target=_run_worker_loop, args=(q, f"w{i}", observed, lock), name=f"w{i}") for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker never terminated"

    expected = sorted([s for members in groups.values() for s in members] + loose)
    assert sorted(row[1] for row in observed) == expected, "every scenario runs exactly once"

    for group in groups:
        spans = sorted((start, end) for g, _slug, start, end in observed if g == group)
        for (_a_start, a_end), (b_start, _b_end) in zip(spans, spans[1:]):
            assert a_end <= b_start, f"two members of {group} overlapped"


def test_worker_loop_drains_a_group_with_a_single_worker(tmp_path: Path):
    """One worker must not exit while its batch still has members left."""
    q = Queue(root=tmp_path)
    q.seed(["g"], batches={"g": ["a", "b", "c"]})
    observed: list = []
    _run_worker_loop(q, "w1", observed, threading.Lock(), hold_s=0.0)
    assert [row[1] for row in observed] == ["a", "b", "c"]
    assert q.is_empty()
    assert q.unclaimed_slugs() == []


def test_seed_writes_no_payload_without_batches(tmp_path: Path):
    """Inertness: with no group declared the queue directory is byte-identical
    to the pre-feature one — same names, empty markers."""
    plain = Queue(root=tmp_path / "plain")
    plain.seed(["a", "b"])
    empty_batches = Queue(root=tmp_path / "grouped")
    empty_batches.seed(["a", "b"], batches={})

    for q in (plain, empty_batches):
        assert sorted(p.name for p in q.queue_dir.iterdir()) == ["0000-a.task", "0001-b.task"]
        assert all(p.stat().st_size == 0 for p in q.queue_dir.iterdir())


def test_batch_marker_carries_its_members(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["billing", "loose"], batches={"billing": ["s1", "s3"]})
    payload = json.loads((q.queue_dir / "0000-billing.task").read_text(encoding="utf-8"))
    assert payload["slugs"] == ["s1", "s3"]
    assert payload["group"] == "billing"
    assert (q.queue_dir / "0001-loose.task").stat().st_size == 0


def test_claim_task_round_trip(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["billing", "loose"], batches={"billing": ["s1", "s3"]})
    first = q.claim_task("w1")
    assert (first.task_id, first.slugs, first.group) == ("billing", ("s1", "s3"), "billing")
    second = q.claim_task("w1")
    assert (second.task_id, second.slugs, second.group) == ("loose", ("loose",), None)
    assert q.claim_task("w1") is None


def test_claim_still_returns_the_task_id(tmp_path: Path):
    """``claim()`` stays a thin wrapper, so there is one implementation of the
    claim protocol and every existing caller keeps working."""
    q = Queue(root=tmp_path)
    q.seed(["billing"], batches={"billing": ["s1", "s3"]})
    assert q.claim("w1") == "billing"


def test_unreadable_batch_payload_raises_after_claiming(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["billing"], batches={"billing": ["s1"]})
    (q.queue_dir / "0000-billing.task").write_text("{not json", encoding="utf-8")
    with pytest.raises(TaskPayloadError) as exc:
        q.claim_task("w1")
    assert exc.value.task_id == "billing"
    # The claim succeeded first, so the members surface as orphans and are retried.
    assert (q.claims_dir / "billing.claim").exists()
    assert q.orphaned_running_tasks() == {"w1": ["s1"]}


def test_orphans_are_members_never_the_group_name(tmp_path: Path):
    """The retry set is built from orphans. Reporting the GROUP here would seed a
    retry task for a test file that does not exist, and the real members would
    never be retried at all."""
    q = Queue(root=tmp_path)
    q.seed(["payments"], batches={"payments": ["a", "b", "c"]})
    q.claim_task("w1")
    q.write_result(ScenarioResult("a", "w1", 0, 1.0, 0.0, 1.0))
    assert q.orphaned_running_tasks() == {"w1": ["b", "c"]}


def test_orphans_fall_back_to_the_stem_for_an_unmapped_task(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["plain", "payments"], batches={"payments": ["a"]})
    q.claim_task("w1")
    q.claim_task("w1")
    assert q.orphaned_running_tasks() == {"w1": ["a", "plain"]}


def test_ownerless_batch_claim_reports_every_member(tmp_path: Path):
    """A worker killed between the claim's create and its write leaves an empty
    claim file. Skipping it — today's behaviour for one scenario — would lose a
    whole group in silence, and the owner id is written before any scenario runs,
    so none of them started."""
    q = Queue(root=tmp_path)
    q.seed(["payments"], batches={"payments": ["a", "b"]})
    q.claim_task("w1")
    (q.claims_dir / "payments.claim").write_text("", encoding="utf-8")
    assert q.orphaned_running_tasks() == {UNKNOWN_WORKER: ["a", "b"]}


def test_reading_a_batched_queue_without_its_map_raises(tmp_path: Path):
    """Turns "somebody queried an unseeded Queue" into an immediate error rather
    than a phantom scenario named after the group."""
    q = Queue(root=tmp_path)
    q.seed(["payments"], batches={"payments": ["a", "b"]})
    q.claim_task("w1")
    stranger = Queue(root=tmp_path)
    with pytest.raises(RuntimeError, match="no batch map"):
        stranger.orphaned_running_tasks()


def test_unclaimed_slugs_expands_a_batch(tmp_path: Path):
    q = Queue(root=tmp_path)
    q.seed(["payments", "loose"], batches={"payments": ["a", "b"]})
    assert sorted(q.unclaimed_slugs()) == ["a", "b", "loose"]
    q.claim_task("w1")  # claims the payments batch
    assert q.unclaimed_slugs() == ["loose"]


def test_unclaimed_slugs_ignores_scenarios_that_reported(tmp_path: Path):
    """A result is proof it ran. Requiring BOTH files to be absent makes this
    incapable of a false positive — and this set forces the run to fail."""
    q = Queue(root=tmp_path)
    q.seed(["a"])
    q.write_result(ScenarioResult("a", "w1", 0, 1.0, 0.0, 1.0))
    assert q.unclaimed_slugs() == []
