"""The ``steppy build`` side of in-app tours: the ``guide`` target.

``steppy generate`` / ``steppy tours`` write the canonical tree
(``output/tours/<build-id>/``); a static guide build republishes it byte for
byte at ``<site>/tours/<build-id>/``, where an app in remote mode fetches it.
``build`` never compiles tours itself — it needs no scenario parsing for this,
and a guide site must serve exactly the bytes whose hashes the apps bundle.

Everything here runs **before** the build wipes its output directory: a tree
that is missing, stale (a file whose SRI no longer matches) or edited fails the
build while the previous site is still intact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..buildid import BuildIdError, resolve_build_id
from ..contracts.tours import TourDataInvalid, TourSchemaUnsupported, check_consistency, parse_tours_manifest
from ..naming import GUIDE_SITE_TOURS_DIR, TOURS_MANIFEST_FILENAME
from .paths import TourPathError, overlaps, read_marker
from .settings import ToursSettings


class TourPublishError(ValueError):
    """The guide target cannot be published as configured; the build must not wipe anything."""


@dataclass(frozen=True)
class GuidePublishPlan:
    build_id: str
    #: (file name, bytes): tour files first, the manifest last.
    files: tuple[tuple[str, bytes], ...]
    tour_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def site_dir(self) -> str:
        """The site-relative directory the tree is published under."""
        return f"{GUIDE_SITE_TOURS_DIR}/{self.build_id}"


def check_build_overlaps(settings: ToursSettings, output_dir: Path, assets_dir: Path) -> None:
    """The build rmtrees ``output_dir`` and ``assets_dir``: no tour directory may live there."""
    tour_dirs = [("output.tours_dir", settings.tours_root)]
    tour_dirs += [("an app-assets target", d) for d in settings.app_assets_dirs]
    for label, tour_dir in tour_dirs:
        for build_label, build_dir in (("outputDir", output_dir), ("assetsDir", assets_dir)):
            if overlaps(tour_dir, build_dir):
                raise TourPublishError(
                    f"tours: {label} ({tour_dir}) overlaps the build's {build_label} ({build_dir}), which "
                    "`steppy build` deletes before writing — move one of them"
                )


def plan_guide_publish(
    settings: ToursSettings,
    *,
    guide_slugs: Iterable[str],
    build_locales: Iterable[str],
) -> GuidePublishPlan:
    """Read and verify the canonical tree for the configured build id."""
    try:
        build_id = resolve_build_id(settings.build_id_raw, settings.project_root)
    except BuildIdError as exc:
        raise TourPublishError(f"tours: {exc}") from exc
    tree = settings.tours_root / build_id
    manifest_path = tree / TOURS_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise TourPublishError(
            f"In-app tours for build {build_id} not found at {tree}. Run `steppy generate` or "
            "`steppy tours` first (with the same tours.build_id)."
        )
    try:
        read_marker(tree)
        raw = manifest_path.read_bytes()
        manifest = parse_tours_manifest(json.loads(raw.decode("utf-8")))
    except (TourPathError, TourDataInvalid, TourSchemaUnsupported, ValueError) as exc:
        raise TourPublishError(f"tours: {manifest_path} is not a valid tours manifest: {exc}") from exc
    if manifest.buildId != build_id:
        raise TourPublishError(f"tours: {manifest_path} is for build {manifest.buildId!r}, expected {build_id!r}")

    names = sorted({name for tour in manifest.tours for name in tour.files.values()})
    files = {name: (tree / name).read_bytes() for name in names if (tree / name).is_file()}
    problems = check_consistency(manifest, files)
    if problems:
        raise TourPublishError(
            f"tours: the tree at {tree} is stale or was edited ({problems[0]}); re-run `steppy tours`"
        )

    warnings = list(_guide_link_warnings(settings, manifest, files, set(guide_slugs), set(build_locales)))
    return GuidePublishPlan(
        build_id=build_id,
        files=(*((name, files[name]) for name in names), (TOURS_MANIFEST_FILENAME, raw)),
        tour_ids=tuple(tour.id for tour in manifest.tours),
        warnings=tuple(warnings),
    )


def _guide_link_warnings(
    settings: ToursSettings,
    manifest,
    files: dict[str, bytes],
    guide_slugs: set[str],
    build_locales: set[str],
) -> Iterable[str]:
    """A tour's "open the full guide" link that this build will not satisfy."""
    base = (settings.guide_base_url or "").rstrip("/") + "/"
    seen: set[str] = set()
    for tour in manifest.tours:
        for name in tour.files.values():
            url: Optional[str] = json.loads(files[name].decode("utf-8")).get("guideUrl")
            if not url or not url.startswith(base) or url in seen:
                continue
            seen.add(url)
            slug, _, page = url[len(base) :].partition("/")
            locale = page.removesuffix(".html")
            if slug not in guide_slugs:
                yield f"tours: tour {tour.id!r} links to guide {slug!r}, which this build does not publish ({url})"
            elif locale not in build_locales:
                yield f"tours: tour {tour.id!r} links to the {locale!r} page of {slug!r}, which this build has no locale for"
