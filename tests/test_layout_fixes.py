"""Regression tests for the three layout-feature bugs:

1. Step predicate semantics: in test mode (or guide mode without
   ``screenshot.layouts``), the ``layouts:`` filter treats ``"desktop"`` as
   the active layout — mobile-only steps are skipped automatically so they
   stop hitting selector-timeout errors at the desktop viewport.

2. ``steppy guide`` (the ``GuideAssembler.generate_guides_for_tag`` path)
   never passed a layout to ``prepare_guide_steps``, so screenshots captured
   under ``<slug>/<locale>/<layout>/`` couldn't be found. The assembler now
   discovers layouts on disk and renders one guide per (locale, layout).

3. ``generate_guide`` output paths overwrote each other across layouts. The
   target dir now nests under ``<role>/<locale>/<layout>/`` when a layout is
   passed.
"""

import ast
import json
import struct
import zlib
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.generator import SteppyGenerator
from steppy.guide import GuideAssembler
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    AuthCredentials,
    SteppyConfig,
)
from steppy.parser import parse_scenario_file

pytest_plugins = ["pytester"]

# ── Test helpers ─────────────────────────────────────────────────────────────


def _minimal_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        sig
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


_SIDECAR = json.dumps(
    {
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
    }
)


def _seed_screenshots(dest: Path, steps: list[int]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    png = _minimal_png()
    for n in steps:
        (dest / f"step-{n:02d}.png").write_bytes(png)
        (dest / f"step-{n:02d}.json").write_text(_SIDECAR, encoding="utf-8")


def _make_config_with_role(role: str) -> SteppyConfig:
    return SteppyConfig(
        apps={
            role: AppConfig(
                name=role.title(),
                url="http://localhost",
                auth=AppAuthConfig(
                    mode="auth0",
                    credentials=AuthCredentials(username="u", password="p"),
                ),
            )
        }
    )


# ── Fix 1: _step_runs predicate semantics ────────────────────────────────────


class TestStepRunsPredicate:
    """Compile the predicate from the conftest template and exercise it
    directly. We exec the function definition in an isolated namespace, then
    flip the GUIDE_MODE / CURRENT_LAYOUT module globals to drive each branch.

    This is a regression net for the contract where, in plain test mode (or
    guide mode without ``screenshot.layouts``), the ``layouts:`` filter
    auto-skips steps whose layout doesn't match the default test viewport
    (treated as ``"desktop"``). Mobile-only steps stop running into
    selector-timeout errors at the desktop viewport.
    """

    def _load_step_runs(self, guide_mode: bool, current_layout):
        """Return a `_step_runs` callable bound to the given globals."""
        ns: dict = {
            "GUIDE_MODE": guide_mode,
            "CURRENT_LAYOUT": current_layout,
            "DEFAULT_TEST_LAYOUT": "desktop",
        }
        # Definition copied verbatim from the conftest template — keep in sync
        # with `templates/conftest.py.j2::_step_runs`.
        exec(
            dedent("""
                def _step_runs(only_in, layouts):
                    if only_in == "test" and GUIDE_MODE:
                        return False
                    if only_in == "guide" and not GUIDE_MODE:
                        return False
                    if layouts:
                        active = CURRENT_LAYOUT or DEFAULT_TEST_LAYOUT
                        if active not in layouts:
                            return False
                    return True
            """),
            ns,
        )
        return ns["_step_runs"]

    def test_layout_filter_uses_desktop_default_in_test_mode(self):
        """Headline contract: in plain test mode (no layout parametrized),
        the layout filter treats ``"desktop"`` as the active layout. Mobile
        steps skip; desktop steps and explicit-multi-layout steps run."""
        run = self._load_step_runs(guide_mode=False, current_layout=None)
        assert run(None, ["desktop"]) is True
        assert run(None, ["mobile"]) is False
        assert run(None, ["desktop", "mobile"]) is True

    def test_layout_filter_uses_desktop_default_in_guide_mode_no_layouts(self):
        """Guide mode without ``screenshot.layouts`` configured behaves like
        test mode for the layout filter — desktop is the active layout."""
        run = self._load_step_runs(guide_mode=True, current_layout=None)
        assert run(None, ["desktop"]) is True
        assert run(None, ["mobile"]) is False
        assert run("guide", ["desktop"]) is True
        assert run("guide", ["mobile"]) is False

    def test_layout_filter_applies_when_layout_active(self):
        """When a layout is parametrized, the whitelist is enforced."""
        run = self._load_step_runs(guide_mode=True, current_layout="desktop")
        assert run(None, ["mobile"]) is False
        assert run(None, ["desktop"]) is True
        assert run(None, ["desktop", "mobile"]) is True

    def test_no_layout_filter_runs_everywhere(self):
        """Steps without a ``layouts:`` filter run regardless of mode."""
        for guide_mode, current_layout in [
            (False, None),
            (True, None),
            (True, "desktop"),
            (True, "mobile"),
        ]:
            run = self._load_step_runs(guide_mode=guide_mode, current_layout=current_layout)
            assert run(None, None) is True
            assert run(None, []) is True

    def test_only_in_test_skipped_in_guide_mode(self):
        run = self._load_step_runs(guide_mode=True, current_layout=None)
        assert run("test", None) is False

    def test_only_in_guide_skipped_outside_guide_mode(self):
        run = self._load_step_runs(guide_mode=False, current_layout=None)
        assert run("guide", None) is False

    def test_combined_only_in_guide_plus_layout(self):
        """guide-only + mobile-only step: runs only when in guide mode AND
        the active layout is mobile (CURRENT_LAYOUT or default)."""
        # Guide mode, mobile active → run
        run = self._load_step_runs(guide_mode=True, current_layout="mobile")
        assert run("guide", ["mobile"]) is True
        # Guide mode, desktop active → skip (layout mismatch)
        run = self._load_step_runs(guide_mode=True, current_layout="desktop")
        assert run("guide", ["mobile"]) is False
        # Test mode → skip (only-in mismatch)
        run = self._load_step_runs(guide_mode=False, current_layout=None)
        assert run("guide", ["mobile"]) is False
        # Guide mode, no layout parametrized → skip (default = desktop, mismatch)
        run = self._load_step_runs(guide_mode=True, current_layout=None)
        assert run("guide", ["mobile"]) is False


def test_conftest_template_step_runs_uses_default_test_layout():
    """The conftest template body must define ``DEFAULT_TEST_LAYOUT`` and use
    the ``CURRENT_LAYOUT or DEFAULT_TEST_LAYOUT`` form, otherwise mobile-only
    steps regress to running (and timing out) at the desktop viewport in
    plain test mode."""
    template_path = Path(__file__).parent.parent / "templates" / "conftest.py.j2"
    body = template_path.read_text(encoding="utf-8")
    assert 'DEFAULT_TEST_LAYOUT = "desktop"' in body
    assert "active = CURRENT_LAYOUT or DEFAULT_TEST_LAYOUT" in body
    assert "if layouts and CURRENT_LAYOUT and CURRENT_LAYOUT not in layouts:" not in body


# ── Fix 2 & 3: GuideAssembler discovers and emits per-layout guides ─────────


class TestGuideAssemblerLayoutDiscovery:
    def test_discover_layouts_returns_layout_names_under_locale_dir(self, tmp_path):
        """Subdirs of ``<slug>/<locale>/`` that contain step PNGs are layouts."""
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        screenshots = tmp_path / "screenshots"
        for layout in ("desktop", "mobile"):
            _seed_screenshots(screenshots / "demo" / "fr" / layout, [1, 2])

        layouts = assembler._discover_layouts("demo", screenshots, locale="fr")
        assert layouts == ["desktop", "mobile"]

    def test_discover_layouts_returns_empty_for_flat_layout(self, tmp_path):
        """No per-layout subdirs → empty list, caller falls back to single guide."""
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        screenshots = tmp_path / "screenshots"
        _seed_screenshots(screenshots / "demo" / "fr", [1, 2])

        assert assembler._discover_layouts("demo", screenshots, locale="fr") == []

    def test_discover_layouts_returns_empty_when_locale_dir_missing(self, tmp_path):
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        screenshots = tmp_path / "screenshots"
        # No screenshots seeded at all
        assert assembler._discover_layouts("demo", screenshots, locale="fr") == []

    def test_discover_layouts_ignores_non_screenshot_subdirs(self, tmp_path):
        """A subdir without ``step-*.png`` isn't a layout (defensive guard
        against unrelated junk in the screenshots tree)."""
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        screenshots = tmp_path / "screenshots"
        _seed_screenshots(screenshots / "demo" / "fr" / "desktop", [1])
        # Empty directory next to a real layout
        (screenshots / "demo" / "fr" / "logs").mkdir(parents=True)

        assert assembler._discover_layouts("demo", screenshots, locale="fr") == ["desktop"]


class TestGenerateGuidesForTagWithLayouts:
    """End-to-end: a scenario captured per-(locale, layout) on disk renders
    one guide pair per layout via ``generate_guides_for_tag``. This is the
    bug the user reported — without this, screenshots in
    ``<slug>/<locale>/<layout>/`` were unreachable from ``steppy guide``."""

    def _make_scenarios_dir(self, tmp_path: Path) -> Path:
        sc_dir = tmp_path / "scenarios"
        sc_dir.mkdir()
        (sc_dir / "demo.md").write_text(
            dedent("""\
            ---
            title: Demo
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Open dashboard
            - role: editor
            - action: navigate
            - target: /
            - screenshot: editor
            - guide-text:
                fr: Tableau de bord
        """),
            encoding="utf-8",
        )
        return sc_dir

    def test_renders_one_guide_per_layout_when_per_layout_screenshots_exist(self, tmp_path):
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        scenarios_dir = self._make_scenarios_dir(tmp_path)

        screenshots = tmp_path / "screenshots"
        for layout in ("desktop", "mobile"):
            _seed_screenshots(screenshots / "demo" / "fr" / layout, [1])

        out = tmp_path / "out"
        results = assembler.generate_guides_for_tag(
            "user-guide-editor",
            ["fr"],
            scenarios_dir,
            out,
            screenshots,
        )

        # Result keys carry the layout dim so the caller can show per-layout status
        scenario_results = results["demo"]
        assert "fr/desktop" in scenario_results
        assert "fr/mobile" in scenario_results
        assert scenario_results["fr/desktop"]["status"] == "success"
        assert scenario_results["fr/mobile"]["status"] == "success"

        # On-disk: per-layout subdirs under <role>/<locale>/<layout>/
        assert (out / "editor" / "fr" / "desktop" / "demo.md").exists()
        assert (out / "editor" / "fr" / "desktop" / "demo.html").exists()
        assert (out / "editor" / "fr" / "mobile" / "demo.md").exists()
        assert (out / "editor" / "fr" / "mobile" / "demo.html").exists()

    def test_renders_single_guide_per_locale_when_no_layouts(self, tmp_path):
        """Back-compat: flat per-locale screenshots still produce one guide
        per locale at the legacy path (no <layout> segment)."""
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)
        scenarios_dir = self._make_scenarios_dir(tmp_path)

        screenshots = tmp_path / "screenshots"
        _seed_screenshots(screenshots / "demo" / "fr", [1])

        out = tmp_path / "out"
        results = assembler.generate_guides_for_tag(
            "user-guide-editor",
            ["fr"],
            scenarios_dir,
            out,
            screenshots,
        )

        # Result key is the bare locale (no layout suffix)
        assert list(results["demo"].keys()) == ["fr"]
        assert results["demo"]["fr"]["status"] == "success"

        # Legacy flat output path
        assert (out / "editor" / "fr" / "demo.md").exists()
        assert (out / "editor" / "fr" / "demo.html").exists()
        # No per-layout dirs were created
        assert not (out / "editor" / "fr" / "desktop").exists()

    def test_layout_filtered_step_appears_only_in_matching_layout(self, tmp_path):
        """End-to-end check that the assembler's step-level layout filter
        works in tandem with on-disk discovery: a step gated to mobile only
        appears in the mobile guide, not the desktop guide."""
        cfg = _make_config_with_role("editor")
        assembler = GuideAssembler(cfg)

        scenarios_dir = tmp_path / "scenarios"
        scenarios_dir.mkdir()
        (scenarios_dir / "flow.md").write_text(
            dedent("""\
            ---
            title: Flow
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Open
            - role: editor
            - action: navigate
            - target: /
            - screenshot: editor
            - guide-text:
                fr: Ouvrir

            ### 2. Tap hamburger (mobile only)
            - role: editor
            - action: click
            - selector: '[data-testid=hamburger]'
            - screenshot: editor
            - layouts:
              - mobile
            - guide-text:
                fr: Touchez le menu

            ### 3. Pick orders
            - role: editor
            - action: click
            - selector: '[data-testid=orders]'
            - screenshot: editor
            - guide-text:
                fr: Cliquez sur Commandes
        """),
            encoding="utf-8",
        )

        screenshots = tmp_path / "screenshots"
        # Desktop has steps 1 and 3 (skipped step 2 because it's mobile-only)
        _seed_screenshots(screenshots / "flow" / "fr" / "desktop", [1, 3])
        # Mobile has all three
        _seed_screenshots(screenshots / "flow" / "fr" / "mobile", [1, 2, 3])

        out = tmp_path / "out"
        results = assembler.generate_guides_for_tag(
            "user-guide-editor",
            ["fr"],
            scenarios_dir,
            out,
            screenshots,
        )
        assert results["flow"]["fr/desktop"]["status"] == "success", results["flow"]["fr/desktop"]
        assert results["flow"]["fr/mobile"]["status"] == "success", results["flow"]["fr/mobile"]

        desktop_md = (out / "editor" / "fr" / "desktop" / "flow.md").read_text(encoding="utf-8")
        mobile_md = (out / "editor" / "fr" / "mobile" / "flow.md").read_text(encoding="utf-8")

        # Desktop guide has steps 1 and 3 only (step 2 is mobile-only)
        assert "step-01" in desktop_md
        assert "step-03" in desktop_md
        assert "step-02" not in desktop_md, "Mobile-only step leaked into the desktop guide"

        # Mobile guide has all three
        assert "step-01" in mobile_md
        assert "step-02" in mobile_md
        assert "step-03" in mobile_md


