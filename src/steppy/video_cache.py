"""Inspect and reclaim the two render caches — a **stdlib-only leaf**.

``.steppy/tts-cache/`` and ``.steppy/video-cache/`` are content-addressed, which
makes them correct and makes them grow forever: every edited sentence and every
re-render leaves its predecessor behind, and a Piper upgrade orphans every clip
at once because the engine version is in the key. Nothing ever collected them,
and an MP4 corpus is measured in gigabytes.

Two rules this module exists to keep:

* **An entry is its primary file plus its sidecars.** Dropping a ``.mp4`` and
  leaving its ``.vtt`` behind would leave the directory looking populated while
  every lookup misses, and the orphan would never be collected by anything.
* **Recency means last *use*, not last write.** The lookup paths touch an entry
  on every hit (``ClipCache.get``, ``video._touch_entry``), so a clip rebuilt
  into every release stays young. Without that, LRU here would evict exactly the
  entries that are earning their keep.

Imports nothing from ``steppy`` — ``video_cli`` must stay cheap enough to sit on
the eager CLI path (guard: ``tests/test_no_video_import_on_default_path.py``).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# An entry's primary artifact, and the companions that travel with it.
SIDECARS: dict[str, tuple[str, ...]] = {
    ".mp4": (".vtt", ".jpg", ".meta"),
    ".wav": (".json",),
}

# A staging file younger than this may belong to a build running right now.
SCRATCH_GRACE_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Entry:
    key: str
    primary: Path
    sidecars: tuple[Path, ...]
    size: int
    used_at: float

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.primary, *self.sidecars)


@dataclass(frozen=True)
class Stats:
    root: Path
    exists: bool
    entries: int
    size: int
    oldest: Optional[float]
    newest: Optional[float]
    scratch_files: int
    scratch_size: int


@dataclass(frozen=True)
class Reclaimed:
    entries: int
    size: int

    def __add__(self, other: "Reclaimed") -> "Reclaimed":
        return Reclaimed(self.entries + other.entries, self.size + other.size)


def scan(root: Path, suffix: str) -> list[Entry]:
    """Every complete entry under *root*, newest use first."""
    root = Path(root)
    if not root.is_dir():
        return []
    entries: list[Entry] = []
    for primary in sorted(root.glob(f"*{suffix}")):
        if primary.name.startswith("."):  # staging file, not an entry
            continue
        sidecars = tuple(p for p in (primary.with_suffix(s) for s in SIDECARS.get(suffix, ())) if p.is_file())
        size = _size(primary) + sum(_size(p) for p in sidecars)
        entries.append(
            Entry(
                key=primary.stem,
                primary=primary,
                sidecars=sidecars,
                size=size,
                used_at=_used_at(primary, sidecars),
            )
        )
    entries.sort(key=lambda e: e.used_at, reverse=True)
    return entries


def stats(root: Path, suffix: str) -> Stats:
    root = Path(root)
    entries = scan(root, suffix)
    scratch = list(scratch_files(root))
    used = [e.used_at for e in entries]
    return Stats(
        root=root,
        exists=root.is_dir(),
        entries=len(entries),
        size=sum(e.size for e in entries),
        oldest=min(used) if used else None,
        newest=max(used) if used else None,
        scratch_files=len(scratch),
        scratch_size=sum(_size(p) for p in scratch),
    )


def scratch_files(root: Path) -> Iterable[Path]:
    """Staging leftovers and pre-transcode scratch — never an entry.

    ``tts-cache/work/`` holds the raw container a provider returned before it
    was normalized to WAV; the normalized clip is the artifact, so anything left
    here is the residue of a killed run.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    found = [p for p in root.glob(".*.tmp*") if p.is_file()]
    work = root / "work"
    if work.is_dir():
        found.extend(p for p in work.rglob("*") if p.is_file())
    return found


def prune(
    root: Path,
    suffix: str,
    *,
    older_than_days: Optional[float] = None,
    max_bytes: Optional[int] = None,
    now: Optional[float] = None,
) -> Reclaimed:
    """Drop entries by age, then by budget. Least recently *used* goes first."""
    entries = scan(root, suffix)
    now = time.time() if now is None else now
    doomed: list[Entry] = []

    if older_than_days is not None:
        cutoff = now - older_than_days * 86400
        doomed = [e for e in entries if e.used_at <= cutoff]
        entries = [e for e in entries if e.used_at > cutoff]

    if max_bytes is not None:
        running = 0
        keep: list[Entry] = []
        for entry in entries:  # already newest-first
            if running + entry.size > max_bytes:
                doomed.append(entry)
            else:
                running += entry.size
                keep.append(entry)
        entries = keep

    freed = 0
    removed = 0
    for entry in doomed:
        if _remove(entry.paths):
            removed += 1
            freed += entry.size
    return Reclaimed(removed, freed)


def sweep_scratch(
    root: Path, *, grace_seconds: float = SCRATCH_GRACE_SECONDS, now: Optional[float] = None
) -> Reclaimed:
    """Remove staging leftovers old enough that no live build can own them."""
    now = time.time() if now is None else now
    freed = 0
    removed = 0
    for path in scratch_files(root):
        try:
            if now - path.stat().st_mtime < grace_seconds:
                continue
            size = _size(path)
        except OSError:
            continue
        if _remove((path,)):
            removed += 1
            freed += size
    return Reclaimed(removed, freed)


def clear(root: Path, suffix: str) -> Reclaimed:
    """Drop every entry, plus the scratch dirs. The directory itself survives,
    so a subsequent run does not have to reason about whether it exists."""
    root = Path(root)
    entries = scan(root, suffix)
    freed = sum(e.size for e in entries)
    removed = 0
    for entry in entries:
        if _remove(entry.paths):
            removed += 1
    for path in scratch_files(root):
        freed += _size(path)
        _remove((path,))
    work = root / "work"
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)
    return Reclaimed(removed, freed)


def _used_at(primary: Path, sidecars: tuple[Path, ...]) -> float:
    """Newest mtime across the entry — a hit touches whatever it reads, and a
    sidecar-only touch still counts as the entry having been used."""
    times = []
    for path in (primary, *sidecars):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove(paths: tuple[Path, ...]) -> bool:
    """Best-effort delete. A file held open by another process (Windows) is
    skipped rather than fatal — the next prune will get it."""
    ok = False
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            ok = True
        except OSError:
            pass
    return ok
