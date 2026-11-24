"""Manifest assembly: walk, classify, hash, budget.

Two passes over the tree. Pass one walks, stats, classifies and hashes; pass two
streams the bytes into the archive. That is two reads of every byte, and on a
large capture tree it is a real cost — but the alternative is buffering the tar
to a temp file so the manifest can be prepended, which doubles peak disk on
exactly the runs where disk is already the constraint. Sequential reads are the
cheaper half of that trade.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .. import __version__
from ..contracts.bundle import (
    BUNDLE_SCHEMA_VERSION,
    BundleCi,
    BundleCoverage,
    BundleFile,
    BundleGit,
    BundleManifest,
    BundleRun,
    BundleSpec,
    BundleTest,
)
from ..provenance import RunMetadata
from . import classify as _classify
from .hashing import sha256_file

#: Default home for written bundles, and therefore excluded from the walk.
BUNDLES_SUBDIR = "bundles"

#: Kinds a user can exclude by flag. Excluding these does NOT set ``truncated``
#: — that flag means "we dropped something that belonged here", and a declared
#: scope is not a loss.
OPTIONAL_KINDS = {
    "guides": {
        _classify.KIND_GUIDE_HTML,
        _classify.KIND_GUIDE_MD,
        _classify.KIND_GUIDE_ASSET,
        _classify.KIND_INTERACTIVE_MANIFEST,
    },
    "traces": {_classify.KIND_TRACE},
    "videos": {_classify.KIND_VIDEO},
    "coverage": {
        _classify.KIND_COVERAGE_RECORD,
        _classify.KIND_COVERAGE_SOURCEMAP,
        _classify.KIND_COVERAGE_GAPS,
    },
}


@dataclass
class Candidate:
    """A file that passed classification, with its content facts filled in."""

    entry: BundleFile
    source: Path


@dataclass
class Assembly:
    manifest: BundleManifest
    candidates: list[Candidate]
    junit_xml: Optional[bytes] = None
    events: list[tuple[str, Path]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def contract_metadata(meta: RunMetadata) -> tuple[BundleGit, Optional[BundleCi]]:
    """A run's metadata as the contract's ``run.git`` and ``run.ci``."""
    git = BundleGit(**asdict(meta.git))
    ci = BundleCi(**asdict(meta.ci)) if meta.ci is not None else None
    return git, ci


def _spec_from_sidecar(sidecar: Optional[dict]) -> BundleSpec:
    if not sidecar:
        return BundleSpec()
    raw = sidecar.get("spec")
    if not isinstance(raw, dict):
        return BundleSpec()
    return BundleSpec.model_validate(raw)


def walk_candidates(
    output_dir: Path,
    ctx: _classify.ClassifyContext,
    *,
    exclude_kinds: frozenset[str] = frozenset(),
    skip_rel: frozenset[str] = frozenset(),
) -> tuple[list[Candidate], list[str]]:
    """Classify and hash everything under ``output_dir``.

    Walks the whole tree rather than only the roots we know about: an
    unclassified file becomes ``kind="other"`` and still ships. Dropping a file
    silently is a far worse failure than carrying one nobody labelled, and the
    contract explicitly allows unknown kinds.
    """
    candidates: list[Candidate] = []
    warnings: list[str] = []
    earlier_failures = 0

    for path in sorted(output_dir.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            # A symlinked capture tree is a path-traversal vector for whatever
            # unpacks this. Never follow one; say so rather than dropping it
            # quietly.
            warnings.append(f"skipped symlink {path.relative_to(output_dir).as_posix()}")
            continue
        if not path.is_file():
            continue

        rel = path.relative_to(output_dir)
        rel_posix = rel.as_posix()
        if any(rel_posix == s or rel_posix.startswith(s + "/") for s in skip_rel):
            continue

        entry = _classify.classify(rel, ctx)
        if entry is None:
            earlier_failures += _classify.is_failure_screenshot(rel, ctx)
            continue
        if entry.kind in exclude_kinds:
            continue

        # A coverage record describes itself; trust that over the filename.
        if entry.kind == _classify.KIND_COVERAGE_RECORD:
            attribution = _classify.read_coverage_record_attribution(path)
            if attribution:
                scenario, locale, layout = attribution
                entry = entry.model_copy(update={"scenario": scenario, "locale": locale, "layout": layout})

        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError as e:
            warnings.append(f"skipped unreadable {rel_posix}: {e}")
            continue

        candidates.append(
            Candidate(
                entry=entry.model_copy(update={"path": f"output/{rel_posix}", "sha256": digest, "size": size}),
                source=path,
            )
        )

    if earlier_failures:
        warnings.append(f"left out {earlier_failures} failure screenshot(s) of tests this run did not run")
    return candidates, warnings


def apply_size_budget(candidates: list[Candidate], max_size: int) -> tuple[list[Candidate], bool, list[str]]:
    """Drop the cheapest-to-lose kinds until the payload fits ``max_size``.

    Returns ``(kept, truncated, warnings)``. ``max_size <= 0`` disables the
    budget. Every drop is reported — a bundle that silently omits data reads to
    its consumer as "this never happened", which is exactly the wrong
    conclusion.
    """
    if max_size <= 0:
        return candidates, False, []

    total = sum(c.entry.size for c in candidates)
    if total <= max_size:
        return candidates, False, []

    kept = list(candidates)
    warnings: list[str] = []
    for kind in _classify.DROP_ORDER:
        if total <= max_size:
            break
        victims = [c for c in kept if c.entry.kind == kind]
        if not victims:
            continue
        freed = sum(c.entry.size for c in victims)
        kept = [c for c in kept if c.entry.kind != kind]
        total -= freed
        warnings.append(f"size budget: dropped {len(victims)} {kind} file(s) ({freed / 1_048_576:.1f} MiB)")

    if total > max_size:
        warnings.append(
            f"size budget exceeded even after dropping every optional artifact "
            f"({total / 1_048_576:.1f} MiB > {max_size / 1_048_576:.1f} MiB); "
            "screenshots and their sidecars are never dropped — raise --max-size"
        )
    return kept, True, warnings


def build(
    *,
    project_root: Path,
    output_dir: Path,
    run_id: str,
    tests: list[BundleTest],
    locales: frozenset[str],
    layouts: frozenset[str],
    coverage_dirname: str = "coverage",
    screenshots_dirname: str = "screenshots",
    guides_dirname: str = "guides",
    reports_dirname: str = "reports",
    sidecar: Optional[dict] = None,
    executor: str = "local",
    git: Optional[BundleGit] = None,
    ci: Optional[BundleCi] = None,
    environment: Optional[str] = None,
    exclude_kinds: frozenset[str] = frozenset(),
    max_size: int = 0,
    junit_xml: Optional[bytes] = None,
    events: Optional[list[tuple[str, Path]]] = None,
    extra_warnings: Optional[list[str]] = None,
) -> Assembly:
    """Assemble the manifest and the list of files to archive."""
    ctx = _classify.ClassifyContext(
        run_id=run_id,
        locales=locales,
        layouts=layouts,
        nodeid_index=_classify.nodeid_index(tests),
        screenshots_dirname=screenshots_dirname,
        coverage_dirname=coverage_dirname,
        guides_dirname=guides_dirname,
        reports_dirname=reports_dirname,
    )

    # The two generated members are archived at the root, not under output/, so
    # their source directories are skipped in the walk.
    from ..runmeta import EVENTS_SUBDIR, JUNIT_SUBDIR, REPORT_SUBDIR, RUN_SIDECAR_NAME

    candidates, warnings = walk_candidates(
        output_dir,
        ctx,
        exclude_kinds=exclude_kinds,
        # ``bundles`` is where we write by default; without skipping it, each
        # run would archive its predecessor and the tree would grow
        # quadratically. ``report`` is the same argument once removed: the
        # rendered site *contains copies of* the screenshots this bundle already
        # carries, so archiving it would roughly double the payload for nothing.
        skip_rel=frozenset({EVENTS_SUBDIR, JUNIT_SUBDIR, RUN_SIDECAR_NAME, BUNDLES_SUBDIR, REPORT_SUBDIR}),
    )
    warnings.extend(extra_warnings or [])

    candidates, truncated, budget_warnings = apply_size_budget(candidates, max_size)
    warnings.extend(budget_warnings)

    files = [c.entry for c in candidates]

    # The generated members are content-addressed too, so a consumer's dedup
    # covers every byte in the archive. manifest.json is the one exception —
    # it cannot contain its own digest.
    from .hashing import sha256_bytes

    if junit_xml is not None:
        files.append(
            BundleFile(path="junit.xml", kind=_classify.KIND_JUNIT, sha256=sha256_bytes(junit_xml), size=len(junit_xml))
        )
    for arcname, source in events or []:
        try:
            files.append(
                BundleFile(
                    path=arcname,
                    kind=_classify.KIND_EVENTS,
                    sha256=sha256_file(source),
                    size=source.stat().st_size,
                )
            )
        except OSError as e:
            warnings.append(f"skipped unreadable {arcname}: {e}")

    files.sort(key=lambda f: f.path)

    coverage_records = _classify.count_coverage_records(output_dir / coverage_dirname, run_id)
    # ``run.created_at`` describes the RUN, not the bundling of it — so it comes
    # from the sidecar the run wrote, and only falls back to now when there is
    # none. That is both more truthful and what makes an archive reproducible:
    # re-bundling an unchanged tree must produce identical bytes, or the
    # content-addressed upload protocol re-sends everything every time.
    created_at = (sidecar or {}).get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest = BundleManifest(
        steppy_bundle=BUNDLE_SCHEMA_VERSION,
        run=BundleRun(
            run_id=run_id,
            created_at=created_at,
            steppy_version=__version__,
            executor=executor,
            git=git or BundleGit(),
            spec=_spec_from_sidecar(sidecar),
            environment=environment,
            ci=ci,
        ),
        tests=tests,
        files=files,
        coverage=BundleCoverage(run_id=run_id, records=coverage_records) if coverage_records else None,
        truncated=truncated,
        warnings=warnings,
    )
    return Assembly(
        manifest=manifest,
        candidates=candidates,
        junit_xml=junit_xml,
        events=list(events or []),
        warnings=warnings,
    )
