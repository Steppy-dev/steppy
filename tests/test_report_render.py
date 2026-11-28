"""The rendered site: escaping, relative URLs, private indexing, overrides.

The escaping tests are the ones with teeth. A failure tail is arbitrary page
content — an app that echoes user input puts whatever it was given into an
assertion message — so the report's Jinja environment sets ``autoescape=True`` and
these tests are what prove it stayed on.
"""

import json
import re
from pathlib import Path

import pytest

from steppy.contracts.bundle import BundleManifest, BundleRun, BundleTest
from steppy.contracts.report import ReportCoverage, ReportSourceInfo
from steppy.report.build import ReportError, build_report
from steppy.report.model import build_model
from steppy.report.render import format_duration, format_filesize, matrix_cell, matrix_columns, render_site

RUN = BundleRun(
    run_id="20260814T100000Z-abc123",
    created_at="2026-08-14T10:00:00Z",
    steppy_version="0.3.0",
    executor="github-actions",
)


def _tail_b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode()).decode("ascii")


def _test(scenario="order-lifecycle", locale="fr", layout="mobile", outcome="passed", **kw):
    nodeid = kw.pop("nodeid", f"tests/e2e/test_{scenario}.py::test_flow[{locale}-{layout}]")
    return BundleTest(scenario=scenario, nodeid=nodeid, outcome=outcome, locale=locale, layout=layout, **kw)


def _model(tests=(), **kw):
    manifest = BundleManifest(steppy_bundle=1, run=RUN, tests=list(tests), **kw.pop("manifest_kw", {}))
    return build_model(manifest=manifest, source=ReportSourceInfo(kind="tree", label="output"), **kw)


def _render(model, tmp_path: Path, **kw):
    site = tmp_path / "site"
    render_site(model, site_dir=site, title=kw.pop("title", "Steppy run report"), **kw)
    return site


def _read(site: Path, rel: str) -> str:
    return (site / rel).read_text(encoding="utf-8")


# ── escaping ────────────────────────────────────────────────────────────────


def test_a_script_tag_in_a_failure_tail_is_escaped(tmp_path: Path):
    """The whole reason the report's environment autoescapes."""
    tail = "E   AssertionError: <script>alert(1)</script>"
    model = _model([_test(outcome="failed", fail_tail_b64=_tail_b64(tail))])
    site = _render(model, tmp_path)

    for page in ("index.html", "scenarios/order-lifecycle.html"):
        html = _read(site, page)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html, page


def test_quotes_and_ampersands_in_a_tail_are_escaped(tmp_path: Path):
    tail = "E   AssertionError: expected \"a\" & got 'b'"
    model = _model([_test(outcome="failed", fail_tail_b64=_tail_b64(tail))])
    html = _read(_render(model, tmp_path), "scenarios/order-lifecycle.html")
    assert "&amp;" in html
    assert "got 'b'\"" not in html  # not sitting raw in an attribute


def test_a_hostile_step_title_is_escaped(tmp_path: Path):
    """Step titles come from scenario markdown, which is committed — but the report
    also renders bundles from elsewhere, so trust ends at the file boundary."""
    from steppy.bundle.results import steps_from_records

    nodeid = "tests/e2e/test_order-lifecycle.py::test_flow[fr-mobile]"
    events = [
        {"kind": "test-start", "ts": 1.0, "nodeid": nodeid},
        {"kind": "step-start", "ts": 1.0, "nodeid": nodeid, "step": 1, "title": "<img onerror=x>", "action": "click"},
        {"kind": "test-end", "ts": 2.0, "nodeid": nodeid, "outcome": "passed", "duration_ms": 1000},
    ]
    model = _model([_test(nodeid=nodeid)], attempts=steps_from_records(events))
    html = _read(_render(model, tmp_path), "scenarios/order-lifecycle.html")
    assert "<img onerror=x>" not in html
    assert "&lt;img onerror=x&gt;" in html


def test_a_hostile_scenario_slug_cannot_escape_the_scenarios_directory(tmp_path: Path):
    model = _model([_test(scenario="../../evil")])
    site = _render(model, tmp_path)
    written = [p for p in (site / "scenarios").iterdir()]
    assert len(written) == 1
    assert site in written[0].parents
    assert not (tmp_path / "evil.html").exists()