# ── --layouts works without --guide (test-mode multi-viewport runs) ──────────


class TestLayoutsWithoutGuide:
    """`steppy run --layouts desktop,mobile` (without `--guide`) must be
    accepted and propagate ``STEPPY_LAYOUTS`` to pytest. This lets users run
    plain test scenarios across configured viewports to verify they work on
    every device — independent of guide/screenshot capture."""

    @pytest.fixture
    def fake_tests_dir(self, tmp_path):
        tests_dir = tmp_path / "tests" / "e2e"
        tests_dir.mkdir(parents=True)
        (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
        (tests_dir / "test_example.py").write_text("# test", encoding="utf-8")
        return tests_dir

    def test_layouts_without_guide_is_accepted(self, fake_tests_dir):
        """Headline: --layouts no longer requires --guide."""
        from unittest.mock import patch, MagicMock
        from typer.testing import CliRunner
        from steppy.cli import app

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = CliRunner().invoke(
                app,
                ["run", "--tests-dir", str(fake_tests_dir), "--layouts", "desktop,mobile"],
            )

        assert result.exit_code == 0, result.output
        # No rejection message
        assert "--layouts requires --guide" not in result.output

        env = mock_run.call_args[1]["env"]
        assert env.get("STEPPY_LAYOUTS") == "desktop,mobile"
        # Must NOT enable guide mode just because layouts were passed
        assert "STEPPY_GUIDE_MODE" not in env or env.get("STEPPY_GUIDE_MODE") != "1"
        # Must NOT auto-enable screenshots (only --guide does)
        assert "STEPPY_SCREENSHOTS" not in env or env.get("STEPPY_SCREENSHOTS") != "1"

    def test_plain_run_does_not_set_layouts_env(self, fake_tests_dir):
        """Backwards compat: ``steppy run`` with no flags sets no layout env."""
        from unittest.mock import patch, MagicMock
        from typer.testing import CliRunner
        from steppy.cli import app

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = CliRunner().invoke(
                app,
                ["run", "--tests-dir", str(fake_tests_dir)],
            )

        assert result.exit_code == 0, result.output
        env = mock_run.call_args[1]["env"]
        assert "STEPPY_LAYOUTS" not in env


# ── Scenario-level `layouts:` declaration (A.2) ──────────────────────────────


def _extract_template_func(name: str) -> str:
    """Return the verbatim source of a pure-Python function from the conftest
    template (def line, the ``):`` closing a wrapped signature, and its indented
    body). Only works for functions with no
    Jinja in their body — true for the scenario-layout hooks. Extracting the
    real source (vs. a hand copy) means these tests can't drift from the
    template."""
    template_path = Path(__file__).parent.parent / "templates" / "conftest.py.j2"
    lines = template_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    capturing = False
    for ln in lines:
        if not capturing and ln.startswith(f"def {name}("):
            capturing = True
            out.append(ln)
            continue
        if capturing:
            if ln.strip() == "" or ln.startswith((" ", "\t", ")")):
                out.append(ln)
            else:
                break
    assert out, f"function {name} not found in conftest template"
    return "\n".join(out)


class TestEffectiveRunLayouts:
    """Exercise the intersection matrix (see the plan's semantics table). The
    ``_effective_run_layouts`` definition is pulled verbatim from the conftest
    template and exec'd in an isolated namespace, flipping the ``LAYOUTS_FOR_RUN``
    module global to drive each case."""

    def _load(self, layouts_for_run):
        ns: dict = {"LAYOUTS_FOR_RUN": layouts_for_run}
        exec(_extract_template_func("_effective_run_layouts"), ns)
        return ns["_effective_run_layouts"]

    def test_no_declaration_passthrough_default(self):
        """No scenario declaration + no run selection → unchanged ([None])."""
        assert self._load([None])(None) == [None]

    def test_no_declaration_passthrough_run_selection(self):
        """No scenario declaration + run --layouts → full run selection."""
        assert self._load(["desktop", "mobile"])(None) == ["desktop", "mobile"]

    def test_authoritative_on_plain_run(self):
        """Declaration + no run selection → the declaration drives the run."""
        assert self._load([None])(["desktop", "mobile"]) == ["desktop", "mobile"]

    def test_run_selection_filters_declaration(self):
        """Declaration + run --layouts → intersection (no phantom captures)."""
        assert self._load(["desktop", "mobile"])(["desktop"]) == ["desktop"]

    def test_disjoint_is_empty(self):
        """Declaration disjoint from run selection → [] (scenario deselected)."""
        assert self._load(["mobile"])(["desktop"]) == []

    def test_intersection_preserves_run_selected_order(self):
        """The intersection follows LAYOUTS_FOR_RUN (run-selected) order."""
        assert self._load(["mobile", "desktop"])(["desktop", "mobile"]) == ["mobile", "desktop"]


def test_conftest_template_has_scenario_layout_hooks():
    """The conftest template must define the parametrization + deselection hooks
    and drop the fixtures' own ``params=`` (moved into the hook). Guards the
    Option-A mechanism that lets ``SCENARIO_LAYOUTS`` narrow the run."""
    template_path = Path(__file__).parent.parent / "templates" / "conftest.py.j2"
    body = template_path.read_text(encoding="utf-8")
    assert "def pytest_generate_tests(metafunc):" in body
    assert "def pytest_collection_modifyitems(config, items):" in body
    assert "def _effective_run_layouts(scenario_layouts):" in body
    assert "_PAGE_FIXTURES = {" in body
    # Fixtures no longer carry their own params= (the hook owns parametrization).
    assert "@pytest.fixture(params=_locale_layout_params()" not in body


class _FakeMetafunc:
    """Minimal ``metafunc`` stand-in that records ``parametrize`` calls."""

    def __init__(self, fixturenames, scenario_layouts):
        self.fixturenames = fixturenames
        self.module = type("M", (), {"SCENARIO_LAYOUTS": scenario_layouts})
        self.calls: list = []

    def parametrize(self, argname, argvalues, **kwargs):
        self.calls.append((argname, list(argvalues), kwargs))


class TestGenerateTestsHook:
    """Drive the real ``pytest_generate_tests`` source (extracted verbatim from
    the template) with a fake metafunc. Guards the parametrization contract —
    especially that a disjoint declaration parametrizes NOTHING (an empty
    ``parametrize`` + id-callable crashes on pytest's empty-set sentinel, so the
    hook must skip and let ``pytest_collection_modifyitems`` deselect instead)."""

    def _hook(self, layouts_for_run):
        ns: dict = {
            "LAYOUTS_FOR_RUN": layouts_for_run,
            "LOCALES_FOR_RUN": [None],
            "_PAGE_FIXTURES": {"admin_page", "admin_page_noauth", "editor_page"},
            "_locale_layout_params": lambda: [(loc, lay) for loc in [None] for lay in layouts_for_run],
            "_locale_layout_id": lambda v: f"{v[0] or 'default'}-{v[1] or 'default'}",
        }
        exec(_extract_template_func("_effective_run_layouts"), ns)
        exec(_extract_template_func("pytest_generate_tests"), ns)
        return ns["pytest_generate_tests"]

    def test_no_declaration_uses_locale_layout_params(self):
        hook = self._hook([None])
        mf = _FakeMetafunc(["admin_page_noauth"], None)
        hook(mf)
        assert len(mf.calls) == 1
        argname, values, kwargs = mf.calls[0]
        assert argname == "admin_page_noauth"
        assert values == [(None, None)]  # default path == _locale_layout_params()
        assert kwargs["indirect"] is True

    def test_intersection_parametrizes_filtered_set(self):
        hook = self._hook(["desktop", "mobile"])
        mf = _FakeMetafunc(["admin_page_noauth"], ["desktop"])
        hook(mf)
        assert len(mf.calls) == 1
        argname, values, kwargs = mf.calls[0]
        assert argname == "admin_page_noauth"
        assert values == [(None, "desktop")]
        assert kwargs["indirect"] is True

    def test_disjoint_declaration_parametrizes_nothing(self):
        """The regression guard: --layouts mobile against a desktop-only scenario
        must NOT call parametrize (empty set + id callable would raise)."""
        hook = self._hook(["mobile"])
        mf = _FakeMetafunc(["admin_page_noauth"], ["desktop"])
        hook(mf)
        assert mf.calls == []

    def test_no_page_fixture_is_noop(self):
        hook = self._hook(["desktop", "mobile"])
        mf = _FakeMetafunc(["tmp_path"], ["desktop"])
        hook(mf)
        assert mf.calls == []

    def test_multi_role_binds_fixtures_in_lockstep(self):
        """Two role pages go under ONE parametrize call. One call per fixture
        multiplies (4 items for two roles and two layouts, one of them a desktop
        page filed under ``mobile/``); ids must be precomputed strings, or pytest
        joins the per-argname ids into ``default-desktop-default-desktop``."""
        hook = self._hook(["desktop", "mobile"])
        mf = _FakeMetafunc(["admin_page", "editor_page"], None)
        hook(mf)
        assert len(mf.calls) == 1
        argnames, values, kwargs = mf.calls[0]
        assert list(argnames) == ["admin_page", "editor_page"]
        assert values == [
            ((None, "desktop"), (None, "desktop")),
            ((None, "mobile"), (None, "mobile")),
        ]
        assert kwargs["ids"] == ["default-desktop", "default-mobile"]
        assert kwargs["indirect"] is True


_LOCKSTEP_CONFTEST = """
import pytest

LOCALES_FOR_RUN = ["fr", "nl"]
LAYOUTS_FOR_RUN = ["desktop", "mobile"]
_PAGE_FIXTURES = {"admin_page", "editor_page", "reviewer_page"}

__HOOKS__


@pytest.fixture
def admin_page(request):
    return request.param


@pytest.fixture
def editor_page(request):
    return request.param


@pytest.fixture
def reviewer_page(request):
    return request.param
"""

_LOCKSTEP_ROLES_MODULE = """
def _is_pair(value):
    return isinstance(value, tuple) and len(value) == 2


def test_one_role(admin_page):
    assert _is_pair(admin_page)


def test_two_roles(admin_page, editor_page):
    assert _is_pair(admin_page) and admin_page == editor_page


def test_three_roles(admin_page, editor_page, reviewer_page):
    assert _is_pair(admin_page) and admin_page == editor_page == reviewer_page
"""

_LOCKSTEP_DECLARED_MODULE = """
SCENARIO_LAYOUTS = ["desktop"]


def test_two_roles_declared(admin_page, editor_page):
    assert admin_page == editor_page and admin_page[1] == "desktop"
"""


class TestMultiRoleLockstepCollection:
    """The real hook under a real pytest, which is what a fake metafunc cannot
    show: how pytest wraps each value (a single-fixture string argname is
    tuple-wrapped, several argnames are not), what the ids come out as, and how
    many items are collected. Every helper is lifted verbatim from the template."""

    @pytest.fixture
    def suite(self, pytester):
        hooks = "\n\n\n".join(
            _extract_template_func(name)
            for name in (
                "_locale_layout_params",
                "_locale_layout_id",
                "_effective_run_layouts",
                "pytest_generate_tests",
                "pytest_collection_modifyitems",
            )
        )
        pytester.makeconftest(_LOCKSTEP_CONFTEST.replace("__HOOKS__", hooks))
        pytester.makepyfile(test_roles=_LOCKSTEP_ROLES_MODULE, test_declared=_LOCKSTEP_DECLARED_MODULE)
        return pytester

    def _collected(self, pytester) -> list[str]:
        result = pytester.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider")
        return [line for line in result.stdout.lines if "::" in line]

    def test_every_role_count_collects_one_item_per_locale_layout(self, suite):
        ids = ["fr-desktop", "fr-mobile", "nl-desktop", "nl-mobile"]
        collected = self._collected(suite)
        for test in ("test_one_role", "test_two_roles", "test_three_roles"):
            assert [n for n in collected if f"::{test}[" in n] == [f"test_roles.py::{test}[{i}]" for i in ids]

    def test_declared_layouts_still_narrow_a_multi_role_scenario(self, suite):
        collected = [n for n in self._collected(suite) if n.startswith("test_declared.py")]
        assert collected == [
            "test_declared.py::test_two_roles_declared[fr-desktop]",
            "test_declared.py::test_two_roles_declared[nl-desktop]",
        ]

    def test_every_role_page_receives_the_same_locale_layout(self, suite):
        """Each test asserts its role pages are equal ``(locale, layout)`` pairs,
        so a pass proves the lockstep and that ``locale, layout = request.param``
        still unpacks."""
        result = suite.runpytest_subprocess("-p", "no:cacheprovider")
        result.assert_outcomes(passed=14)


class _FakeScreenshotPage:
    def __init__(self, viewport_size):
        self.viewport_size = viewport_size

    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b"png")


