"""Payloads, canonical bytes, and write plans for tour output.

Every byte written goes through :func:`steppy.contracts.tours.dumps_canonical`
and is re-validated by the producer contract before anything touches disk, so
a compiler bug can never ship a file the overlay would reject.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..contracts.tours import (
    TourDataInvalid,
    check_consistency,
    dumps_canonical,
    parse_tour_file,
    parse_tours_manifest,
    sri_sha384,
)
from ..naming import TOUR_FILE_KIND, TOURS_MANIFEST_FILENAME, TOURS_MANIFEST_KIND
from ..tour_spec import MAX_MANIFEST_BYTES, MAX_TOUR_FILE_BYTES, TOUR_SCHEMA_VERSION, tour_file_name
from .model import CompiledTour, TourCompileResult
from .paths import TourPathError, atomic_write_bytes, check_tour_file, is_tour_file, listed_files, read_marker
from .settings import ToursSettings

Status = Literal["generated", "updated", "unchanged", "removed", "planned", "missing"]


class TourContractBug(RuntimeError):
    """The compiler produced something its own contract rejects (always a bug)."""


@dataclass
class TourArtifacts:
    build_id: str
    files: dict[str, bytes]  # tour file name -> bytes
    manifest: bytes

    def all_files(self) -> dict[str, bytes]:
        return {**self.files, TOURS_MANIFEST_FILENAME: self.manifest}


@dataclass
class FileStatus:
    path: Path
    status: Status


@dataclass
class DirPlan:
    directory: Path
    statuses: list[FileStatus] = field(default_factory=list)
    writes: dict[str, bytes] = field(default_factory=dict)  # name -> bytes, tour files then manifest
    removals: list[str] = field(default_factory=list)


def tour_file_payload(tour: CompiledTour, locale: str, build_id: str) -> dict:
    steps = []
    for n, plan in enumerate(tour.steps):
        title, text = tour.step_texts[locale][n]
        step = {"id": plan.step_id, "target": plan.target, "advance": {"on": plan.advance}}
        if plan.wait_for:
            step["waitFor"] = plan.wait_for
        if title:
            step["title"] = title
        if text:
            step["text"] = text
        if plan.placement and plan.placement != "auto":
            step["placement"] = plan.placement
        if plan.hold:
            step["hold"] = plan.hold
        if plan.target_click:
            step["targetClick"] = plan.target_click
        if plan.layouts:
            step["layouts"] = list(plan.layouts)
        steps.append(step)
    payload = {
        "schema": TOUR_SCHEMA_VERSION,
        "kind": TOUR_FILE_KIND,
        "id": tour.id,
        "buildId": build_id,
        "locale": locale,
        "title": tour.titles[locale],
        "entry": {"route": tour.entry_route} if tour.entry_route else None,
        "steps": steps,
    }
    if locale in tour.guide_urls:
        payload["guideUrl"] = tour.guide_urls[locale]
    return payload


def render_artifacts(result: TourCompileResult, settings: ToursSettings, build_id: str) -> TourArtifacts:
    """Serialize every tour file and the manifest, then self-check them."""
    files: dict[str, bytes] = {}
    entries = []
    used_layouts: set[str] = set()
    for tour in result.tours:
        integrity = {}
        names = {}
        for locale in tour.locales:
            data = dumps_canonical(tour_file_payload(tour, locale, build_id))
            name = tour_file_name(tour.id, locale)
            if len(data) > MAX_TOUR_FILE_BYTES:
                raise TourDataInvalid(f"{name} is {len(data)} bytes; the player accepts at most {MAX_TOUR_FILE_BYTES}")
            files[name] = data
            names[locale] = name
            integrity[locale] = sri_sha384(data)
        for plan in tour.steps:
            used_layouts.update(plan.layouts or ())
        entries.append(
            {
                "id": tour.id,
                "role": tour.role,
                "title": {loc: tour.titles[loc] for loc in tour.locales},
                "entry": {"route": tour.entry_route} if tour.entry_route else None,
                "steps": len(tour.steps),
                "files": names,
                "integrity": integrity,
            }
        )
    manifest = {
        "schema": TOUR_SCHEMA_VERSION,
        "kind": TOURS_MANIFEST_KIND,
        "buildId": build_id,
        "defaultLocale": settings.default_locale,
        "locales": list(settings.locales),
        "testIdAttribute": settings.test_id_attribute,
        "tours": entries,
    }
    if settings.guide_origin and any(t.guide_urls for t in result.tours):
        manifest["guideOrigin"] = settings.guide_origin
    if used_layouts:
        manifest["layouts"] = {name: q for name, q in settings.layouts if name in used_layouts}
    manifest_bytes = dumps_canonical(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise TourDataInvalid(f"the manifest is {len(manifest_bytes)} bytes; the player accepts {MAX_MANIFEST_BYTES}")
    try:
        parsed = parse_tours_manifest(json.loads(manifest_bytes))
        for data in files.values():
            parse_tour_file(json.loads(data))
    except TourDataInvalid as exc:
        raise TourContractBug(f"compiled tour data violates the v1 contract: {exc}") from exc
    problems = check_consistency(parsed, files)
    if problems:
        raise TourContractBug("compiled tour data is inconsistent: " + "; ".join(problems))
    return TourArtifacts(build_id=build_id, files=files, manifest=manifest_bytes)


def plan_dir(directory: Path, artifacts: TourArtifacts, *, prune: bool = True) -> DirPlan:
    """Byte-compare against disk; plan writes and (marker-checked) stale removals.

    Raises :class:`TourPathError` — at planning time, before anything is written —
    when a write or a removal would hit a file that is not a tour file (the
    manifest itself is vouched for by :func:`read_marker`).
    """
    plan = DirPlan(directory=directory)
    previous = read_marker(directory) if directory.is_dir() else None
    for name, data in [*sorted(artifacts.files.items()), (TOURS_MANIFEST_FILENAME, artifacts.manifest)]:
        path = directory / name
        if path.is_file():
            if path.read_bytes() == data:
                plan.statuses.append(FileStatus(path, "unchanged"))
                continue
            if name != TOURS_MANIFEST_FILENAME:
                check_tour_file(path, "overwrite")
            plan.statuses.append(FileStatus(path, "updated"))
        elif path.exists() or path.is_symlink():
            raise TourPathError(f"{path} exists and is not a regular file; refusing to replace it")
        else:
            plan.statuses.append(FileStatus(path, "generated"))
        plan.writes[name] = data
    if prune:
        for name in sorted(listed_files(previous) - set(artifacts.files)):
            path = directory / name
            # A case-only rename (``Fr-be`` -> ``fr-BE``) on a case-insensitive
            # filesystem: the old name is the file about to be rewritten.
            if any(_same_file(path, directory / w) for w in artifacts.files if w.casefold() == name.casefold()):
                continue
            if path.is_file() and not path.is_symlink():
                check_tour_file(path, "delete")
                plan.removals.append(name)
                plan.statuses.append(FileStatus(path, "removed"))
    return plan


def apply_dir(plan: DirPlan) -> None:
    """Tour files first, the manifest last (so a reader never sees a manifest without its files)."""
    names = [n for n in plan.writes if n != TOURS_MANIFEST_FILENAME]
    if TOURS_MANIFEST_FILENAME in plan.writes:
        names.append(TOURS_MANIFEST_FILENAME)
    for name in names:
        atomic_write_bytes(plan.directory / name, plan.writes[name])
    for name in plan.removals:
        (plan.directory / name).unlink(missing_ok=True)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def sibling_build_dirs(tours_root: Path, keep: str) -> list[Path]:
    """Other ``<build-id>/`` trees under the canonical root that carry our marker."""
    out = []
    if not tours_root.is_dir():
        return out
    for child in sorted(tours_root.iterdir()):
        if child.name == keep or not child.is_dir() or child.is_symlink():
            continue
        # The tree just written, reached under a case variant of its id on a
        # case-insensitive filesystem.
        if _same_file(child, tours_root / keep):
            continue
        try:
            if read_marker(child) is not None:
                out.append(child)
        except ValueError:
            continue
    return out


def remove_build_dir(directory: Path) -> list[Path]:
    """Remove a marker-checked build tree: only its manifest and the files it lists."""
    manifest = read_marker(directory)
    removed = []
    for name in sorted(listed_files(manifest)):
        path = directory / name
        if path.is_file() and not path.is_symlink() and is_tour_file(path):
            path.unlink()
            removed.append(path)
    (directory / TOURS_MANIFEST_FILENAME).unlink(missing_ok=True)
    removed.append(directory / TOURS_MANIFEST_FILENAME)
    try:
        directory.rmdir()
    except OSError:
        pass
    return removed
