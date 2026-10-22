"""Tests for the shared selection kernel (FLAWS §8.3).

`steppy.selection` is the single home for the scenario / locale / layout selection
decision that `cli.run` (single-worker) and `coordinator.run_parallel` (parallel)
both consume — extracted so `--workers N` can't silently select a different scenario
set than `--workers 1`. These tests pin the pure kernel directly; the equivalence of
the two *callers* is pinned by `test_parallel_guide_integration.py` and
`test_run_byte_identical.py`.
"""

import sys

import pytest

from steppy import selection
from steppy.models import SteppyConfig


def _config(locales=("fr", "en", "nl"), layouts=("desktop", "mobile")) -> SteppyConfig:
    return SteppyConfig(
        apps={
            "editor": {
                "name": "Editor",
                "url": "http://localhost",
                "auth": {
                    "mode": "auth0",
                    "credentials": {"username": "u", "password": "p"},
                },
            }
        },
        locales=list(locales),
        screenshot={"layouts": {name: {"width": 800, "height": 600} for name in layouts}},
    )


def _make_scenarios(tmp_path, scenarios: dict[str, bool]):
    """Create scenarios/ (one <slug>.md per entry with the given guide.enabled) and
    tests/ (a test_<slug>.py per entry). Returns (scenarios_dir, tests_dir)."""
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for slug, guide_enabled in scenarios.items():
        (scenarios_dir / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\nguide:\n  enabled: {'true' if guide_enabled else 'false'}\n---\n",
            encoding="utf-8",
        )
        (tests_dir / f"test_{slug}.py").write_text("# generated\n", encoding="utf-8")
    return scenarios_dir, tests_dir


# ── resolve_locales / resolve_layouts ───────────────────────────────────────


def test_resolve_locales_explicit_wins():
    assert selection.resolve_locales("en,nl", guide=True, config=_config()) == ["en", "nl"]


def test_resolve_locales_strips_and_drops_blanks():
    assert selection.resolve_locales(" en , , nl ", guide=False, config=None) == ["en", "nl"]


def test_resolve_locales_guide_pulls_config():
    assert selection.resolve_locales(None, guide=True, config=_config()) == ["fr", "en", "nl"]


def test_resolve_locales_no_guide_is_empty():
    assert selection.resolve_locales(None, guide=False, config=_config()) == []


def test_resolve_locales_guide_without_config_degrades_to_empty():
    # cli's ConfigError-degrade path passes config=None in guide mode.
    assert selection.resolve_locales(None, guide=True, config=None) == []


def test_resolve_locales_empty_explicit_raises():
    with pytest.raises(selection.SelectionError) as exc:
        selection.resolve_locales(" , ", guide=True, config=_config())
    assert "locale codes" in exc.value.message


def test_resolve_layouts_explicit_wins():
    assert selection.resolve_layouts("mobile", guide=True, config=_config()) == ["mobile"]


def test_resolve_layouts_guide_pulls_config_keys():
    assert selection.resolve_layouts(None, guide=True, config=_config()) == ["desktop", "mobile"]


def test_resolve_layouts_empty_explicit_raises():
    with pytest.raises(selection.SelectionError) as exc:
        selection.resolve_layouts(",", guide=False, config=_config())
    assert "layout names" in exc.value.message


# ── select_scenario_slugs ───────────────────────────────────────────────────


def test_select_single_scenario_returns_slug(tmp_path):
    scenarios_dir, tests_dir = _make_scenarios(tmp_path, {"alpha": True})
    slugs = selection.select_scenario_slugs(
        scenarios_dir=scenarios_dir,
        tests_dir=tests_dir,
        scenario="alpha",
        tag=None,
        guide=False,
        no_guide=False,
    )
    assert slugs == ["alpha"]


def test_select_single_scenario_missing_file_raises_with_hint(tmp_path):
    scenarios_dir, tests_dir = _make_scenarios(tmp_path, {"alpha": True})
    with pytest.raises(selection.SelectionError) as exc:
        selection.select_scenario_slugs(
            scenarios_dir=scenarios_dir,
            tests_dir=tests_dir,
            scenario="missing",
            tag=None,
            guide=False,
            no_guide=False,
        )
    assert "Test file not found" in exc.value.message
    assert exc.value.hint and "steppy generate" in exc.value.hint


