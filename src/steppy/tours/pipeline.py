"""The entry point ``steppy generate`` and ``steppy tours`` share.

:func:`prepare_tours` does everything that can fail — parsing, compiling,
resolving the build id, serializing, self-checking, guarding every target and
reading every marker — **before** anything is written, so a refusal never
leaves half an output behind. :meth:`TourPipeline.emit` then writes.

Tours are always compiled from **every** scenario in the directory, even when
``generate`` was asked for one file or a tag: a manifest must never depend on
CLI arguments.

``mode="check"`` (``steppy tours --check``) plans only the app-assets
targets — the copies an app commits — and compares their *content* for the
build id each one records: the canonical tree is a gitignored build output, and
committed files always carry an older id under ``git-sha`` or a CI-stamped id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from ..buildid import BuildIdError, is_build_id, resolve_build_id
from ..captures import DEFAULT_GUIDES_DIR, DEFAULT_SCREENSHOTS_DIR, resolve_output_dir
from ..contracts.tours import TourDataInvalid
from ..models import SteppyConfig, Scenario
from ..naming import TOURS_MANIFEST_FILENAME
from ..parser import ParseError, parse_scenario_file, validate_scenario_layouts, validate_scenario_roles
from .compiler import compile_tours, opted_in
from .diagnostics import Diagnostic, sort_diagnostics
from .emit import (
    DirPlan,
    FileStatus,
    TourArtifacts,
    apply_dir,
    plan_dir,
    remove_build_dir,
    render_artifacts,
    sibling_build_dirs,
)
from .model import ReplayPlan, TourCompileResult
from .paths import TourPathError, check_emit_dir, read_marker
from .settings import ToursSettings, settings_diagnostics

Mode = Literal["generate", "tours", "validate", "check"]


@dataclass
class TourPipeline:
    settings: ToursSettings
    diagnostics: list[Diagnostic] = field(default_factory=list)
    result: Optional[TourCompileResult] = None
    build_id: Optional[str] = None
    artifacts: Optional[TourArtifacts] = None
    plans: list[DirPlan] = field(default_factory=list)
    stale_builds: list[Path] = field(default_factory=list)
    replay_plans: dict[str, ReplayPlan] = field(default_factory=dict)
    #: ``check`` mode: app-assets dir → the build id its manifest records (None: no tour files there).
    checked: dict[Path, Optional[str]] = field(default_factory=dict)
    #: ``check`` mode: why the configured build id could not be resolved here, if it could not.
    build_id_error: Optional[str] = None

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def statuses(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for plan in self.plans:
            for status in plan.statuses:
                out[str(status.path)] = status.status
        for directory in self.stale_builds:
            out[str(directory)] = "removed"
        return out

    def emit(self, dry_run: bool = False) -> dict[str, str]:
        """Write every planned file (unless ``dry_run``); return path → status."""
        statuses = self.statuses()
        if dry_run or self.has_errors:
            return statuses
        for plan in self.plans:
            apply_dir(plan)
        for directory in self.stale_builds:
            remove_build_dir(directory)
        return statuses


def _err(code: str, message: str, scenario: Optional[str] = None) -> Diagnostic:
    return Diagnostic("error", code, message, scenario=scenario)


def _read_dir(scenarios_dir: Path, diagnostics: list[Diagnostic]) -> list[Scenario]:
    out = []
    for path in sorted(scenarios_dir.glob("*.md")):
        try:
            out.append(parse_scenario_file(path))
        except ParseError as exc:
            first = str(exc).splitlines()[0] if str(exc) else repr(exc)
            diagnostics.append(
                _err(
                    "scenario-unreadable",
                    f"{path.name} does not parse ({first}); in-app tours are compiled from every scenario, "
                    "so the tour set would be incomplete",
                )
            )
    return out


def prepare_tours(
    config: SteppyConfig,
    *,
    project_root: Path,
    scenarios_dir: Path,
    selected: Optional[list[Scenario]] = None,
    all_scenarios: Optional[list[Scenario]] = None,
    build_id_override: Optional[str] = None,
    mode: Mode = "generate",
) -> TourPipeline:
    root = Path(project_root).resolve()
    settings = ToursSettings.from_config(config, root)
    pipeline = TourPipeline(settings=settings)
    diags = pipeline.diagnostics
    diags.extend(settings_diagnostics(config, settings))

    scenarios = all_scenarios if all_scenarios is not None else _read_dir(scenarios_dir, diags)
    by_slug = {s.slug: s for s in scenarios}

    resolved_dir = scenarios_dir.resolve()
    selected_slugs = None
    if selected is not None:
        selected_slugs = {s.slug for s in selected}
        for s in opted_in(selected, settings):
            path = Path(s.file_path).resolve() if s.file_path else None
            if path is None or path.parent != resolved_dir:
                diags.append(
                    _err(
                        "scenario-unreadable",
                        f"{s.slug} opts into tours but is not in {scenarios_dir}; tours are compiled from that "
                        "directory, so run the command with --scenarios-dir pointing at it",
                        scenario=s.slug,
                    )
                )
            by_slug.setdefault(s.slug, s)

    roles, layouts = set(config.apps), set(config.screenshot.layouts)
    for s in opted_in(list(by_slug.values()), settings):
        if selected_slugs is not None and s.slug in selected_slugs:
            continue  # the caller already validated the selection
        for problem in validate_scenario_roles(s, roles) + validate_scenario_layouts(s, layouts):
            diags.append(_err("scenario-unreadable", f"{problem}", scenario=s.slug))

    result = compile_tours(list(by_slug.values()), settings)
    pipeline.result = result
    diags.extend(result.diagnostics)
    if not result.tours and not result.has_errors:
        diags.append(
            Diagnostic(
                "warning",
                "no-tours",
                "tours are enabled but no scenario opts in (`tour: true`); an empty manifest is written",
            )
        )
    if settings.replay:
        pipeline.replay_plans = {
            slug: plan for slug, plan in result.replay_plans.items() if selected_slugs is None or slug in selected_slugs
        }
    if pipeline.has_errors or mode == "validate":
        pipeline.diagnostics[:] = sort_diagnostics(diags)
        return pipeline

    owned = [
        resolve_output_dir(root, config.output.screenshots_dir, DEFAULT_SCREENSHOTS_DIR),
        resolve_output_dir(root, config.output.guides_dir, DEFAULT_GUIDES_DIR),
        resolved_dir,
        root / "tests",
        root / ".steppy",
        root / ".auth",
        root / ".git",
    ]

    try:
        pipeline.build_id = resolve_build_id(settings.build_id_raw, root, override=build_id_override)
    except BuildIdError as exc:
        if mode == "check":
            pipeline.build_id_error = str(exc)  # only shown in a note: check compares recorded ids
        else:
            diags.append(_err("build-id", str(exc)))
            pipeline.diagnostics[:] = sort_diagnostics(diags)
            return pipeline

    if mode == "check":
        _prepare_check(pipeline, result, root, owned)
        pipeline.diagnostics[:] = sort_diagnostics(diags)
        return pipeline

    try:
        pipeline.artifacts = render_artifacts(result, settings, pipeline.build_id)
    except TourDataInvalid as exc:
        diags.append(_err("limit-exceeded", str(exc)))
        pipeline.diagnostics[:] = sort_diagnostics(diags)
        return pipeline

    try:
        check_emit_dir(
            settings.tours_root,
            project_root=root,
            owned_roots=[*owned, *settings.app_assets_dirs],
            label="output.tours_dir",
        )
        canonical = settings.tours_root / pipeline.build_id
        pipeline.plans.append(plan_dir(canonical, pipeline.artifacts))
        pipeline.stale_builds = sibling_build_dirs(settings.tours_root, pipeline.build_id)
        for directory in settings.app_assets_dirs:
            others = [d for d in settings.app_assets_dirs if d != directory]
            check_emit_dir(
                directory,
                project_root=root,
                owned_roots=[*owned, settings.tours_root, *others],
                label="tours app-assets target",
            )
            if not directory.parent.is_dir():
                message = f"the app-assets target {directory} has no parent directory (is the app checked out?)"
                if mode == "generate":
                    diags.append(Diagnostic("warning", "app-assets-skipped", message + "; skipped"))
                    continue
                diags.append(_err("target-path", message))
                continue
            pipeline.plans.append(plan_dir(directory, pipeline.artifacts))
    except TourPathError as exc:
        diags.append(_err("target-path", str(exc)))
    pipeline.diagnostics[:] = sort_diagnostics(diags)
    return pipeline


def _prepare_check(pipeline: TourPipeline, result: TourCompileResult, root: Path, owned: list[Path]) -> None:
    """Plan the app-assets targets only, each against the build id its own manifest records."""
    settings = pipeline.settings
    diags = pipeline.diagnostics
    rendered: dict[str, TourArtifacts] = {}
    for directory in settings.app_assets_dirs:
        manifest_path = directory / TOURS_MANIFEST_FILENAME
        try:
            others = [d for d in settings.app_assets_dirs if d != directory]
            check_emit_dir(
                directory,
                project_root=root,
                owned_roots=[*owned, settings.tours_root, *others],
                label="tours app-assets target",
            )
            if not directory.parent.is_dir():
                diags.append(
                    _err(
                        "target-path",
                        f"the app-assets target {directory} has no parent directory (is the app checked out?)",
                    )
                )
                continue
            previous = read_marker(directory) if directory.is_dir() else None
            if previous is None:
                pipeline.checked[directory] = None
                pipeline.plans.append(DirPlan(directory=directory, statuses=[FileStatus(manifest_path, "missing")]))
                continue
            recorded = previous.get("buildId")
            if not is_build_id(recorded):
                diags.append(
                    _err("build-id", f"{manifest_path} records the build id {recorded!r}, which is not a valid one")
                )
                continue
            pipeline.checked[directory] = recorded
            if recorded not in rendered:
                rendered[recorded] = render_artifacts(result, settings, recorded)
            pipeline.plans.append(plan_dir(directory, rendered[recorded]))
        except TourDataInvalid as exc:
            diags.append(_err("limit-exceeded", str(exc)))
        except TourPathError as exc:
            diags.append(_err("target-path", str(exc)))