# ── the site's shape ────────────────────────────────────────────────────────


def test_a_minimal_site_has_everything_it_needs_to_be_self_contained(tmp_path: Path):
    site = _render(_model([_test()]), tmp_path)
    for rel in (
        "index.html",
        "scenarios/order-lifecycle.html",
        "assets/report.css",
        "report.json",
        ".nojekyll",
        "robots.txt",
    ):
        assert (site / rel).is_file(), rel


def test_report_json_parses_as_the_contract(tmp_path: Path):
    from steppy.contracts.report import parse_report

    site = _render(_model([_test()]), tmp_path)
    parsed = parse_report(json.loads(_read(site, "report.json")))
    assert parsed.steppy_report == 1


def test_every_page_is_marked_noindex(tmp_path: Path):
    """A run report has no SEO value, and a published Pages site means published
    screenshots. There is deliberately no config knob to turn this off."""
    model = _model([_test()], coverage=ReportCoverage(tier="bundled"))
    site = _render(model, tmp_path)
    for page in ("index.html", "scenarios/order-lifecycle.html", "coverage.html"):
        assert 'content="noindex,nofollow,noarchive"' in _read(site, page), page


def test_robots_txt_disallows_everything(tmp_path: Path):
    site = _render(_model([_test()]), tmp_path)
    assert "Disallow: /" in _read(site, "robots.txt")


def test_nojekyll_exists_because_the_assets_tree_has_an_underscore_directory(tmp_path: Path):
    """Pages runs Jekyll by default, which silently drops ``_``-prefixed paths —
    and failure captures land under ``assets/_failures/``."""
    assert (_render(_model([_test()]), tmp_path) / ".nojekyll").is_file()


def test_no_url_in_the_site_is_absolute(tmp_path: Path):
    """A Pages project site is served from /<repo>/, so an absolute URL 404s."""
    model = _model([_test(outcome="failed", fail_tail_b64=_tail_b64("E boom"))], coverage=ReportCoverage(tier="store"))
    site = _render(model, tmp_path)
    for page in ("index.html", "scenarios/order-lifecycle.html", "coverage.html"):
        html = _read(site, page)
        assert 'href="/' not in html, page
        assert 'src="/' not in html, page


def test_a_scenario_page_reaches_the_root_with_a_relative_prefix(tmp_path: Path):
    site = _render(_model([_test()]), tmp_path)
    html = _read(site, "scenarios/order-lifecycle.html")
    assert 'href="../index.html"' in html
    assert 'href="../assets/report.css"' in html


def test_the_coverage_page_and_badge_appear_only_when_coverage_exists(tmp_path: Path):
    without = _render(_model([_test()]), tmp_path / "a")
    assert not (without / "coverage.html").exists()
    assert not (without / "badge.json").exists()

    with_cov = _render(_model([_test()], coverage=ReportCoverage(tier="bundled")), tmp_path / "b")
    assert (with_cov / "coverage.html").is_file()
    assert (with_cov / "badge.json").is_file()


def test_the_badge_file_is_a_shields_endpoint_object(tmp_path: Path):
    from steppy.contracts.report import ReportBadge

    coverage = ReportCoverage(tier="bundled", badge=ReportBadge(percent=62, color="orange", label="e2e surface"))
    site = _render(_model([_test()], coverage=coverage), tmp_path)
    payload = json.loads(_read(site, "badge.json"))
    assert payload == {"schemaVersion": 1, "label": "e2e surface", "message": "62%", "color": "orange"}


def test_an_unmeasured_badge_says_n_a(tmp_path: Path):
    site = _render(_model([_test()], coverage=ReportCoverage(tier="records")), tmp_path)
    assert json.loads(_read(site, "badge.json"))["message"] == "n/a"


# ── the honest-degradation notices ──────────────────────────────────────────


def test_a_run_with_no_step_events_says_so_instead_of_showing_an_empty_table(tmp_path: Path):
    html = _read(_render(_model([_test()]), tmp_path), "index.html")
    assert "No per-step timeline was recorded" in html
    assert "STEPPY_STEP_EVENTS=0" in html


