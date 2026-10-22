"""Reclaiming the two render caches.

Both are content-addressed, which is what makes them correct and what makes them
grow forever. The rules under test are the two that make collection safe: an
entry is its primary file *plus* its sidecars, and recency means last **use**
rather than last write — the lookup paths touch on every hit, so evicting by
write time would drop exactly the entries that are earning their keep.
"""

import os
import time

import pytest

from steppy.video_cache import Reclaimed, clear, prune, scan, stats, sweep_scratch

DAY = 86400


def entry(root, key, suffix, *sidecars, size=1024, age_days=0.0):
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / f"{key}{suffix}", *(root / f"{key}{s}" for s in sidecars)]
    for path in paths:
        path.write_bytes(b"x" * size)
    when = time.time() - age_days * DAY
    for path in paths:
        os.utime(path, (when, when))
    return paths


# ── Scanning ────────────────────────────────────────────────────────────────


def test_an_entry_gathers_its_sidecars(tmp_path):
    entry(tmp_path, "abc", ".mp4", ".vtt", ".jpg", ".meta", size=100)
    (found,) = scan(tmp_path, ".mp4")
    assert found.key == "abc"
    assert {p.suffix for p in found.sidecars} == {".vtt", ".jpg", ".meta"}
    assert found.size == 400


def test_staging_files_are_never_entries(tmp_path):
    entry(tmp_path, "abc", ".mp4")
    (tmp_path / ".abc.1234.tmp.mp4").write_bytes(b"partial")
    assert [e.key for e in scan(tmp_path, ".mp4")] == ["abc"]


def test_entries_come_back_most_recently_used_first(tmp_path):
    entry(tmp_path, "old", ".mp4", age_days=10)
    entry(tmp_path, "new", ".mp4", age_days=1)
    assert [e.key for e in scan(tmp_path, ".mp4")] == ["new", "old"]


def test_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert scan(tmp_path / "nope", ".mp4") == []
    assert stats(tmp_path / "nope", ".mp4").exists is False


# ── Pruning ─────────────────────────────────────────────────────────────────


def test_pruning_by_age_takes_the_whole_entry(tmp_path):
    """A dropped .mp4 whose .vtt survives leaves the directory looking populated
    while every lookup misses, and nothing ever collects the orphan."""
    entry(tmp_path, "stale", ".mp4", ".vtt", ".jpg", ".meta", age_days=40)
    entry(tmp_path, "fresh", ".mp4", ".vtt", age_days=1)

    freed = prune(tmp_path, ".mp4", older_than_days=30)
    assert freed.entries == 1
    assert list(tmp_path.glob("stale*")) == []
    assert (tmp_path / "fresh.mp4").is_file() and (tmp_path / "fresh.vtt").is_file()


def test_a_hit_touched_today_survives_a_prune_of_old_writes(tmp_path):
    """The whole reason ``ClipCache.get`` and ``video._touch_entry`` touch."""
    paths = entry(tmp_path, "reused", ".mp4", ".vtt", age_days=400)
    for path in paths:  # a cache hit, this morning
        path.touch()
    assert prune(tmp_path, ".mp4", older_than_days=30).entries == 0
    assert (tmp_path / "reused.mp4").is_file()


def test_a_budget_evicts_least_recently_used_first(tmp_path):
    entry(tmp_path, "a", ".mp4", size=1000, age_days=1)
    entry(tmp_path, "b", ".mp4", size=1000, age_days=5)
    entry(tmp_path, "c", ".mp4", size=1000, age_days=9)

    freed = prune(tmp_path, ".mp4", max_bytes=2000)
    assert freed.entries == 1 and freed.size == 1000
    assert {e.key for e in scan(tmp_path, ".mp4")} == {"a", "b"}


def test_reporting_nothing_to_do_is_not_an_error(tmp_path):
    entry(tmp_path, "a", ".mp4", age_days=1)
    assert prune(tmp_path, ".mp4", older_than_days=30) == Reclaimed(0, 0)


# ── Scratch ─────────────────────────────────────────────────────────────────


def test_scratch_older_than_a_day_is_swept(tmp_path):
    stale = tmp_path / ".abc.999.tmp.mp4"
    tmp_path.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"partial")
    when = time.time() - 3 * DAY
    os.utime(stale, (when, when))

    assert sweep_scratch(tmp_path).entries == 1
    assert not stale.exists()


def test_scratch_from_a_build_running_right_now_is_left_alone(tmp_path):
    live = tmp_path / ".abc.999.tmp.mp4"
    tmp_path.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"in flight")
    assert sweep_scratch(tmp_path).entries == 0
    assert live.is_file()


def test_the_work_dir_is_scratch_not_entries(tmp_path):
    """``tts-cache/work/`` holds the pre-transcode container; the normalized
    clip is the artifact, so anything left here is a killed run's residue."""
    work = tmp_path / "work"
    work.mkdir(parents=True)
    raw = work / "abc.raw.mp3"
    raw.write_bytes(b"mp3")
    when = time.time() - 3 * DAY
    os.utime(raw, (when, when))

    assert sweep_scratch(tmp_path).entries == 1
    assert not raw.exists()


# ── Clearing and stats ──────────────────────────────────────────────────────


def test_clearing_removes_entries_and_scratch_but_keeps_the_directory(tmp_path):
    entry(tmp_path, "a", ".wav", ".json")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "raw.mp3").write_bytes(b"x")

    freed = clear(tmp_path, ".wav")
    assert freed.entries == 1
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []


def test_stats_add_up_across_an_entry_and_its_sidecars(tmp_path):
    entry(tmp_path, "a", ".wav", ".json", size=500, age_days=2)
    entry(tmp_path, "b", ".wav", size=500, age_days=8)

    got = stats(tmp_path, ".wav")
    assert got.entries == 2
    assert got.size == 1500
    assert got.newest > got.oldest


@pytest.mark.parametrize("suffix", [".mp4", ".wav"])
def test_both_caches_use_the_same_machinery(tmp_path, suffix):
    entry(tmp_path, "k", suffix, age_days=99)
    assert prune(tmp_path, suffix, older_than_days=1).entries == 1
