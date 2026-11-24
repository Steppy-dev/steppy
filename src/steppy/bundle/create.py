"""Orchestration for ``steppy bundle create`` / ``inspect`` / ``verify``.

The only module here that knows about a project's config, and the one that
decides a run's identity.

:func:`plan_bundle` is the half ``steppy bundle upload`` shares — see
:class:`BundlePlan`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import provenance as _provenance
from ..contracts.bundle import BundleManifest, parse_manifest
from ..runmeta import EVENTS_SUBDIR, JUNIT_SUBDIR, bundle_dirs, new_run_id, read_run_sidecar
from . import archive as _archive
from . import manifest as _manifest
from . import results as _results
from .archive import ArchiveError, ArchiveMember

MANIFEST_NAME = "manifest.json"


class BundleError(Exception):
    """A user-facing failure — always says what to do next."""


@dataclass
class BundlePlan:
    """A bundle decided but not yet written.

    Everything :func:`create_bundle` knows just before it starts writing bytes.
    Split out because ``steppy bundle upload`` needs exactly this and no archive:
    the upload protocol is per-file and content-addressed, so a tarball would be
    written only to be read back and thrown away.

    :attr:`assembly` carries the ``Candidate`` list, whose ``source`` paths are
    the *only* mapping from a manifest entry back to the bytes it describes.
    Reach it through :meth:`members`, never by re-deriving a path from
    ``BundleFile.path`` — the generated ``junit.xml`` and ``events/*.jsonl``
    members have no file under ``output/`` at all.
    """

    assembly: _manifest.Assembly
    run_id: str
    run_id_source: str
    fmt: str
    #: ``recorded`` (the run's sidecar) or ``detected`` (a run before 0.10, or
    #: none recorded): where ``manifest.run``'s git, CI and environment came from.
    metadata_source: str = "recorded"
    #: What the flags changed in that metadata, for the command to print.
    overrides: list[_provenance.Change] = field(default_factory=list)

    @property
    def manifest(self) -> BundleManifest:
        return self.assembly.manifest

    def members(self, target: Optional[Path] = None) -> list[ArchiveMember]:
        """Every archive member, manifest first. See :func:`archive_members`."""
        return archive_members(self.assembly, target)


@dataclass
class BundleResult:
    path: Optional[Path]
    manifest: BundleManifest
    run_id: str
    run_id_source: str
    fmt: str
    compressed_size: int = 0
    warnings: list[str] = field(default_factory=list)
    overrides: list[_provenance.Change] = field(default_factory=list)

    @property
    def payload_size(self) -> int:
        return sum(f.size for f in self.manifest.files)


@dataclass(frozen=True)
class Roots:
    """Where this project's artifacts actually live.

    Note "actually". The directory names below are the ones the *writers* use.
    Since FLAWS §4.6 that is the same thing ``config.output`` declares for
    screenshots, guides and coverage records — see :func:`resolve_roots`.
    """

    output: Path
    screenshots_dirname: str = "screenshots"
    coverage_dirname: str = "coverage"
    guides_dirname: str = "guides"
    reports_dirname: str = "reports"
    locales: frozenset[str] = frozenset()
    layouts: frozenset[str] = frozenset()


def _escapes_output(configured: str, output_dir: Path) -> bool:
    """True when a configured artifact root resolves outside ``output/``."""
    try:
        return not (output_dir.parent / configured).resolve().is_relative_to(output_dir.resolve())
    except OSError:
        return True


def _dirname_under_output(configured: str, output_dir: Path, default: str) -> str:
    """Reduce a configured path like ``./output/coverage`` to ``coverage``.

    Falls back to the default when the project pointed the root outside
    ``output/`` — the bundle archives ``output/`` and cannot reach beyond it.
    The caller warns in that case (:func:`resolve_roots`); silently defaulting
    would have the bundler report an empty directory as an empty run.
    """
    try:
        resolved = (output_dir.parent / configured).resolve()
        rel = resolved.relative_to(output_dir.resolve())
    except (OSError, ValueError):
        return default
    parts = rel.parts
    return parts[0] if parts else default


def resolve_roots(project_root: Path, output_dir: Path) -> tuple[Roots, list[str]]:
    """Resolve the artifact roots and the project's declared locales/layouts.

    ``config.output.screenshots_dir`` / ``guides_dir`` and
    ``coverage.records_dir`` are all **honoured**, because since FLAWS §4.6 the
    writers honour them too: the generated conftest resolves the capture root
    from config at runtime, ``steppy guide`` defaults both of its path flags
    from it, and ``steppy build`` falls back to it. Following them here is what
    keeps the bundler pointed at the directory the run actually wrote.

    What the bundler cannot do is reach outside ``output/`` — it archives that
    tree and nothing else. A root configured elsewhere is therefore reported as
    a warning and skipped rather than silently producing an empty section: a
    consumer must be able to tell "no guides were built" from "the guides are
    somewhere this archive can't see".

    ``reports_dir`` is not consulted: it is deprecated and inert (see
    :class:`steppy.models.OutputConfig`) — nothing has ever written
    ``output/reports``.

    A missing or invalid config is not fatal: the defaults are what the vast
    majority of projects use, and refusing to bundle a finished run because its
    config no longer parses would be a poor trade.
    """
    warnings: list[str] = []
    try:
        from ..config import load_config

        config = load_config(project_root / "config.yaml")
    except Exception as e:
        # First line only: ConfigError carries a multi-line hint aimed at
        # someone editing config.yaml, which is not what this reader is doing.
        reason = str(e).splitlines()[0]
        warnings.append(f"config.yaml not loaded ({reason}); assuming default output paths and no declared locales")
        return Roots(output=output_dir), warnings

    roots = Roots(
        output=output_dir,
        screenshots_dirname=_dirname_under_output(config.output.screenshots_dir, output_dir, "screenshots"),
        guides_dirname=_dirname_under_output(config.output.guides_dir, output_dir, "guides"),
        coverage_dirname=(
            _dirname_under_output(config.coverage.records_dir, output_dir, "coverage")
            if config.coverage
            else "coverage"
        ),
        locales=frozenset(config.locales or ()),
        layouts=frozenset(config.screenshot.layouts.keys()),
    )

    outside = {
        "output.screenshots_dir": config.output.screenshots_dir,
        "output.guides_dir": config.output.guides_dir,
        **({"coverage.records_dir": config.coverage.records_dir} if config.coverage else {}),
    }
    escaped = [f"config.{name} ({value})" for name, value in outside.items() if _escapes_output(value, output_dir)]
    if escaped:
        warnings.append(
            f"{', '.join(escaped)} resolves outside {output_dir}; a bundle archives the output/ tree only, "
            "so those artifacts are NOT in this archive"
        )
    return roots, warnings


def resolve_run_id(
    project_root: Path, output_dir: Path, explicit: Optional[str], coverage_dirname: str = "coverage"
) -> tuple[str, str]:
    """``(run_id, source)`` — the ladder that gives a bundle its identity.

    Explicit wins because a control plane mints its own identity and passes it
    in; everything below it is a convenience for a human at a terminal.
    """
    if explicit:
        return explicit, "explicit"

    sidecar = read_run_sidecar(project_root)
    if sidecar and sidecar.get("run_id"):
        return str(sidecar["run_id"]), "sidecar"

    # Newest recorded run, by name. This is why ids are timestamp-prefixed.
    for parent in (output_dir / EVENTS_SUBDIR, output_dir / coverage_dirname / "raw"):
        if parent.is_dir():
            names = sorted(p.name for p in parent.iterdir() if p.is_dir() and p.name != "_maps")
            if names:
                return names[-1], "discovered"

    return new_run_id(), "minted"


def resolve_metadata(
    project_root: Path,
    sidecar: Optional[dict[str, Any]],
    *,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    executor: Optional[str] = None,
) -> tuple[_provenance.RunMetadata, list[_provenance.Change], str]:
    """``(metadata, overrides, source)`` for ``manifest.run``.

    The run's own record wins over anything detected now: ``steppy run``
    resolved its metadata when it started (flags, then the CI, then the
    repository), and a bundle made later — another CI step, another machine, a
    tree edited since — would describe another checkout. Only a run that
    recorded none (before 0.10, or no sidecar at all) is detected here, without
    the working-tree check, which would describe the tree as it is now. The
    flags then override either.
    """
    from ..config import ConfigError, read_environment

    try:
        _provenance.check_flags(branch=branch, commit=commit, pr=pr, environment=environment)
        recorded = _provenance.from_sidecar(sidecar)
        if recorded is not None:
            base, source = recorded, "recorded"
        else:
            base = _provenance.detect(
                project_root, config_environment=read_environment(project_root / "config.yaml"), dirty=False
            )
            source = "detected"
        meta, changes = _provenance.override(
            base, branch=branch, commit=commit, pr=pr, environment=environment, executor=executor
        )
    except (_provenance.MetadataError, ConfigError) as e:
        raise BundleError(str(e)) from None
    return meta, changes, source


def override_manifest(
    manifest: BundleManifest,
    *,
    run_id: Optional[str] = None,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    executor: Optional[str] = None,
) -> tuple[BundleManifest, list[_provenance.Change]]:
    """An existing bundle's manifest with the flags applied, and what they changed.

    What ``steppy bundle upload <archive>`` sends: an archive describes the run
    that made it, but the flags of the command are the user's last word, the
    same as for a live tree. The archive itself is never rewritten — only the
    manifest sent. ``run.ci`` is left as the archive has it: no flag names a CI.
    Fields of a newer producer survive (``model_copy``, never a rebuild).
    """
    try:
        base = _provenance.RunMetadata.from_dict(manifest.run.model_dump(mode="json"))
        meta, changes = _provenance.override(
            base, branch=branch, commit=commit, pr=pr, environment=environment, executor=executor
        )
    except _provenance.MetadataError as e:
        raise BundleError(str(e)) from None

    run = manifest.run
    update: dict[str, Any] = {}
    if run_id and run_id != run.run_id:
        changes.insert(0, _provenance.Change("run_id", run.run_id, run_id))
        update["run_id"] = run_id
    if changes:
        git_fields = {k: v for k, v in meta.as_dict()["git"].items() if getattr(run.git, k, None) != v}
        update.update(executor=meta.executor, environment=meta.environment, git=run.git.model_copy(update=git_fields))
    if not update:
        return manifest, []
    return manifest.model_copy(update={"run": run.model_copy(update=update)}), changes


def _excluded_kinds(*, guides: bool, traces: bool, videos: bool, coverage: bool) -> frozenset[str]:
    excluded: set[str] = set()
    for flag, key in ((guides, "guides"), (traces, "traces"), (videos, "videos"), (coverage, "coverage")):
        if not flag:
            excluded |= _manifest.OPTIONAL_KINDS[key]
    return frozenset(excluded)


def plan_bundle(
    *,
    project_root: Path,
    output_dir: Optional[Path] = None,
    run_id: Optional[str] = None,
    fmt: str = _archive.FORMAT_AUTO,
    executor: Optional[str] = None,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    git_sha: Optional[str] = None,
    git_branch: Optional[str] = None,
    guides: bool = True,
    traces: bool = True,
    videos: bool = True,
    coverage: bool = True,
    max_size: int = 0,
) -> BundlePlan:
    """Walk, classify, hash and budget a run's ``output/`` tree.

    Everything :func:`create_bundle` does except writing the archive, and the
    entry point ``steppy bundle upload`` uses instead — see :class:`BundlePlan`.
    ``fmt`` is carried through so a caller can report it; nothing here depends
    on the codec.

    ``branch`` / ``commit`` / ``pr`` / ``environment`` / ``executor`` override
    the run's metadata (:func:`resolve_metadata`); ``git_branch`` / ``git_sha``
    are their earlier names, still accepted.
    """
    project_root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else project_root / "output"

    if not output_dir.is_dir():
        raise BundleError(
            f"no output directory at {output_dir} — run your scenarios first (`steppy run`), then create the bundle"
        )

    sidecar = read_run_sidecar(output_dir.parent)
    # Before the walk: a bad flag should fail in milliseconds, not after hashing
    # a gigabyte of screenshots.
    meta, overrides, metadata_source = resolve_metadata(
        project_root,
        sidecar,
        branch=branch or git_branch,
        commit=commit or git_sha,
        pr=pr,
        environment=environment,
        executor=executor,
    )

    roots, warnings = resolve_roots(project_root, output_dir)
    warnings.extend(meta.warnings)
    resolved_run_id, run_id_source = resolve_run_id(project_root, output_dir, run_id, roots.coverage_dirname)

    events_dir, junit_dir = bundle_dirs(output_dir.parent, resolved_run_id)
    if output_dir.name != "output":
        # A relocated output dir: bundle_dirs assumes <root>/output.
        events_dir = output_dir / EVENTS_SUBDIR / resolved_run_id
        junit_dir = output_dir / JUNIT_SUBDIR / resolved_run_id

    tests, test_warnings = _results.collect_tests(events_dir, junit_dir)
    warnings.extend(test_warnings)
    if not tests:
        warnings.append(
            "no test outcomes recorded — manifest.tests[] is empty. "
            "Re-run with `steppy run` (0.9.1 or later) so per-test event records are captured"
        )

    junit_xml = _results.merge_junit(junit_dir)
    events = [(f"{EVENTS_SUBDIR}/{p.name}", p) for p in _results.read_event_files(events_dir)]
    git, ci = _manifest.contract_metadata(meta)

    assembly = _manifest.build(
        project_root=project_root,
        output_dir=output_dir,
        run_id=resolved_run_id,
        tests=tests,
        locales=roots.locales,
        layouts=roots.layouts,
        screenshots_dirname=roots.screenshots_dirname,
        coverage_dirname=roots.coverage_dirname,
        guides_dirname=roots.guides_dirname,
        reports_dirname=roots.reports_dirname,
        sidecar=sidecar,
        executor=meta.executor,
        git=git,
        ci=ci,
        environment=meta.environment,
        exclude_kinds=_excluded_kinds(guides=guides, traces=traces, videos=videos, coverage=coverage),
        max_size=max_size,
        junit_xml=junit_xml,
        events=events,
        extra_warnings=warnings,
    )

    return BundlePlan(
        assembly=assembly,
        run_id=resolved_run_id,
        run_id_source=run_id_source,
        fmt=fmt,
        metadata_source=metadata_source,
        overrides=overrides,
    )


def create_bundle(
    *,
    project_root: Path,
    output_dir: Optional[Path] = None,
    target: Optional[Path] = None,
    run_id: Optional[str] = None,
    fmt: str = _archive.FORMAT_AUTO,
    executor: Optional[str] = None,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    git_sha: Optional[str] = None,
    git_branch: Optional[str] = None,
    guides: bool = True,
    traces: bool = True,
    videos: bool = True,
    coverage: bool = True,
    max_size: int = 0,
    dry_run: bool = False,
) -> BundleResult:
    """Build a results bundle from a run's ``output/`` tree."""
    # An explicitly named target dictates the codec, so `-o run.tar.zst` can
    # never produce a file whose contents disagree with its name — which is
    # what a silent gzip fallback would do to anything sniffing the extension.
    #
    # Resolved before the walk, deliberately: an unusable codec should fail in
    # milliseconds, not after hashing a gigabyte of screenshots.
    named_fmt = _named_format(target)
    if named_fmt and fmt == _archive.FORMAT_AUTO:
        fmt = named_fmt
    elif named_fmt and named_fmt != fmt:
        raise BundleError(
            f"--format {fmt} contradicts the output filename {Path(target).name!r}; "
            "drop --format, or name the file to match"
        )

    try:
        resolved_fmt = _archive.resolve_format(fmt)
    except ArchiveError as e:
        raise BundleError(str(e)) from e

    plan = plan_bundle(
        project_root=project_root,
        output_dir=output_dir,
        run_id=run_id,
        fmt=resolved_fmt,
        executor=executor,
        branch=branch or git_branch,
        commit=commit or git_sha,
        pr=pr,
        environment=environment,
        guides=guides,
        traces=traces,
        videos=videos,
        coverage=coverage,
        max_size=max_size,
    )

    if dry_run:
        return BundleResult(
            path=None,
            manifest=plan.manifest,
            run_id=plan.run_id,
            run_id_source=plan.run_id_source,
            fmt=resolved_fmt,
            warnings=plan.manifest.warnings,
            overrides=plan.overrides,
        )

    target_path = _resolve_target(target, Path(project_root).resolve(), plan.run_id, resolved_fmt)
    compressed = _archive.write_archive(target_path, plan.members(target_path), fmt=resolved_fmt)

    return BundleResult(
        path=target_path,
        manifest=plan.manifest,
        run_id=plan.run_id,
        run_id_source=plan.run_id_source,
        fmt=resolved_fmt,
        compressed_size=compressed,
        warnings=plan.manifest.warnings,
        overrides=plan.overrides,
    )


def _named_format(target: Optional[Path]) -> Optional[str]:
    """The codec an explicit output filename asks for, if it names one."""
    if target is None:
        return None
    suffix = Path(target).suffix.lower()
    if suffix == ".zst":
        return _archive.FORMAT_ZSTD
    if suffix == ".gz":
        return _archive.FORMAT_GZIP
    return None


def _resolve_target(target: Optional[Path], project_root: Path, run_id: str, fmt: str) -> Path:
    extension = _archive.extension_for(fmt)
    if target is None:
        return project_root / "output" / _manifest.BUNDLES_SUBDIR / f"steppy-bundle-{run_id}{extension}"
    target = Path(target)
    if target.is_dir() or not target.suffix:
        return target / f"steppy-bundle-{run_id}{extension}"
    return target


def manifest_bytes(manifest: BundleManifest) -> bytes:
    """Canonical serialisation — sorted, indented, trailing newline.

    Same convention as ``.steppy/scaffold.json`` and the config schema, so the
    file reads well and diffs cleanly when someone commits one as a fixture.
    """
    payload = manifest.model_dump(mode="json", exclude_none=False)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def archive_members(assembly: _manifest.Assembly, target: Optional[Path] = None) -> list[ArchiveMember]:
    """Archive members, manifest first then everything else in path order.

    The single mapping from a manifest entry's ``path`` back to its bytes, and
    the reason ``steppy bundle upload`` goes through here rather than joining
    ``output_dir`` with each path: ``junit.xml`` and the ``events/*.jsonl``
    members are generated, and two of the three member kinds do not live under
    ``output/`` at all.

    ``target`` is the archive being written, excluded from its own contents.
    ``None`` when nothing is being written (the upload path).
    """
    members = [ArchiveMember(arcname=MANIFEST_NAME, data=manifest_bytes(assembly.manifest))]
    if assembly.junit_xml is not None:
        members.append(ArchiveMember(arcname="junit.xml", data=assembly.junit_xml))
    for arcname, source in sorted(assembly.events):
        members.append(ArchiveMember(arcname=arcname, source=source))
    target_resolved = target.resolve() if target is not None else None
    for candidate in sorted(assembly.candidates, key=lambda c: c.entry.path):
        # Never archive the bundle into itself when --output points inside output/.
        if target_resolved is not None and candidate.source.resolve() == target_resolved:
            continue
        members.append(ArchiveMember(arcname=candidate.entry.path, source=candidate.source))
    return members


def inspect_bundle(path: Path) -> BundleManifest:
    """Read a bundle's manifest without extracting it."""
    path = Path(path)
    if not path.is_file():
        raise BundleError(f"no such bundle: {path}")
    try:
        with _archive.open_archive(path) as tar:
            for member in tar:
                if member.name == MANIFEST_NAME:
                    handle = tar.extractfile(member)
                    if handle is None:
                        break
                    return parse_manifest(json.loads(handle.read().decode("utf-8")))
    except ArchiveError as e:
        raise BundleError(str(e)) from e
    except (OSError, ValueError) as e:
        raise BundleError(f"could not read {path.name}: {e}") from e
    raise BundleError(f"{path.name} contains no {MANIFEST_NAME} — is it a steppy bundle?")


def verify_bundle(path: Path) -> tuple[BundleManifest, list[str]]:
    """Re-hash every member against the manifest. Returns ``(manifest, problems)``.

    Streams: a bundle can be hundreds of megabytes and there is no reason to
    extract it to check it.
    """
    import hashlib

    path = Path(path)
    manifest = inspect_bundle(path)
    expected = {f.path: (f.sha256, f.size) for f in manifest.files}
    seen: set[str] = set()
    problems: list[str] = []
    walked = False  # every member was reached, so the missing-member check means something

    # The manifest read cleanly, so damage from here on is a finding, not a failure.
    try:
        with _archive.open_archive(path) as tar:
            for member in tar:
                if not member.isfile() or member.name == MANIFEST_NAME:
                    continue
                if member.name not in expected:
                    problems.append(f"{member.name}: present in the archive but absent from the manifest")
                    continue
                want_sha, want_size = expected[member.name]
                handle = tar.extractfile(member)
                if handle is None:
                    problems.append(f"{member.name}: unreadable")
                    continue
                digest = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                seen.add(member.name)
                if digest.hexdigest() != want_sha:
                    problems.append(f"{member.name}: sha256 mismatch")
                elif size != want_size:
                    problems.append(f"{member.name}: size mismatch ({size} != {want_size})")
            walked = True
            _archive.read_to_end(tar)
    except ArchiveError as e:
        problems.append(str(e) if walked else f"{e} (the members after the damage were not checked)")

    if walked:
        for missing in sorted(set(expected) - seen):
            problems.append(f"{missing}: listed in the manifest but missing from the archive")
    return manifest, problems
