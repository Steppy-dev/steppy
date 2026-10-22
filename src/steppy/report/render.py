""":class:`ReportModel` → a self-contained static site.

The renderer, and nothing else. It knows about Jinja and about which files a site
has; it computes nothing — every number, label and URL was decided in
:mod:`steppy.report.model` and :mod:`steppy.report.assets`. That split is what
lets a control plane consume the model without importing a template engine.

**Two rules this module has to keep.**

Autoescape is **on**. Everything rendered here came out of a bundle, and a failure
tail is arbitrary page content — a page under test that echoes user input can put
``<script>`` into an assertion message. Escaping by default means a forgotten
``| e`` is impossible rather than merely unlikely.

It must **not import** :mod:`steppy.guide`. That module builds a module-level
``MarkdownIt``, and ``markdown_it`` is one of the prefixes
``tests/test_no_playwright_import_on_default_path.py`` forbids on the default path.
Nothing here needs markdown: step titles are plain text and tails live in
``<pre>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..contracts.report import ReportModel, ReportScenario, ReportVariant, report_bytes

#: One directory deep, so a scenario page's ``../`` reaches the site root. Every
#: URL in the site is relative, which is what lets it be published at any prefix
#: (a Pages project site lives under ``/<repo>/``, not at ``/``).
SCENARIOS_DIRNAME = "scenarios"

REPORT_JSON = "report.json"
BADGE_JSON = "badge.json"
CSS_NAME = "report.css"

#: Present for the same reason ``steppy build`` emits it: GitHub Pages runs Jekyll
#: by default, which silently drops files and directories beginning with ``_`` —
#: and this site has an ``assets/_failures/`` directory.
NOJEKYLL = ".nojekyll"

_ROBOTS = """# A Steppy run report. Not documentation, and not for indexing.
#
# The <meta name="robots" content="noindex,nofollow,noarchive"> on every page is
# the real control: a crawler blocked here never fetches a page and therefore
# never learns it is noindex, so a link from elsewhere could still be listed.
# This file is belt and braces, and matches what `steppy build` writes in its
# private SEO mode so the two sites do not diverge.
User-agent: *
Disallow: /
"""


@dataclass
class RenderResult:
    """Which files the site has, keyed by site-relative path.

    Values use the same vocabulary as ``build.BuildAssembler``:
    ``planned`` | ``generated`` | ``updated`` | ``unchanged``.
    """

    files: dict[str, str] = field(default_factory=dict)

    def count(self, status: str) -> int:
        return sum(1 for value in self.files.values() if value == status)


def format_duration(ms) -> str:
    """Human duration from milliseconds. ``None`` reads as unknown, not as zero."""
    if ms is None:
        return "—"
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_filesize(size) -> str:
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"  # pragma: no cover - unreachable, the loop returns


def variant_label(variant: ReportVariant) -> str:
    """``fr / mobile``, ``fr``, or ``default`` — never an empty heading."""
    parts = [p for p in (variant.locale, variant.layout) if p]
    return " / ".join(parts) if parts else "default"


def matrix_columns(report: ReportModel) -> list[dict]:
    """The (locale, layout) columns of the scenario table.

    Built from the *observed* dimensions, so a scenario deselected by a disjoint
    frontmatter ``layouts:`` does not add a column of empty cells that reads like a
    failure. An unparametrised run gets no columns at all and the table falls back
    to a single outcome cell.
    """
    locales = report.dimensions.get("locales") or []
    layouts = report.dimensions.get("layouts") or []
    if not locales and not layouts:
        return []
    for values in (locales, layouts):
        if not values:
            values.append(None)
    return [
        {"locale": locale, "layout": layout, "label": " / ".join(p for p in (locale, layout) if p)}
        for locale in locales
        for layout in layouts
    ]


def matrix_cell(scenario: ReportScenario, column: dict) -> Optional[str]:
    """The outcome in one matrix cell, or ``None`` when that pair never ran."""
    for variant in scenario.variants:
        if variant.locale == column["locale"] and variant.layout == column["layout"]:
            return variant.outcome
    return None


def make_env(templates_dir: Optional[Path], project_root: Optional[Path]):
    """The report's Jinja environment: autoescaping, with the report's own filters."""
    from .._templating import make_template_env

    env = make_template_env(templates_dir, project_root, autoescape=True)
    env.filters["duration"] = format_duration
    env.filters["filesize"] = format_filesize
    return env


def render_site(
    report: ReportModel,
    *,
    site_dir: Path,
    title: str,
    templates_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    dry_run: bool = False,
) -> RenderResult:
    """Write every page, stylesheet and data file of the site."""
    from .model import scenario_href

    env = make_env(templates_dir, project_root)
    result = RenderResult()

    def write(rel: str, content: str) -> None:
        _write(site_dir / rel, content, result, dry_run, site_dir=site_dir)

    columns = matrix_columns(report)
    write(
        "index.html",
        env.get_template("report/index.html.j2").render(
            report=report,
            title=title,
            page_title=title,
            root="",
            matrix_columns=columns,
            matrix_cell=matrix_cell,
            scenario_href=scenario_href,
        ),
    )

    scenario_template = env.get_template("report/scenario.html.j2")
    for scenario in report.scenarios:
        write(
            # ``scenario_href`` decides the link AND the filename — one definition,
            # and it sanitises, because a slug from a downloaded manifest decides a
            # path on disk here.
            scenario.href or scenario_href(scenario.slug),
            scenario_template.render(
                report=report,
                scenario=scenario,
                title=title,
                page_title=f"{scenario.slug} — {title}",
                # One level deep, so ``../`` is the site root.
                root="../",
                variant_label=variant_label,
            ),
        )

    if report.coverage.tier != "none":
        write(
            "coverage.html",
            env.get_template("report/coverage.html.j2").render(
                coverage=_coverage_context(report),
                bucket=_bucket_getter(report),
                title=title,
                page_title=f"Coverage — {title}",
                root="",
            ),
        )
        write(BADGE_JSON, _badge_json(report))

    # Through the loader, so a project override at templates/report/report.css wins
    # by the same precedence rule as every other template.
    write(f"assets/{CSS_NAME}", env.get_template("report/report.css").render())

    write(REPORT_JSON, report_bytes(report).decode("utf-8"))
    write(NOJEKYLL, "")
    write("robots.txt", _ROBOTS)
    return result


def _coverage_context(report: ReportModel):
    """The coverage view plus the one derived flag its template needs.

    ``capabilities_strength`` lives on the model's ``capabilities`` block, not on
    the coverage block, and the template should not have to reach across for it.
    """

    class _Context:
        def __init__(self, coverage, strength):
            self._coverage = coverage
            self.capabilities_strength = strength

        def __getattr__(self, name):
            return getattr(self._coverage, name)

    return _Context(report.coverage, report.capabilities.strength_reliable)


def _bucket_getter(report: ReportModel):
    """``bucket(kind, name)`` over the carried gap buckets.

    Tolerant of an absent bucket on purpose: the buckets come from
    ``compute_gaps`` verbatim, and a tier that could not produce one (or a future
    version that renames one) should render an empty section rather than raise
    inside a template.
    """

    def bucket(kind: str, name: str) -> list:
        section = report.coverage.buckets.get(kind)
        if not isinstance(section, dict):
            return []
        rows = section.get(name)
        return rows if isinstance(rows, list) else []

    return bucket


def _badge_json(report: ReportModel) -> str:
    """The shields endpoint object, kept as its own file at the site root.

    Separate from ``report.json`` so a README's
    ``https://img.shields.io/endpoint?url=…`` never has to change, and so the
    badge stays readable by shields without it parsing a whole report.
    """
    import json

    badge = report.coverage.badge
    message = "n/a" if badge.percent is None else f"{badge.percent}%"
    payload = {"schemaVersion": 1, "label": badge.label, "message": message, "color": badge.color}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write(path: Path, content: str, result: RenderResult, dry_run: bool, *, site_dir: Path) -> None:
    """Write *content*, recording what happened under its site-relative key.

    Same shape and vocabulary as ``build.BuildAssembler._write_file`` — including
    ``unchanged`` — so the CLI summary reads identically for both commands. The key
    is site-relative because that is what a reader can act on; an absolute temp
    path in a CI log tells them nothing.
    """
    key = _site_key(path, site_dir)
    if dry_run:
        result.files[key] = "planned"
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                result.files[key] = "unchanged"
                return
        except (OSError, UnicodeDecodeError):
            pass
        result.files[key] = "updated"
    else:
        result.files[key] = "generated"
    path.write_text(content, encoding="utf-8", newline="\n")


def _site_key(path: Path, site_dir: Path) -> str:
    try:
        return path.relative_to(site_dir).as_posix()
    except ValueError:  # pragma: no cover - every writer stays inside the site
        return path.as_posix()