def test_select_missing_scenarios_dir_raises(tmp_path):
    with pytest.raises(selection.SelectionError) as exc:
        selection.select_scenario_slugs(
            scenarios_dir=tmp_path / "nope",
            tests_dir=tmp_path,
            scenario=None,
            tag=None,
            guide=False,
            no_guide=False,
        )
    assert "Scenarios directory not found" in exc.value.message


def test_select_all_when_no_filter(tmp_path):
    scenarios_dir, tests_dir = _make_scenarios(tmp_path, {"alpha": True, "beta": False})
    slugs = selection.select_scenario_slugs(
        scenarios_dir=scenarios_dir,
        tests_dir=tests_dir,
        scenario=None,
        tag=None,
        guide=False,
        no_guide=False,
    )
    assert sorted(slugs) == ["alpha", "beta"]


def test_select_guide_filters_to_enabled(tmp_path):
    scenarios_dir, tests_dir = _make_scenarios(tmp_path, {"alpha": True, "beta": False, "gamma": True})
    slugs = selection.select_scenario_slugs(
        scenarios_dir=scenarios_dir,
        tests_dir=tests_dir,
        scenario=None,
        tag=None,
        guide=True,
        no_guide=False,
    )
    assert sorted(slugs) == ["alpha", "gamma"]


def test_select_no_guide_filters_out_enabled(tmp_path):
    scenarios_dir, tests_dir = _make_scenarios(tmp_path, {"alpha": True, "beta": False, "gamma": True})
    slugs = selection.select_scenario_slugs(
        scenarios_dir=scenarios_dir,
        tests_dir=tests_dir,
        scenario=None,
        tag=None,
        guide=False,
        no_guide=True,
    )
    assert slugs == ["beta"]


# ── import hygiene (no-parallel-import guard, FLAWS §8.3) ────────────────────


def test_importing_selection_pulls_in_no_parallel_code():
    # cli.py imports selection eagerly; it must not transitively drag in the
    # coordinator/worker/_parallel modules the default-path guard forbids. Run in a
    # fresh interpreter so a coordinator import from another test can't pollute the
    # check.
    import subprocess

    code = (
        "import sys, steppy.selection\n"
        "bad = [m for m in sys.modules if m == 'steppy.coordinator' "
        "or m == 'steppy.worker' or m.startswith('steppy._parallel')]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ── BCP-47 validation on the capture side ───────────────────────────────────


class TestLocaleTagValidation:
    """`--locales` values become screenshot directory segments that
    `steppy build` reads back, and the build side rejects a malformed tag — so
    accepting one here only defers the failure to a place that cannot explain
    it. Grammar lives in `steppy.locales`; this pins the wiring and the flag
    name in the message.
    """

    @pytest.mark.parametrize("value", ["fr", "fr,nl", "fr_BE,nl-be", "pt-BR", "zh-Hant", "es-419"])
    def test_accepts_usable_tags(self, value):
        assert selection.resolve_locales(value, guide=False, config=None) == [v.strip() for v in value.split(",")]

    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            ("fr,index", "2 or 3 letters"),
            ("fr,x-default", "private-use"),
            ("fr,i-klingon", "tlh"),
            ("con", "reserved device name"),
            ("fr,fr--BE", "empty subtag"),
        ],
    )
    def test_rejects_unusable_tags(self, value, fragment):
        with pytest.raises(selection.SelectionError) as exc:
            selection.resolve_locales(value, guide=False, config=None)
        assert "--locales" in str(exc.value)
        assert fragment in str(exc.value)

    def test_config_derived_locales_are_not_re_validated(self):
        """They came from SteppyConfig, which validated them on load."""
        assert selection.resolve_locales(None, guide=True, config=_config()) == ["fr", "en", "nl"]

    def test_layouts_are_still_unvalidated(self):
        """Deliberate, and recorded: layout names are bare directory segments
        too, but nothing has ever checked them and
        tests/test_layout_fixes.py positively pins that an unknown one
        round-trips. Changing that is its own decision."""
        assert selection.resolve_layouts("desktopp", guide=False, config=None) == ["desktopp"]