class TestSidecarViewportFromPage:
    """The sidecar's ``viewport`` is the space its bboxes were measured in, so it
    is read off the page. ``CURRENT_LAYOUT`` is one global shared by every role
    page a test holds — the source that let a desktop capture be recorded as
    390x844 with ``bbox.x = 1435``."""

    def _sidecar(self, tmp_path, viewport_size) -> dict:
        ns: dict = {
            "Page": object,
            "json": json,
            "SCREENSHOTS_ENABLED": True,
            "CURRENT_LAYOUT": "mobile",
            "_screenshot_folder": lambda scenario: tmp_path,
            "_offset_for_full_page": lambda page: (0, 0),
            "_screenshot_mask_kwargs": lambda page: {},
            "_get_viewport_for_layout": lambda layout: {"width": 390, "height": 844},
            "_get_scale_factor_for_layout": lambda layout: 1,
        }
        exec(_extract_template_func("take_screenshot_with_bbox"), ns)
        bbox = {"x": 1435, "y": 10, "width": 40, "height": 20}
        ns["take_screenshot_with_bbox"](_FakeScreenshotPage(viewport_size), "scn", 1, "click", precaptured_bbox=bbox)
        return json.loads((tmp_path / "step-01.json").read_text(encoding="utf-8"))

    def test_records_the_pages_own_viewport(self, tmp_path):
        sidecar = self._sidecar(tmp_path, {"width": 1280, "height": 720})
        assert sidecar["viewport"] == {"width": 1280, "height": 720}

    def test_falls_back_to_the_layout_viewport_without_one(self, tmp_path):
        """A context built with ``no_viewport`` reports ``viewport_size is None``."""
        sidecar = self._sidecar(tmp_path, None)
        assert sidecar["viewport"] == {"width": 390, "height": 844}


