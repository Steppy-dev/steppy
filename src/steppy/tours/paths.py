"""Filesystem guards for tour output: where we may write, and what we may delete.

Tour files land in two kinds of place: the canonical tree under
``output.tours_dir`` and app-assets directories inside *someone else's*
repository. Pruning stale files there is only safe when the directory is
provably ours, so the rules are strict:

* a directory is ours only when its ``tours.manifest.json`` carries our
  ``kind`` marker — a foreign or unreadable manifest stops everything;
* only files that previous manifest *listed* (and that look like tour file
  names) are ever deleted — never a glob — and an existing file is only ever
  overwritten or deleted when it is itself a tour file (its JSON ``kind``);
* no target may be, contain, or sit inside the project root, the scenarios,
  the generated tests, the captures, or another target.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from ..naming import TOUR_FILE_KIND, TOURS_MANIFEST_FILENAME, TOURS_MANIFEST_KIND
from ..tour_spec import TOUR_FILE_NAME_PATTERN

#: Names that betray a project or package root — never write into a directory holding one.
GUARDED_NAMES = ("config.yaml", "pyproject.toml", ".git", "scenarios", "src", "package.json", "angular.json")


class TourPathError(ValueError):
    """A tour target or prune would touch a path it must not."""


def _resolved(path: Path) -> Path:
    return Path(os.path.abspath(path))


def overlaps(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` are the same path or one contains the other."""
    a, b = _resolved(a), _resolved(b)
    return a == b or a in b.parents or b in a.parents


def check_emit_dir(path: Path, *, project_root: Path, owned_roots: Iterable[Path], label: str) -> None:
    """Refuse a write target that could clobber something that is not ours."""
    target = _resolved(path)
    root = _resolved(project_root)
    if target == Path(target.anchor):
        raise TourPathError(f"{label} {path} is a filesystem root")
    if target == root or target in root.parents:
        raise TourPathError(f"{label} {path} is the project root or one of its parents")
    for owned in owned_roots:
        if overlaps(target, owned):
            raise TourPathError(f"{label} {path} overlaps {owned}, which Steppy owns for something else")
    if target.is_dir():
        clash = [name for name in GUARDED_NAMES if (target / name).exists()]
        if clash:
            raise TourPathError(
                f"{label} {path} contains {', '.join(clash)} — it looks like a project directory, not a tours folder"
            )


def read_marker(directory: Path) -> Optional[dict]:
    """The previous manifest if ``directory`` is ours, None if it has none; raise if foreign."""
    manifest = directory / TOURS_MANIFEST_FILENAME
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise TourPathError(f"{manifest} is not readable JSON ({exc}); refusing to touch {directory}") from exc
    if not isinstance(data, dict) or data.get("kind") != TOURS_MANIFEST_KIND:
        raise TourPathError(
            f"{manifest} is not a Steppy tours manifest; refusing to touch {directory} — delete it manually"
        )
    return data


def listed_files(manifest: Optional[dict]) -> set[str]:
    """Tour file names a (marker-checked) manifest lists; anything path-like is ignored."""
    if not manifest:
        return set()
    names: set[str] = set()
    for tour in manifest.get("tours") or []:
        if not isinstance(tour, dict):
            continue
        for name in (tour.get("files") or {}).values():
            if isinstance(name, str) and re.fullmatch(TOUR_FILE_NAME_PATTERN, name):
                names.add(name)
    return names


def is_tour_file(path: Path) -> bool:
    """True when ``path`` is a JSON file carrying the tour-file ``kind`` marker."""
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    return isinstance(data, dict) and data.get("kind") == TOUR_FILE_KIND


def check_tour_file(path: Path, verb: str) -> None:
    """Refuse to ``verb`` (overwrite/delete) a file that is not a Steppy tour file."""
    if not is_tour_file(path):
        raise TourPathError(
            f"{path} is not a Steppy tour file; refusing to {verb} it — move it away or delete it manually"
        )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a sibling temp file + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
