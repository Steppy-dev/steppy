"""Orchestration: a run in, a static site out.

The order matters in one place and is otherwise unremarkable. Assets are planned
and copied **before** the model is built, because the model needs the URL each
capture ended up at; and the target directory is validated **before** anything is
removed, because ``-o`` is a path off the command line and one typo away from
somewhere that matters.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from ..contracts.report import ReportModel, ReportSourceInfo

#: Default site location. Deliberately *not* a ``config.output.*`` key: this root
#: has one writer and no automated reader (a CI job names the path in its own
#: upload step), and ``OutputConfig`` already carries a deprecated-and-inert
#: ``reports_dir`` — a live ``report_dir`` one letter away would be a permanent
#: trap in every future review of that file.
DEFAULT_SITE_SUBDIR = "report"

#: Files whose presence means "this is a directory that matters, not a stale site".
#: A second lock on rmtree: even ``--force`` will not delete a project.
GUARDED_NAMES = ("config.yaml", "pyproject.toml", ".git", "scenarios", "src")

#: The site's own marker. ``report.json`` is already written at the root and
#: self-identifies via ``steppy_report``, so there is no extra file to keep in sync.
MARKER_NAME = "report.json"


class ReportError(Exception):
    """The report could not be produced. Always actionable."""


@dataclass
class ReportResult:
    site_dir: Path
    model: ReportModel
    files: dict[str, str] = field(default_factory=dict)
    copied: int = 0
    copied_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return self.model.totals.verdict

    def count(self, status: str) -> int:
        return sum(1 for value in self.files.values() if value == status)


def build_report(
    *,
    project_root: Path,
    bundle: Optional[Path] = None,
    site_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    run_id: Optional[str] = None,
    screenshots: str = "failed",
    badge_surface: str = "all",
    badge_label: Optional[str] = None,
    title: Optional[str] = None,
    templates_dir: Optional[Path] = None,
    non_ui: Sequence[str] = (),
    force: bool = False,
    dry_run: bool = False,
) -> ReportResult:
    """Render one run to a self-contained static site."""
    from .. import __version__
    from ..coverage.gaps import SURFACES
    from .assets import POLICIES, plan_assets
    from .coverage_source import resolve_coverage
    from .model import build_model
    from .render import render_site
    from .source import ReportSourceError, open_source

    # Validated before anything is read or removed: an unusable flag should cost
    # the caller a message, not a half-written site. Both are checked here rather
    # than only in Typer so a programmatic caller gets the same guarantee — and
    # an unknown surface would otherwise render a silently empty badge, which is
    # the worst of the three outcomes.
    if screenshots not in POLICIES:
        raise ReportError(f"--screenshots must be one of {', '.join(POLICIES)} (got {screenshots!r})")
    if badge_surface not in SURFACES:
        raise ReportError(f"--badge-surface must be one of {', '.join(SURFACES)} (got {badge_surface!r})")

    try:
        source = open_source(bundle, project_root=project_root, output_dir=output_dir, run_id=run_id)
    except ReportSourceError as e:
        raise ReportError(str(e)) from e

    target = Path(site_dir) if site_dir else project_root / "output" / DEFAULT_SITE_SUBDIR
    target = target.resolve()
    _check_target(target, force=force, dry_run=dry_run)

    warnings: list[str] = list(source.warnings)
    warnings.extend(_placement_warnings(target, project_root))

    plan = plan_assets(source.manifest, site_dir=target, policy=screenshots)
    warnings.extend(plan.warnings)

    if not dry_run:
        _reset(target)
        _, fetch_warnings = source.fetch(into=plan.into)
        warnings.extend(fetch_warnings)

    coverage = resolve_coverage(
        source,
        project_root=project_root,
        non_ui=non_ui,
        surface=badge_surface,
        badge_label=badge_label,
        href="coverage.html",
    )

    attempts = _timeline(source)
    model = build_model(
        manifest=source.manifest,
        source=ReportSourceInfo(kind=source.kind, label=source.label),
        attempts=attempts,
        coverage=coverage,
        assets=plan.urls,
        screenshots_policy=screenshots,
        steppy_version=__version__,
        extra_warnings=warnings,
    )

    rendered = render_site(
        model,
        site_dir=target,
        title=title or _default_title(model),
        templates_dir=templates_dir,
        project_root=project_root,
        dry_run=dry_run,
    )
    return ReportResult(
        site_dir=target,
        model=model,
        files=rendered.files,
        copied=plan.copies,
        copied_bytes=plan.bytes,
        warnings=warnings,
    )


def _timeline(source) -> dict:
    """``{nodeid: [TestAttempt]}`` from the run's event JSONL, or ``{}``.

    Absent step events are a *capability*, not a failure: they exist only when the
    run set ``STEPPY_STEP_EVENTS=1`` (which ``steppy run --bundle`` does), and a
    tree generated before the markers existed emits none either. The model records
    which, and the page says so.
    """
    from ..bundle.results import parse_event_lines, steps_from_records

    wanted = {f.path for f in source.manifest.files if f.kind == "events"}
    if not wanted:
        return {}
    fetched, _warnings = source.fetch(texts=wanted)
    texts = [(name, payload.decode("utf-8", errors="replace")) for name, payload in sorted(fetched.items())]
    records, _parse_warnings = parse_event_lines(texts)
    return steps_from_records(records)


def _default_title(model: ReportModel) -> str:
    branch = model.run.git.branch
    return f"Steppy run report — {branch}" if branch else "Steppy run report"


def _check_target(target: Path, *, force: bool, dry_run: bool) -> None:
    """Refuse to write where a mistake would be expensive.

    ``steppy report -o <dir>`` takes its path from the command line, so ``-o .`` in
    a project root is one keystroke away. Three checks, and the **order matters**:

    1. Our own marker wins first. A directory holding ``report.json`` with a
       ``steppy_report`` key is a previous render of this report, and re-rendering
       into it is the normal case. This has to come first because the site itself
       writes a ``scenarios/`` directory — which is also one of the signals below
       that a directory is a *project*. Checking that first would refuse every
       second render, permanently.
    2. A directory that looks like a project (``config.yaml`` / ``pyproject.toml``
       / ``.git`` / ``scenarios`` / ``src``) is refused **even with ``--force``**.
       Nothing about rendering a report justifies deleting one of those. A
       directory cannot become both, because step 2 refuses the first render into
       a project before step 1 could ever have written a marker there.
    3. Otherwise a non-empty directory needs ``--force``.
    """
    if not target.exists():
        return
    if not target.is_dir():
        raise ReportError(f"{target} exists and is not a directory")

    if _looks_like_a_report(target):
        return

    guarded = [name for name in GUARDED_NAMES if (target / name).exists()]
    if guarded:
        raise ReportError(
            f"refusing to write a report into {target} — it contains {', '.join(sorted(guarded))}, "
            "which makes it look like a project rather than a report directory. "
            "Point -o at a dedicated directory (the default is output/report)."
        )

    if dry_run or not any(target.iterdir()) or force:
        return
    raise ReportError(
        f"{target} is not empty and does not look like a Steppy report "
        f"(no {MARKER_NAME} with a 'steppy_report' key). Pass --force to replace its contents, "
        "or point -o somewhere else."
    )


def _looks_like_a_report(target: Path) -> bool:
    import json

    marker = target / MARKER_NAME
    if not marker.is_file():
        return False
    try:
        return "steppy_report" in json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def _reset(target: Path) -> None:
    """Clear the site so a removed scenario's page does not linger.

    Only ever reached after :func:`_check_target` approved the path.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def _placement_warnings(target: Path, project_root: Path) -> list[str]:
    """Warn when the site lands somewhere ``bundle create`` will archive.

    ``output/report/`` is skipped by the bundler by name. Anywhere else under
    ``output/`` is not, so a later ``steppy bundle create`` would archive the whole
    site — including every screenshot it copied — and roughly double the bundle.
    """
    output = (project_root / "output").resolve()
    default = (output / DEFAULT_SITE_SUBDIR).resolve()
    if target == default:
        return []
    try:
        inside = target.is_relative_to(output)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return []
    if not inside:
        return []
    return [
        f"the report is being written to {target}, which is inside output/ but is not "
        f"output/{DEFAULT_SITE_SUBDIR} — a later `steppy bundle create` will archive it as run "
        "artifacts, roughly doubling the bundle's screenshot payload"
    ]