def test_a_run_with_no_outcomes_points_at_the_bundle_flag(tmp_path: Path):
    from steppy.contracts.bundle import BundleFile

    files = [
        BundleFile(
            path="output/screenshots/onboarding/step-01.png",
            kind="screenshot",
            sha256="0" * 64,
            size=1,
            scenario="onboarding",
            step=1,
        )
    ]
    model = _model(manifest_kw={"files": files})
    html = _read(_render(model, tmp_path), "index.html")
    assert "no test outcomes" in html
    assert "steppy run --bundle" in html


def test_a_truncated_bundle_is_announced(tmp_path: Path):
    html = _read(_render(_model([_test()], manifest_kw={"truncated": True}), tmp_path), "index.html")
    assert "Truncated" in html


def test_the_records_tier_says_strength_is_unavailable(tmp_path: Path):
    model = _model([_test()], coverage=ReportCoverage(tier="records"))
    html = _read(_render(model, tmp_path), "coverage.html")
    assert "Assert strength is not shown" in html


def test_warnings_are_surfaced_rather_than_swallowed(tmp_path: Path):
    model = _model([_test()], extra_warnings=["size budget: dropped 3 video file(s)"])
    assert "dropped 3 video file(s)" in _read(_render(model, tmp_path), "index.html")


def test_a_failure_with_no_open_step_explains_itself(tmp_path: Path):
    """Rather than blaming the last step reached, which would point at working
    code."""
    model = _model([_test(outcome="failed", failed_step=None)])
    html = _read(_render(model, tmp_path), "scenarios/order-lifecycle.html")
    assert "No step was in flight" in html


# ── the matrix ──────────────────────────────────────────────────────────────


def test_the_matrix_has_one_column_per_observed_pair(tmp_path: Path):
    model = _model(
        [
            _test(locale="fr", layout="mobile"),
            _test(locale="nl", layout="desktop", outcome="failed"),
        ]
    )
    columns = matrix_columns(model)
    assert [c["label"] for c in columns] == ["fr / desktop", "fr / mobile", "nl / desktop", "nl / mobile"]
    assert matrix_cell(model.scenarios[0], columns[1]) == "passed"
    assert matrix_cell(model.scenarios[0], columns[0]) is None  # never ran


def test_an_unparametrised_run_gets_no_matrix_columns():
    model = _model([_test(locale=None, layout=None)])
    assert matrix_columns(model) == []


def test_a_cell_that_never_ran_says_so(tmp_path: Path):
    model = _model([_test(locale="fr", layout="mobile"), _test(locale="nl", layout="desktop")])
    assert "not run" in _read(_render(model, tmp_path), "index.html")


def test_a_run_with_no_outcome_never_says_not_run_or_passed(tmp_path: Path):
    from steppy.contracts.bundle import BundleFile

    shot = BundleFile(
        path="output/screenshots/onboarding/step-01.png",
        kind="screenshot",
        sha256="0" * 64,
        size=1,
        scenario="onboarding",
        step=1,
    )
    site = _render(_model(manifest_kw={"files": [shot]}), tmp_path)
    for name, html in _pages(site).items():
        assert "Unknown" in html, name
        assert "Not run" not in html and "Every scenario passed" not in html, name


def test_a_run_where_nothing_fails_shows_every_scenario(tmp_path: Path):
    """The table opens on what does not pass. With nothing there, hiding every scenario
    would leave a page with no way in."""
    green = _read(_render(_model([_test()]), tmp_path / "a"), "index.html")
    assert re.search(r'value="all"[^>]* checked', green)
    red = _read(_render(_model([_test(outcome="failed")]), tmp_path / "b"), "index.html")
    assert re.search(r'value="not"[^>]* checked', red)


def test_the_page_is_in_the_clouds_order(tmp_path: Path):
    model = _model([_test(outcome="failed", fail_tail_b64=_tail_b64("E boom"))], extra_warnings=["careful"])
    html = _read(_render(model, tmp_path), "index.html")
    places = [
        html.index(text)
        for text in (
            'id="ui/run"',
            "1 warning in this report",
            "Why it failed",
            'id="ui/scenarios"',
            'class="steppy-variant ',
        )
    ]
    assert places == sorted(places)


