"""Conftest recorder template: renders, parses, gated off by default.

The recorder lives in the GENERATED conftest (both template trees, byte-
identical — see test_template_trees_in_sync.py). These tests render the
template the same way the generator does and pin:
- the emitted Python is syntactically valid,
- the recorder is fully env-gated (``STEPPY_COVERAGE``), returning None when off,
- ordering: start after ``_attach_page_diagnostics``, flush before ``page.close()``,
  in BOTH the auth and noauth fixture variants,
- the makereport hook stamps the call outcome for the recorder teardown.
"""

import ast
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenario_file


@pytest.fixture(scope="module")
def conftest_src(tmp_path_factory) -> str:
    scenario_md = tmp_path_factory.mktemp("scn") / "smoke.md"
    scenario_md.write_text(
        dedent("""\
            ---
            title: Smoke
            auth: false
            ---

            ### 1. Open home
            - role: editor
            - action: navigate
            - target: /
            """),
        encoding="utf-8",
    )
    scenario = parse_scenario_file(scenario_md)
    return SteppyGenerator(load_config()).generate_conftest([scenario])


def test_generated_conftest_parses(conftest_src):
    ast.parse(conftest_src)


def test_recorder_is_env_gated(conftest_src):
    assert 'COVERAGE_ENABLED = bool(int(__import__("os").environ.get("STEPPY_COVERAGE") or "0"))' in conftest_src
    assert "if not COVERAGE_ENABLED:\n        return None" in conftest_src


def test_recorder_ordering_in_both_fixture_variants(conftest_src):
    for noauth in (False, True):
        marker = f"noauth={noauth}"
        for chunk in _fixture_chunks(conftest_src, marker):
            attach = chunk.index("_attach_page_diagnostics(page)")
            start = chunk.index("_coverage_start(")
            yield_pos = chunk.index("yield page")
            stop = chunk.index("_coverage_stop_and_write(_cov, request)")
            close = chunk.index("page.close()")
            assert attach < start < yield_pos < stop < close, f"recorder mis-ordered in fixture variant {marker}"


def _fixture_chunks(src: str, marker: str) -> list[str]:
    """Slice each fixture body containing ``marker`` out of the source."""
    chunks = []
    pos = 0
    while True:
        hit = src.find(marker, pos)
        if hit == -1:
            break
        start = src.rfind("@pytest.fixture", 0, hit)
        end = src.find("CURRENT_LAYOUT = None", hit)
        chunks.append(src[start : end + len("CURRENT_LAYOUT = None")])
        pos = hit + 1
    assert chunks, f"no fixture found containing {marker}"
    return chunks


def test_makereport_stamps_call_outcome(conftest_src):
    assert "item._steppy_call_failed = rep.failed" in conftest_src


def test_history_hook_covers_spa_events(conftest_src):
    for token in (
        "history.pushState",
        "history.replaceState",
        "popstate",
        "hashchange",
        "__steppy_route_event",
        "framenavigated",
    ):
        assert token in conftest_src, f"missing {token} in recorder"


def test_cdp_profiler_with_graceful_degradation(conftest_src):
    # playwright-python has no page.coverage API — must go through CDP.
    assert "Profiler.startPreciseCoverage" in conftest_src
    assert "Profiler.takePreciseCoverage" in conftest_src
    assert '"ok": False' in conftest_src  # degradation path exists
    # No Node.js-style coverage API calls (they don't exist in playwright-python).
    assert "page.coverage.startJSCoverage" not in conftest_src
    assert "coverage.start_js_coverage" not in conftest_src


def test_record_schema_version_pinned(conftest_src):
    # The merge side (steppy.coverage.merge.RECORD_SCHEMA_VERSION) rejects
    # records with another version — keep the two in sync.
    from steppy.coverage.merge import RECORD_SCHEMA_VERSION

    assert f'"steppy_coverage_record": {RECORD_SCHEMA_VERSION},' in conftest_src


def test_recorder_flush_is_exception_proof(conftest_src):
    flush = conftest_src.index("def _coverage_stop_and_write")
    body = conftest_src[flush : conftest_src.index("def ", flush + 10)]
    assert "except Exception" in body
    assert "coverage record failed" in body
