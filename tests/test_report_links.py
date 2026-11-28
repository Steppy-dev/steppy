"""The report's links: the cloud's fragments (spec V3), and pages that hold what they name.

What a browser does with them is in ``tests/integration/test_report_design_it.py``.
Here: the fragment's spelling, and the one invariant that keeps a static site honest
— every internal link of every page points at a file and an id that exist.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

from steppy.bundle.results import steps_from_records
from steppy.contracts.bundle import BundleManifest, BundleRun, BundleSpec, BundleTest
from steppy.contracts.report import ReportCoverage, ReportSourceInfo
from steppy.report.links import cause_fragment, scenario_fragment, step_fragment, variant_fragment
from steppy.report.model import build_model
from steppy.report.render import render_site

RUN = BundleRun(
    run_id="r1",
    created_at="2026-11-25T20:13:35Z",
    steppy_version="0.10.0",
    spec=BundleSpec(locales=["en", "fr"], layouts=["desktop", "mobile"]),
)


# ── the fragment ────────────────────────────────────────────────────────────


def test_the_three_forms():
    assert scenario_fragment("checkout") == "checkout"
    assert variant_fragment("checkout", "fr", "mobile") == "checkout/fr/mobile"
    assert step_fragment("checkout", "fr", "mobile", 7) == "checkout/fr/mobile/step-7"


def test_a_missing_locale_or_layout_is_a_dash():
    assert variant_fragment("login", None, None) == "login/-/-"
    assert variant_fragment("login", "fr", None) == "login/fr/-"
    assert step_fragment("login", None, "mobile", 2) == "login/-/mobile/step-2"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("checkout-guest_2.0", "checkout-guest_2.0"),
        ("fr-BE", "fr-BE"),
        ("mon scénario", "mon%20sc%C3%A9nario"),
        ("日本", "%E6%97%A5%E6%9C%AC"),
        ("50%", "50%25"),
        ("a/b", "a%2Fb"),  # no real segment holds a slash; a hostile one must not add a segment
        ('<"`>', "%3C%22%60%3E"),
        ("a;b,c?d:e@f&g=h+i$j!k~l*m'n(o)p#q", "a;b,c?d:e@f&g=h+i$j!k~l*m'n(o)p#q"),  # encodeURI leaves these
        ("[x]{y}|\\^", "%5Bx%5D%7By%7D%7C%5C%5E"),
    ],
)
def test_a_segment_is_encoded_as_encode_uri_does(text, expected):
    assert scenario_fragment(text) == expected
    assert unquote(expected) == text


def test_a_cause_is_two_segments_whatever_it_holds():
    assert cause_fragment("b6b5c199") == "cause/b6b5c199"
    assert cause_fragment("a/b/c") == "cause/a%2Fb%2Fc"


# ── the site ────────────────────────────────────────────────────────────────


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.classes: dict[str, int] = {}
        self.headings: list[int] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "a" and "href" in values:
            self.hrefs.append(values["href"])
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        for name in (values.get("class") or "").split():
            self.classes[name] = self.classes.get(name, 0) + 1


def parse_page(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def _test(scenario, locale, layout, outcome="passed", **kw):
    nodeid = kw.pop("nodeid", f"tests/e2e/test_{scenario}.py::test_flow[{locale}-{layout}]")
    return BundleTest(scenario=scenario, nodeid=nodeid, outcome=outcome, locale=locale, layout=layout, **kw)


def _events(*nodeids):
    events = []
    for nodeid in nodeids:
        events.append({"kind": "test-start", "ts": 1.0, "nodeid": nodeid})
        for step in (1, 2, 5):
            row = {"nodeid": nodeid, "step": step}
            events.append({"kind": "step-start", "ts": 1.0, "title": f"Do {step}", "action": "click", **row})
            if step != 5:
                events.append({"kind": "step-end", "ts": 1.2, **row})
        events.append({"kind": "test-end", "ts": 2.0, "nodeid": nodeid, "outcome": "failed", "duration_ms": 1000})
    return steps_from_records(events)


def _site(tmp_path: Path, tests, run=RUN) -> Path:
    model = build_model(
        manifest=BundleManifest(steppy_bundle=1, run=run, tests=tests),
        source=ReportSourceInfo(kind="tree", label="output"),
        attempts=_events(*(t.nodeid for t in tests)),
        coverage=ReportCoverage(tier="bundled"),
    )
    site = tmp_path / "site"
    render_site(model, site_dir=site, title="Report")
    return site


ORDINARY = [
    _test("checkout", locale, layout, "failed" if (locale, layout) == ("fr", "mobile") else "passed", failed_step=5)
    for locale in ("en", "fr")
    for layout in ("desktop", "mobile")
] + [_test("login", "en", "desktop")]

ODD = [
    _test("mon scénario", "fr-BE", "grand écran", "failed", failed_step=5),
    _test("50% off", None, None),
    _test("a&b", "en", None, "failed", failed_step=5),
    _test('"quoted"', "en", "mobile"),
    _test("../../evil", "../x", "</script>", "failed", failed_step=5),
]


@pytest.mark.parametrize("tests", [ORDINARY, ODD], ids=["ordinary", "odd"])
def test_every_internal_link_points_at_something_that_exists(tmp_path: Path, tests):
    site = _site(tmp_path, tests)
    pages = {p: parse_page(p) for p in site.rglob("*.html")}
    checked = 0
    for path, page in pages.items():
        for href in page.hrefs:
            target, _, fragment = href.partition("#")
            resolved = (path.parent / unquote(target)).resolve() if target else path
            assert resolved.is_file(), (path.name, href)
            if fragment:
                assert fragment in pages[resolved].ids, (path.name, href)
            checked += 1
    assert checked > 40


@pytest.mark.parametrize("tests", [ORDINARY, ODD], ids=["ordinary", "odd"])
def test_an_id_is_unique_on_its_page(tmp_path: Path, tests):
    for path in _site(tmp_path, tests).rglob("*.html"):
        ids = parse_page(path).ids
        assert sorted(ids) == sorted(set(ids)), path.name


def test_a_scenarios_page_holds_every_fragment_that_names_it(tmp_path: Path):
    ids = set(parse_page(_site(tmp_path, ORDINARY) / "scenarios" / "checkout.html").ids)
    assert "checkout" in ids
    for locale in ("en", "fr"):
        for layout in ("desktop", "mobile"):
            assert f"checkout/{locale}/{layout}" in ids
            # The scenario's own numbers, sparse: there is no step 3 or 4.
            assert {f"checkout/{locale}/{layout}/step-{n}" for n in (1, 2, 5)} <= ids
            assert f"checkout/{locale}/{layout}/step-3" not in ids


def test_the_scenarios_own_link_is_before_the_variant_it_opens(tmp_path: Path):
    ids = parse_page(_site(tmp_path, ORDINARY) / "scenarios" / "checkout.html").ids
    assert ids[ids.index("checkout") + 1] == "checkout/fr/mobile"  # its first failing variant


def test_each_step_and_each_variant_has_a_link_to_copy(tmp_path: Path):
    site = _site(tmp_path, ORDINARY)
    page = parse_page(site / "scenarios" / "checkout.html")
    assert page.classes["steppy-copy-link"] == 4 + 4 * 3
    assert "../scenarios/checkout.html#checkout/fr/mobile/step-5" in page.hrefs
    index = parse_page(site / "index.html")
    assert index.classes["steppy-copy-link"] == 1 + 3
    assert "scenarios/checkout.html#checkout/fr/mobile/step-5" in index.hrefs


def test_a_second_test_in_a_variant_takes_no_id_of_the_first(tmp_path: Path):
    tests = [
        _test("a", "fr", "mobile", "failed", nodeid="t.py::test_a[x]", failed_step=5),
        _test("a", "fr", "mobile", "passed", nodeid="t.py::test_a__tour[x]"),
    ]
    page = parse_page(_site(tmp_path, tests) / "scenarios" / "a.html")
    assert page.classes["steppy-variant"] == 2
    assert page.ids.count("a/fr/mobile") == 1
    assert page.classes["steppy-copy-link"] == 1 + 3


def _config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'<script type="application/json" id="ui/links">(.*?)</script>', text, re.DOTALL)
    assert match, path.name
    return json.loads(match.group(1))


def test_the_index_knows_each_scenarios_page(tmp_path: Path):
    config = _config(_site(tmp_path, ORDINARY) / "index.html")
    assert config == {
        "page": "index",
        "scenarios": {"checkout": "scenarios/checkout.html", "login": "scenarios/login.html"},
        "shown": "checkout · fr · mobile",
    }


def test_a_scenarios_page_knows_what_to_fall_back_to(tmp_path: Path):
    config = _config(_site(tmp_path, ORDINARY) / "scenarios" / "checkout.html")
    assert config == {
        "page": "scenario",
        "scenario": "checkout",
        "anchor": "checkout",
        "first": "checkout/fr/mobile",
        "shown": "checkout · fr · mobile",
        "index": "../index.html",
    }


def test_a_hostile_name_cannot_close_the_data_block(tmp_path: Path):
    site = _site(tmp_path, ODD)
    for path in site.rglob("*.html"):
        if path.name == "coverage.html":
            continue
        assert _config(path)["page"] in ("index", "scenario")
        # The two scripts of the page and nothing else.
        assert path.read_text(encoding="utf-8").count("</script>") == 2, path.name
    assert "../../evil" in _config(site / "index.html")["scenarios"]


def test_the_address_the_script_goes_to_is_the_reports_own(tmp_path: Path):
    for href in _config(_site(tmp_path, ODD) / "index.html")["scenarios"].values():
        assert re.fullmatch(r"scenarios/[A-Za-z0-9._-]+\.html", href)