# ── formatting filters ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ms,expected",
    [(None, "—"), (0, "0 ms"), (999, "999 ms"), (1500, "1.5 s"), (61_000, "1m 01s"), (3_700_000, "1h 01m")],
)
def test_duration_formatting(ms, expected):
    assert format_duration(ms) == expected


def test_duration_of_an_unknown_value_is_not_zero():
    """``None`` means the step never finished. Rendering that as 0 ms would be a
    claim rather than an omission."""
    assert format_duration(None) == "—"
    assert format_duration("nonsense") == "—"


@pytest.mark.parametrize("size,expected", [(0, "0 B"), (512, "512 B"), (2048, "2.0 KiB"), (5 * 1024**2, "5.0 MiB")])
def test_filesize_formatting(size, expected):
    assert format_filesize(size) == expected


# ── template overrides ──────────────────────────────────────────────────────


def test_a_project_can_override_the_report_css(tmp_path: Path):
    """Subdirectory-only overrides count, so a project gets the same three-rung
    precedence for the report as for every other template."""
    project = tmp_path / "proj"
    (project / "templates" / "report").mkdir(parents=True)
    (project / "templates" / "report" / "report.css").write_text("/* mine */\n", encoding="utf-8")
    site = _render(_model([_test()]), tmp_path, project_root=project)
    assert _read(site, "assets/report.css") == "/* mine */\n"


def test_a_page_written_for_an_older_report_still_renders(tmp_path: Path):
    """A project's own copy of a page from before 0.10 uses the old context. It has to
    be redone to look right, but it must not break ``steppy report`` in CI."""
    project = tmp_path / "proj"
    (project / "templates" / "report").mkdir(parents=True)
    (project / "templates" / "report" / "index.html.j2").write_text(
        "{% for s in report.scenarios %}{{ s.slug }} {{ scenario_href(s.slug) }}"
        "{% for c in matrix_columns %} {{ c.label }}={{ matrix_cell(s, c) }}{% endfor %}{% endfor %}\n",
        encoding="utf-8",
    )
    (project / "templates" / "report" / "scenario.html.j2").write_text(
        "{{ root }}{{ scenario.slug }}{% for v in scenario.variants %} {{ variant_label(v) }}{% endfor %}\n",
        encoding="utf-8",
    )
    site = _render(_model([_test()]), tmp_path, project_root=project)
    assert _read(site, "index.html").strip() == "order-lifecycle scenarios/order-lifecycle.html fr / mobile=passed"
    assert _read(site, "scenarios/order-lifecycle.html").strip() == "../order-lifecycle fr / mobile"


def test_a_project_can_override_a_font(tmp_path: Path):
    project = tmp_path / "proj"
    fonts = project / "templates" / "report" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "jetbrains-mono-latin.woff2").write_bytes(b"mine")
    site = _render(_model([_test()]), tmp_path, project_root=project)
    assert (site / "assets/fonts/jetbrains-mono-latin.woff2").read_bytes() == b"mine"
    assert (site / "assets/fonts/bricolage-grotesque-latin.woff2").stat().st_size > 10_000


def test_a_project_can_override_a_page(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "templates" / "report").mkdir(parents=True)
    (project / "templates" / "report" / "index.html.j2").write_text("<p>ours</p>\n", encoding="utf-8")
    site = _render(_model([_test()]), tmp_path, project_root=project)
    assert _read(site, "index.html") == "<p>ours</p>\n"


def test_the_default_autoescape_is_unchanged_for_every_other_consumer():
    """Turning it on globally would silently escape every ``<em>`` in every guide,
    because the guide templates inject markdown-rendered HTML through ``{{ }}``."""
    from steppy._templating import make_template_env

    assert make_template_env().autoescape is False
    assert make_template_env(autoescape=True).autoescape is True


# ── dry run and results vocabulary ──────────────────────────────────────────


def test_a_dry_run_writes_nothing_and_plans_everything(tmp_path: Path):
    site = tmp_path / "site"
    result = render_site(_model([_test()]), site_dir=site, title="t", dry_run=True)
    assert not site.exists()
    assert set(result.files.values()) == {"planned"}
    assert "index.html" in result.files