class TestScenarioLayoutsBaked:
    """The generated test module bakes ``SCENARIO_LAYOUTS`` next to ``SCENARIO``."""

    def _baked(self, src: str):
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SCENARIO_LAYOUTS" for t in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError("SCENARIO_LAYOUTS not found in generated module")

    def _generate(self, tmp_path: Path, layouts_line: str) -> str:
        lines = ["---", "title: Scenario", "auth: false"]
        if layouts_line:
            lines.append(layouts_line)  # inline YAML flow list, e.g. "layouts: [desktop]"
        lines += ["---", "", "### 1. Step", "- role: editor", "- action: navigate", "- target: /", ""]
        md = tmp_path / "scenario.md"
        md.write_text("\n".join(lines), encoding="utf-8")
        scenario = parse_scenario_file(md)
        src = SteppyGenerator(_make_config_with_role("editor")).generate_test_file(scenario)
        ast.parse(src)  # generated module must be valid Python
        return src

    def test_declared_layouts_baked_as_list(self, tmp_path):
        src = self._generate(tmp_path, "layouts: [desktop, mobile]")
        assert self._baked(src) == ["desktop", "mobile"]

    def test_absent_layouts_baked_as_none(self, tmp_path):
        src = self._generate(tmp_path, "")
        assert self._baked(src) is None