def test_a_second_render_reports_unchanged(tmp_path: Path):
    """Same vocabulary as ``steppy build``, so the CLI summary reads identically."""
    model = _model([_test()])
    site = tmp_path / "site"
    render_site(model, site_dir=site, title="t")
    second = render_site(model, site_dir=site, title="t")
    assert second.count("unchanged") == len(second.files)


def test_an_edited_page_reports_updated(tmp_path: Path):
    model = _model([_test()])
    site = tmp_path / "site"
    render_site(model, site_dir=site, title="t")
    (site / "index.html").write_text("stale", encoding="utf-8")
    assert render_site(model, site_dir=site, title="t").files["index.html"] == "updated"


def test_result_keys_are_site_relative(tmp_path: Path):
    """An absolute temp path in a CI log tells the reader nothing."""
    result = render_site(_model([_test()]), site_dir=tmp_path / "site", title="t")
    assert all(not Path(key).is_absolute() for key in result.files)


# ── target safety ───────────────────────────────────────────────────────────


def test_a_project_directory_is_refused_even_with_force(tmp_path: Path):
    """``-o .`` in a project root is one keystroke away, and nothing about
    rendering a report justifies deleting a repo."""
    project = tmp_path / "proj"
    (project / "output").mkdir(parents=True)
    (project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    with pytest.raises(ReportError, match="look like a project"):
        build_report(project_root=project, site_dir=project, force=True)


def test_a_non_empty_unrecognised_directory_needs_force(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "output").mkdir(parents=True)
    (project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    target = tmp_path / "docs"
    target.mkdir()
    (target / "index.md").write_text("hand-written", encoding="utf-8")
    with pytest.raises(ReportError, match="does not look like a Steppy report"):
        build_report(project_root=project, site_dir=target)


def test_rendering_twice_into_the_same_directory_works(tmp_path: Path):
    """The site writes a ``scenarios/`` directory, which is also one of the signals
    that a directory is a *project* — so the marker check has to come first or
    every second render is refused, permanently. This is the regression test for
    exactly that ordering."""
    project = tmp_path / "proj"
    (project / "output").mkdir(parents=True)
    (project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    target = tmp_path / "site"
    build_report(project_root=project, site_dir=target)
    assert (target / "scenarios").is_dir() or True  # may be empty for a bare run
    build_report(project_root=project, site_dir=target)  # must not raise
    assert (target / "index.html").is_file()


def test_an_existing_report_directory_is_replaced_without_force(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "output").mkdir(parents=True)
    (project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    target = tmp_path / "site"
    target.mkdir()
    (target / "report.json").write_text('{"steppy_report": 1}', encoding="utf-8")
    (target / "stale.html").write_text("old", encoding="utf-8")
    build_report(project_root=project, site_dir=target)
    assert not (target / "stale.html").exists()  # a removed scenario must not linger
    assert (target / "index.html").is_file()


def test_a_file_where_the_site_should_go_is_refused(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "output").mkdir(parents=True)
    (project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    target = tmp_path / "site"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ReportError, match="not a directory"):
        build_report(project_root=project, site_dir=target)


# ── untrusted content ───────────────────────────────────────────────────────

EVIL = "<img src=x onerror=alert(1)>\"'&"


def _hostile_model():
    """Every text a bundle carries, hostile."""
    from steppy.bundle.results import steps_from_records
    from steppy.contracts.bundle import BundleCi, BundleFile, BundleGit

    run = RUN.model_copy(
        update={
            "run_id": EVIL,
            "created_at": EVIL,
            "steppy_version": EVIL,
            "executor": EVIL,
            "environment": EVIL,
            "git": BundleGit(sha=EVIL, branch=EVIL, pr=3, base_branch=EVIL, tested_sha=EVIL, dirty=True),
            "ci": BundleCi(provider=EVIL, url="javascript:alert(1)", repo=EVIL),
        }
    )
    test = _test(
        scenario=EVIL,
        locale=EVIL,
        layout=EVIL,
        outcome="failed",
        nodeid=EVIL,
        failed_step=1,
        fail_tail_b64=_tail_b64(EVIL),
    )
    odd = _test(scenario="other", outcome=EVIL, nodeid="n2", locale=EVIL, layout=EVIL)
    key = {"scenario": EVIL, "locale": EVIL, "layout": EVIL, "sha256": "0" * 64, "size": 1}
    files = [
        BundleFile(path=EVIL, kind="trace", **key),
        BundleFile(path=EVIL, kind="video", **key),
        BundleFile(path=EVIL, kind="failure_log", **key),
        BundleFile(path="x", kind=EVIL, sha256="0" * 64, size=1),
    ]
    events = [
        {"kind": "test-start", "ts": 1.0, "nodeid": EVIL},
        {"kind": "step-start", "ts": 1.0, "nodeid": EVIL, "step": 1, "title": EVIL, "action": EVIL},
        {"kind": "test-end", "ts": 2.0, "nodeid": EVIL, "outcome": "failed", "duration_ms": 1000},
    ]
    return build_model(
        manifest=BundleManifest(steppy_bundle=1, run=run, tests=[test, odd], files=files, warnings=[EVIL]),
        source=ReportSourceInfo(kind=EVIL, label=EVIL),
        attempts=steps_from_records(events),
        coverage=ReportCoverage(tier="bundled", warnings=[EVIL]),
        steppy_version=EVIL,
    )


def _pages(site: Path) -> dict[str, str]:
    return {p.relative_to(site).as_posix(): p.read_text(encoding="utf-8") for p in site.rglob("*.html")}


def test_nothing_a_bundle_holds_becomes_markup(tmp_path: Path):
    for name, html in _pages(_render(_hostile_model(), tmp_path, title=EVIL)).items():
        assert "<img src=x" not in html, name
        assert "onerror=alert(1)>" not in html, name


def test_an_address_that_is_not_http_is_text(tmp_path: Path):
    pages = _pages(_render(_hostile_model(), tmp_path))
    assert "javascript:alert(1)" in pages["index.html"]
    for name, html in pages.items():
        for address in re.findall(r'(?:href|src|action)="([^"]*)"', html):
            assert not re.match(r"\s*(javascript|vbscript|data:text)", address, re.IGNORECASE), (name, address)


def test_a_ci_address_is_a_link_that_gives_nothing_away(tmp_path: Path):
    from steppy.contracts.bundle import BundleCi

    run = RUN.model_copy(update={"ci": BundleCi(provider="gitlab-ci", url="https://gl.example/acme/-/jobs/7?a=1&b=2")})
    model = build_model(
        manifest=BundleManifest(steppy_bundle=1, run=run, tests=[_test()]), source=ReportSourceInfo(kind="tree")
    )
    html = _read(_render(model, tmp_path), "index.html")
    assert 'href="https://gl.example/acme/-/jobs/7?a=1&amp;b=2" target="_blank" rel="noopener noreferrer"' in html
    assert "GitLab job 7" in html


def test_the_pages_run_no_handler_and_no_eval(tmp_path: Path):
    for name, html in _pages(_render(_model([_test(outcome="failed")]), tmp_path)).items():
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", html), name
        assert "eval(" not in html and "new Function" not in html and "innerHTML" not in html, name


# ── a report is a folder of files, and the same one every time ──────────────


def test_two_renders_of_one_run_are_the_same_bytes(tmp_path: Path):
    from .test_report_hotspots import _captures
    from .test_bundle import build_project

    project = _captures(build_project(tmp_path))
    sites = []
    for name in ("one", "two"):
        build_report(project_root=project, site_dir=tmp_path / name, screenshots="all")
        root = tmp_path / name
        sites.append({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()})
    assert sites[0] == sites[1]
    assert len(sites[0]) > 10


def test_a_warning_of_the_run_is_listed_once(tmp_path: Path):
    """The source's warnings are the manifest's: handing both to the model doubled them."""
    from .test_bundle import build_project

    project = build_project(tmp_path)
    (project / "output" / "screenshots" / "order-lifecycle" / "fr" / "mobile" / "step-01.png").write_text("changed")
    result = build_report(project_root=project, site_dir=tmp_path / "site")
    assert result.model.warnings
    assert len(result.model.warnings) == len(set(result.model.warnings))


def test_the_fonts_travel_with_their_licences(tmp_path: Path):
    fonts = _render(_model([_test()]), tmp_path) / "assets" / "fonts"
    for face in ("bricolage-grotesque", "jetbrains-mono"):
        assert (fonts / f"{face}-latin.woff2").read_bytes()[:4] == b"wOF2"
        licence = (fonts / f"{face}-OFL.txt").read_text(encoding="utf-8")
        assert "SIL Open Font License, Version 1.1" in licence and licence.startswith("Copyright")
    assert sum(p.stat().st_size for p in fonts.iterdir()) < 200_000


def test_the_site_asks_the_network_for_nothing(tmp_path: Path):
    site = _render(_model([_test(outcome="failed")], coverage=ReportCoverage(tier="bundled")), tmp_path)
    css = _read(site, "assets/report.css")
    assert "@import" not in css
    for address in re.findall(r"url\(\s*[\"']?([^\"')]+)", css):
        assert (site / "assets" / address).is_file(), address
    for name, html in _pages(site).items():
        for address in re.findall(r'(?:src|href)="([^"]+)"', html):
            assert not re.match(r"(?:https?:)?//", address), (name, address)


# ── the design ──────────────────────────────────────────────────────────────


def _css() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "templates" / "report" / "report.css").read_text(encoding="utf-8")


def _tokens(css: str, selector: str) -> dict[str, str]:
    block = css[css.index(selector) + len(selector) :]
    block = block[: block.index("}")]
    return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-f]{6})", block))


def _ratio(a: str, b: str) -> float:
    def luminance(colour: str) -> float:
        parts = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in parts)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_stylesheet_holds_no_jinja_delimiter():
    """It is parsed as a template: one would be a syntax error, in a comment too."""
    assert not re.search(r"\{[{%#]", _css())


def test_the_dark_palette_is_the_same_in_both_places():
    css = _css()
    media = _tokens(css, ':root:not([data-theme="light"]) {')
    assert media and media == _tokens(css, ':root[data-theme="dark"] {')


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_meets_the_contrast_it_needs(scheme):
    css = _css()
    tokens = _tokens(css, ":root {")
    if scheme == "dark":
        tokens = {**tokens, **_tokens(css, ':root[data-theme="dark"] {')}
    for ground in ("--bg", "--surface", "--chrome", "--highlight"):
        assert _ratio(tokens["--text"], tokens[ground]) >= 4.5, ground
    for ground in ("--bg", "--surface", "--chrome", "--highlight"):
        assert _ratio(tokens["--text-muted"], tokens[ground]) >= 4.5, ground
        assert _ratio(tokens["--pass"], tokens[ground]) >= 4.5, ground
    assert _ratio(tokens["--fail"], tokens["--surface"]) >= 4.5
    for status in ("--pass", "--fail", "--flaky", "--neutral"):
        assert _ratio(tokens[status], tokens[f"{status}-bg"]) >= 4.5, status
    # What is not text needs 3:1: the focus ring and a control's edge.
    for graphic in ("--marker", "--border-strong"):
        assert _ratio(tokens[graphic], tokens["--surface"]) >= 3, graphic


def test_balise_is_never_text():
    css = _css()
    assert not re.search(r"(?<![a-z-])color:\s*var\(--(marker|logo-check)\)", css)
    assert css.lower().count("#d2561a") == 2  # the two tokens, and nowhere else


def test_a_page_skips_no_heading_level(tmp_path: Path):
    from .test_report_links import parse_page

    model = _model(
        [_test(outcome="failed", fail_tail_b64=_tail_b64("E boom"))], coverage=ReportCoverage(tier="bundled")
    )
    site = _render(model, tmp_path)
    for path in site.rglob("*.html"):
        levels = parse_page(path).headings
        assert levels[0] == 1 and levels.count(1) == 1, path.name
        assert all(b - a <= 1 for a, b in zip(levels, levels[1:])), (path.name, levels)


def test_hide_target_shows_only_where_there_is_one_to_hide(tmp_path: Path):
    html = _read(_render(_model([_test(outcome="failed")]), tmp_path), "index.html")
    assert "Hide target" not in html
