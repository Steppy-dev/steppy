"""``steppy bundle create`` — classification, manifest, archive, round trip.

The synthetic tree below is deliberately the *hard* shape rather than the
common one: locales crossed with layouts, a multi-frame ``fill-form`` capture,
coverage records from two different runs, artifacts named after a sanitised
(and therefore un-invertible) pytest nodeid, and both event and JUnit sources
of truth disagreeing about which tests exist. ``fixtures/demo-guide/output`` is
the easy shape and gets its own test at the bottom.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from steppy.bundle import BundleError, create_bundle, inspect_bundle, verify_bundle
from steppy.bundle.archive import zstandard_available

CONFIG = """\
apps:
  editor:
    name: Editor
    url: http://localhost:4200
    auth:
      mode: form
      credentials: {username: u, password: p}
locales: [fr, nl]
screenshot:
  layouts:
    desktop: {width: 1280, height: 800}
    mobile: {width: 390, height: 844}
"""

RUN_ID = "20260806T100000Z-aaaaaa"
OLD_RUN_ID = "20260805T100000Z-bbbbbb"
NODEID = "tests/e2e/test_order_lifecycle.py::test_flow[fr-mobile]"
SANITISED = "tests_e2e_test_order_lifecycle.py__test_flow(fr-mobile)"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_project(tmp_path: Path) -> Path:
    """A project root whose output/ exercises every classification branch.

    A plain function as well as a fixture so ``test_bundle_contract_parity``
    can build the same hard shape — the parity check is only worth running
    against a manifest that has every field populated.
    """
    root = tmp_path / "proj"
    _write(root / "config.yaml", CONFIG)
    out = root / "output"

    shots = out / "screenshots" / "order-lifecycle"
    for stem in ("step-01", "step-02", "step-03-a", "step-03-b"):
        _write(shots / "fr" / "mobile" / f"{stem}.png", f"png-{stem}")
        _write(shots / "fr" / "mobile" / f"{stem}.json", json.dumps({"step": 1, "device_scale_factor": 3}))
    _write(shots / "nl" / "desktop" / "step-01.png", "png-nl")
    # Flat/legacy capture for a second scenario — the back-compat shape.
    _write(out / "screenshots" / "onboarding" / "step-01.png", "png-onboarding")

    _write(out / "screenshots" / "_failures" / f"{SANITISED}__editor_page.png", "failshot")

    cov = out / "coverage" / "raw"
    _write(
        cov / RUN_ID / "order-lifecycle" / "editor_page__fr-mobile.json",
        json.dumps({"steppy_coverage_record": 1, "scenario": "order-lifecycle", "locale": "fr", "layout": "mobile"}),
    )
    _write(
        cov / RUN_ID / "order-lifecycle" / "editor_page_noauth__default-default.json",
        json.dumps({"steppy_coverage_record": 1, "scenario": "order-lifecycle", "locale": None, "layout": None}),
    )
    _write(cov / RUN_ID / "_maps" / "abc.json.gz", "gz")
    # A PREVIOUS run's records, which must not end up in this bundle.
    _write(cov / OLD_RUN_ID / "order-lifecycle" / "editor_page__fr-mobile.json", "stale")
    # The gap report the run leaves beside its records — the only way a detached
    # bundle can show a real coverage surface, since the sqlite store it comes
    # from lives outside output/ and never travels.
    _write(out / "coverage" / "gaps.json", json.dumps({"counts": {}, "e2e_surface": {}}))
    # Something nobody labelled, under the coverage root. It must SHIP as
    # `other`, not vanish.
    _write(out / "coverage" / "notes.txt", "hand-written note")

    _write(out / "failures" / "order-lifecycle" / "pytest.log", "traceback")
    _write(out / "failures" / "order-lifecycle" / "summary.txt", "summary")
    _write(out / "failures" / "_worker-logs" / "w1-stack.log", "stack")

    _write(out / "guides" / "editor" / "fr" / "mobile" / "order-lifecycle.html", "<html/>")
    _write(out / "guides" / "editor" / "fr" / "mobile" / "order-lifecycle.md", "# guide")

    _write(out / "traces" / f"{SANITISED}.zip", "trace")
    _write(out / "videos" / f"{SANITISED}.webm", "video")

    _write(
        out / "events" / RUN_ID / "order-lifecycle__fr-mobile.jsonl",
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "v": 1,
                    "kind": "test-start",
                    "ts": 1.0,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                },
                # Step 1 completed; step 2 started and never closed — the shape
                # a generated body leaves behind when a step raises.
                {
                    "v": 1,
                    "kind": "step-start",
                    "ts": 1.1,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                    "step": 1,
                    "title": "Open the list",
                    "action": "navigate",
                },
                {
                    "v": 1,
                    "kind": "step-end",
                    "ts": 1.5,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                    "step": 1,
                },
                {
                    "v": 1,
                    "kind": "step-start",
                    "ts": 1.6,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                    "step": 2,
                    "title": "Click a missing button",
                    "action": "click",
                },
                {
                    "v": 1,
                    "kind": "test-end",
                    "ts": 2.0,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                    "outcome": "failed",
                    "duration_ms": 41200,
                },
                {
                    "v": 1,
                    "kind": "test-fail-tail",
                    "ts": 2.0,
                    "nodeid": NODEID,
                    "scenario": "order-lifecycle",
                    "locale": "fr",
                    "layout": "mobile",
                    "tail_b64": "Ym9vbQ==",
                },
            )
        )
        + "\n",
    )
    _write(
        out / "junit" / RUN_ID / "order-lifecycle.xml",
        '<?xml version="1.0"?>\n'
        '<testsuites><testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0" time="44.2">'
        '<testcase classname="tests.e2e.test_order_lifecycle" name="test_flow[fr-mobile]" time="41.2">'
        '<failure message="boom">boom</failure></testcase>'
        '<testcase classname="tests.e2e.test_order_lifecycle" name="test_other[nl-desktop]" time="3.0"/>'
        "</testsuite></testsuites>\n",
    )
    _write(
        out / ".steppy-run.json",
        json.dumps(
            {
                "steppy_run": 1,
                "run_id": RUN_ID,
                "created_at": "2026-08-06T10:00:00Z",
                "steppy_version": "0.3.0",
                "executor": "local",
                "spec": {
                    "guide": True,
                    "no_asserts": True,
                    "screenshots": True,
                    "coverage": True,
                    "locales": ["fr", "nl"],
                    "layouts": ["desktop", "mobile"],
                    "workers": 4,
                    "tag": None,
                    "scenario": None,
                },
            },
            indent=2,
        ),
    )
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project(tmp_path)


def _by_path(manifest) -> dict:
    return {f.path: f for f in manifest.files}


# ── classification ──────────────────────────────────────────────────────────


def test_every_kind_is_classified_with_the_right_attribution(project: Path):
    result = create_bundle(project_root=project, fmt="gz")
    files = _by_path(result.manifest)

    shot = files["output/screenshots/order-lifecycle/fr/mobile/step-03-b.png"]
    assert (shot.kind, shot.scenario, shot.locale, shot.layout, shot.step, shot.frame) == (
        "screenshot",
        "order-lifecycle",
        "fr",
        "mobile",
        3,
        "b",
    )
    assert files["output/screenshots/order-lifecycle/fr/mobile/step-01.json"].kind == "sidecar"

    flat = files["output/screenshots/onboarding/step-01.png"]
    assert (flat.locale, flat.layout, flat.step) == (None, None, 1)

    cov = files[f"output/coverage/raw/{RUN_ID}/order-lifecycle/editor_page__fr-mobile.json"]
    assert (cov.kind, cov.scenario, cov.locale, cov.layout) == ("coverage_record", "order-lifecycle", "fr", "mobile")

    # The literal "default" sentinel maps back to None.
    noauth = files[f"output/coverage/raw/{RUN_ID}/order-lifecycle/editor_page_noauth__default-default.json"]
    assert (noauth.locale, noauth.layout) == (None, None)

    assert files[f"output/coverage/raw/{RUN_ID}/_maps/abc.json.gz"].kind == "coverage_sourcemap"
    assert files["output/failures/order-lifecycle/pytest.log"].kind == "failure_log"
    assert files["output/failures/order-lifecycle/pytest.log"].scenario == "order-lifecycle"
    assert files["output/failures/_worker-logs/w1-stack.log"].kind == "worker_log"

    guide = files["output/guides/editor/fr/mobile/order-lifecycle.html"]
    assert (guide.kind, guide.scenario, guide.locale, guide.layout) == (
        "guide_html",
        "order-lifecycle",
        "fr",
        "mobile",
    )
    assert files["output/guides/editor/fr/mobile/order-lifecycle.md"].kind == "guide_md"


def test_nodeid_named_artifacts_are_attributed_by_forward_map(project: Path):
    """The sanitiser is lossy, so attribution comes from sanitising the
    nodeids the run reported — never from parsing the filename back."""
    result = create_bundle(project_root=project, fmt="gz")
    files = _by_path(result.manifest)

    for path, kind in (
        (f"output/screenshots/_failures/{SANITISED}__editor_page.png", "failure_screenshot"),
        (f"output/traces/{SANITISED}.zip", "trace"),
        (f"output/videos/{SANITISED}.webm", "video"),
    ):
        entry = files[path]
        assert entry.kind == kind
        assert (entry.scenario, entry.locale, entry.layout) == ("order-lifecycle", "fr", "mobile"), path


def test_a_previous_runs_coverage_records_are_excluded(project: Path):
    result = create_bundle(project_root=project, fmt="gz")
    paths = set(_by_path(result.manifest))
    assert not any(OLD_RUN_ID in p for p in paths), "records from another run leaked into the bundle"


def test_the_coverage_gap_report_ships_with_its_own_kind(project: Path):
    """Without this the report can only show *observed* routes for a downloaded
    bundle: the sqlite store lives outside output/ and the declared surface comes
    off the network, so gaps.json is the one artifact that can carry the answer.
    """
    result = create_bundle(project_root=project, fmt="gz")
    files = _by_path(result.manifest)
    assert files["output/coverage/gaps.json"].kind == "coverage_gaps"


def test_an_unlabelled_file_under_the_coverage_root_ships_as_other(project: Path):
    """It used to VANISH. ``_coverage_file`` returned None — which the walker
    reads as "different run, skip entirely" — for anything that was not
    ``raw/<run_id>/…``, so gaps.json and any hand-dropped file were silently
    dropped from every bundle. This module's own rule is the opposite: carrying a
    file nobody labelled beats dropping one nobody noticed.
    """
    result = create_bundle(project_root=project, fmt="gz")
    files = _by_path(result.manifest)
    assert files["output/coverage/notes.txt"].kind == "other"


def test_no_coverage_also_drops_the_gap_report(project: Path):
    """--no-coverage is a scope declaration; leaving the gap report behind would
    ship a coverage answer for a bundle that carries no coverage evidence."""
    result = create_bundle(project_root=project, fmt="gz", coverage=False)
    assert "coverage_gaps" not in {f.kind for f in result.manifest.files}


def test_the_gap_report_is_never_dropped_by_the_size_budget(project: Path):
    """It is kilobytes, and it is the only route to a real coverage section in a
    detached bundle — so it is not in DROP_ORDER, like screenshots."""
    result = create_bundle(project_root=project, fmt="gz", max_size=300)
    assert result.manifest.truncated is True
    assert "coverage_gaps" in {f.kind for f in result.manifest.files}


def test_flags_exclude_kinds_without_marking_the_bundle_truncated(project: Path):
    """An excluded artifact is the user's declared scope, not a loss —
    ``truncated`` must stay false or a consumer cannot tell the two apart."""
    result = create_bundle(project_root=project, fmt="gz", guides=False, traces=False, videos=False, coverage=False)
    kinds = {f.kind for f in result.manifest.files}
    assert not kinds & {"guide_html", "guide_md", "trace", "video", "coverage_record", "coverage_sourcemap"}
    assert result.manifest.truncated is False


def test_size_budget_drops_cheap_kinds_first_and_marks_truncated(project: Path):
    result = create_bundle(project_root=project, fmt="gz", max_size=300)
    kinds = {f.kind for f in result.manifest.files}
    assert result.manifest.truncated is True
    assert "video" not in kinds and "trace" not in kinds
    # Never dropped, whatever the budget.
    assert "screenshot" in kinds and "sidecar" in kinds
    assert any("dropped" in w for w in result.manifest.warnings), result.manifest.warnings


# ── tests[] ─────────────────────────────────────────────────────────────────


def test_events_win_over_junit_and_junit_fills_the_gaps(project: Path):
    result = create_bundle(project_root=project, fmt="gz")
    tests = {t.nodeid: t for t in result.manifest.tests}
    assert len(tests) == 2

    from_events = tests[NODEID]
    assert (from_events.outcome, from_events.scenario, from_events.locale, from_events.layout) == (
        "failed",
        "order-lifecycle",
        "fr",
        "mobile",
    )
    assert from_events.duration_ms == 41200
    assert from_events.fail_tail_b64 == "Ym9vbQ=="
    # Step 1 closed, step 2 did not — so the failure was inside step 2.
    assert from_events.failed_step == 2

    junit_only = tests["tests/e2e/test_order_lifecycle.py::test_other[nl-desktop]"]
    assert junit_only.outcome == "passed"
    # The scenario IS recoverable — the generator names every file
    # ``test_<slug>.py``, so the module name is an exact mapping. Locale and
    # layout are NOT: they live in the parametrization id, and guessing there is
    # what the event sink exists to avoid.
    assert junit_only.scenario == "order_lifecycle"
    assert (junit_only.locale, junit_only.layout) == (None, None)
    assert any("absent from the event records" in w for w in result.manifest.warnings)


def _rewrite_events(project: Path, rows: list[dict]) -> None:
    path = project / "output" / "events" / RUN_ID / "order-lifecycle__fr-mobile.jsonl"
    _write(path, "\n".join(json.dumps(r) for r in rows) + "\n")


def _event(kind: str, **fields) -> dict:
    base = {
        "v": 1,
        "kind": kind,
        "ts": 1.0,
        "nodeid": NODEID,
        "scenario": "order-lifecycle",
        "locale": "fr",
        "layout": "mobile",
    }
    base.update(fields)
    return base


def _only_test(manifest):
    return next(t for t in manifest.tests if t.nodeid == NODEID)


def test_a_passing_test_has_no_failed_step(project: Path):
    _rewrite_events(
        project,
        [
            _event("test-start"),
            _event("step-start", step=1, title="a", action="click"),
            _event("step-end", step=1),
            _event("test-end", outcome="passed", duration_ms=10),
        ],
    )
    assert _only_test(create_bundle(project_root=project, fmt="gz").manifest).failed_step is None


def test_a_failure_with_every_step_closed_has_no_failed_step(project: Path):
    """The teardown case. "Last step reached" would blame step 2 here, pointing
    a reader at code that ran fine — which is the whole reason the emitter
    brackets steps instead of only marking their starts."""
    _rewrite_events(
        project,
        [
            _event("test-start"),
            _event("step-start", step=1, title="a", action="click"),
            _event("step-end", step=1),
            _event("step-start", step=2, title="b", action="click"),
            _event("step-end", step=2),
            _event("test-end", outcome="error", duration_ms=10),
        ],
    )
    assert _only_test(create_bundle(project_root=project, fmt="gz").manifest).failed_step is None


def test_failed_step_is_the_scenarios_step_number_not_an_ordinal(project: Path):
    """Steps skipped by ``only-in:``/``layouts:`` emit no markers at all, so the
    number reported is the one written in the scenario file."""
    _rewrite_events(
        project,
        [
            _event("test-start"),
            _event("step-start", step=1, title="a", action="click"),
            _event("step-end", step=1),
            # steps 2 and 3 were skipped by a guard — no records at all
            _event("step-start", step=4, title="d", action="click"),
            _event("test-end", outcome="failed", duration_ms=10),
        ],
    )
    assert _only_test(create_bundle(project_root=project, fmt="gz").manifest).failed_step == 4


def test_a_rerun_does_not_inherit_the_previous_attempts_open_step(project: Path):
    """`--reruns` replays the same nodeid into the same file. Attempt 1's
    unclosed step must not be blamed for attempt 2's result."""
    _rewrite_events(
        project,
        [
            _event("test-start"),
            _event("step-start", step=2, title="b", action="click"),
            _event("test-end", outcome="failed", duration_ms=10),
            _event("test-start"),
            _event("step-start", step=1, title="a", action="click"),
            _event("step-end", step=1),
            _event("test-end", outcome="passed", duration_ms=10),
        ],
    )
    result = create_bundle(project_root=project, fmt="gz").manifest
    test = _only_test(result)
    assert (test.outcome, test.failed_step) == ("passed", None)
    assert not [w for w in result.warnings if "still open" in w]


def test_a_lost_step_end_on_a_passing_test_warns_instead_of_lying(project: Path):
    _rewrite_events(
        project,
        [
            _event("test-start"),
            _event("step-start", step=1, title="a", action="click"),
            _event("test-end", outcome="passed", duration_ms=10),
        ],
    )
    result = create_bundle(project_root=project, fmt="gz").manifest
    assert _only_test(result).failed_step is None
    assert any("still open" in w for w in result.warnings), result.warnings


def test_every_test_row_carries_a_scenario(project: Path):
    """``BundleTest.scenario`` is required by the contract, because a Steppy test
    belongs to a scenario by construction. Emitting null makes a consumer reject
    the whole manifest — caught the hard way against the control plane's parser."""
    result = create_bundle(project_root=project, fmt="gz")
    assert result.manifest.tests
    for test in result.manifest.tests:
        assert test.scenario, f"{test.nodeid} has no scenario"


def test_missing_outcomes_warn_but_still_produce_a_valid_bundle(project: Path):
    import shutil

    shutil.rmtree(project / "output" / "events")
    shutil.rmtree(project / "output" / "junit")
    result = create_bundle(project_root=project, fmt="gz", run_id=RUN_ID)
    assert result.manifest.tests == []
    assert any("no test outcomes recorded" in w for w in result.manifest.warnings)


def test_torn_event_lines_are_skipped_not_fatal(project: Path):
    """A killed worker leaves a half-written last line."""
    path = project / "output" / "events" / RUN_ID / "order-lifecycle__fr-mobile.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"v":1,"kind":"test-en', encoding="utf-8")
    result = create_bundle(project_root=project, fmt="gz")
    assert any("malformed event line" in w for w in result.manifest.warnings)
    assert len(result.manifest.tests) == 2


# ── run identity ────────────────────────────────────────────────────────────


def test_run_id_ladder(project: Path, tmp_path: Path):
    assert create_bundle(project_root=project, fmt="gz").run_id_source == "sidecar"
    explicit = create_bundle(project_root=project, fmt="gz", run_id="FROM-THE-PLANE")
    assert (explicit.run_id, explicit.run_id_source) == ("FROM-THE-PLANE", "explicit")

    (project / "output" / ".steppy-run.json").unlink()
    discovered = create_bundle(project_root=project, fmt="gz")
    assert (discovered.run_id, discovered.run_id_source) == (RUN_ID, "discovered")


def test_spec_comes_from_the_run_not_from_the_bundler(project: Path):
    spec = create_bundle(project_root=project, fmt="gz").manifest.run.spec
    assert spec.workers == 4
    assert spec.locales == ["fr", "nl"] and spec.layouts == ["desktop", "mobile"]
    assert spec.guide is True and spec.coverage is True


def test_missing_output_directory_is_an_actionable_error(tmp_path: Path):
    with pytest.raises(BundleError, match="steppy run --bundle"):
        create_bundle(project_root=tmp_path, fmt="gz")


def test_config_output_paths_are_followed(project: Path):
    """``config.output.screenshots_dir`` / ``guides_dir`` are live knobs since
    FLAWS §4.6 — the generated conftest resolves the capture root from config at
    runtime and ``steppy guide`` defaults its flags from it — so the bundler has
    to follow them or it archives an empty directory while the artifacts sit
    where the run actually put them."""
    import shutil

    out = project / "output"
    shutil.move(str(out / "screenshots"), str(out / "shots"))
    shutil.move(str(out / "guides"), str(out / "books"))
    (project / "config.yaml").write_text(
        CONFIG + "output:\n  screenshots_dir: ./output/shots\n  guides_dir: ./output/books\n",
        encoding="utf-8",
    )
    result = create_bundle(project_root=project, fmt="gz")

    assert not [w for w in result.manifest.warnings if "output" in w and "outside" in w], result.manifest.warnings
    shot = _by_path(result.manifest)["output/shots/order-lifecycle/fr/mobile/step-03-b.png"]
    assert (shot.kind, shot.scenario, shot.locale, shot.layout) == ("screenshot", "order-lifecycle", "fr", "mobile")
    guide = _by_path(result.manifest)["output/books/editor/fr/mobile/order-lifecycle.html"]
    assert (guide.kind, guide.scenario, guide.locale, guide.layout) == ("guide_html", "order-lifecycle", "fr", "mobile")


def test_output_path_outside_the_output_tree_warns_that_it_is_not_archived(project: Path):
    """A bundle archives ``output/`` and nothing else. Relocating a root beyond
    it is allowed everywhere else, so the bundle must say the artifacts are
    missing — a consumer has to be able to tell "no guides were built" from
    "the guides are somewhere this archive can't see"."""
    (project / "config.yaml").write_text(
        CONFIG + "output:\n  screenshots_dir: ../artifacts/shots\n",
        encoding="utf-8",
    )
    result = create_bundle(project_root=project, fmt="gz")

    assert any("outside" in w and "screenshots_dir" in w for w in result.manifest.warnings), result.manifest.warnings


def test_coverage_records_dir_is_honoured_because_the_conftest_reads_it(project: Path):
    """The one live knob in this area: the capture-time recorder resolves it, so
    the bundler must follow it or miss the records entirely."""
    import shutil

    out = project / "output"
    shutil.move(str(out / "coverage"), str(out / "cov-records"))
    (project / "config.yaml").write_text(
        CONFIG + "coverage:\n  records_dir: ./output/cov-records\n  apis: []\n",
        encoding="utf-8",
    )
    result = create_bundle(project_root=project, fmt="gz")
    records = [f for f in result.manifest.files if f.kind == "coverage_record"]
    assert len(records) == 2, [f.path for f in result.manifest.files]
    assert result.manifest.coverage is not None and result.manifest.coverage.records == 2


# ── archive ─────────────────────────────────────────────────────────────────


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "fmt",
    ["gz", pytest.param("zst", marks=pytest.mark.skipif(not zstandard_available(), reason="needs steppy-dev[bundle]"))],
)
def test_archives_are_reproducible(project: Path, tmp_path: Path, fmt: str):
    """Unchanged tree ⇒ identical bytes. The content-addressed upload protocol
    the contract describes is worthless without this."""
    ext = "tar.zst" if fmt == "zst" else "tar.gz"
    a = create_bundle(project_root=project, fmt=fmt, target=tmp_path / f"a.{ext}").path
    b = create_bundle(project_root=project, fmt=fmt, target=tmp_path / f"b.{ext}").path
    assert _sha(a) == _sha(b)


def test_archive_members_are_normalised_and_ordered(project: Path, tmp_path: Path):
    path = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz").path
    with tarfile.open(path, "r:gz") as tar:
        members = list(tar)
    assert members[0].name == "manifest.json", "manifest must be readable without a full scan"
    for member in members:
        assert member.isfile(), f"{member.name}: no directory members, for cross-platform stability"
        assert (member.mtime, member.uid, member.gid, member.uname, member.gname) == (0, 0, 0, "", "")
        assert "\\" not in member.name, "archive paths must be POSIX even when built on Windows"


def test_round_trip_reproduces_the_output_tree_verbatim(project: Path, tmp_path: Path):
    """The no-path-rewriting promise: an unpacked bundle feeds `steppy guide`
    and `steppy build` unchanged."""
    path = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz").path
    dest = tmp_path / "unpacked"
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(dest, filter="data")

    source_shots = project / "output" / "screenshots"
    for original in sorted(p for p in source_shots.rglob("*") if p.is_file()):
        rel = original.relative_to(project / "output")
        assert (dest / "output" / rel).read_bytes() == original.read_bytes()

    assert (dest / "manifest.json").is_file()
    assert (dest / "junit.xml").is_file()
    assert (dest / "events" / "order-lifecycle__fr-mobile.jsonl").is_file()


def test_generated_members_are_content_addressed_too(project: Path, tmp_path: Path):
    """Everything in the archive except manifest.json carries a digest, so a
    consumer's dedup covers every byte it might have to fetch."""
    result = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz")
    paths = _by_path(result.manifest)
    assert "junit.xml" in paths and paths["junit.xml"].kind == "junit"
    assert "events/order-lifecycle__fr-mobile.jsonl" in paths
    assert "manifest.json" not in paths, "the manifest cannot contain its own digest"


def test_merged_junit_has_one_shape_and_summed_counters(project: Path, tmp_path: Path):
    """A parallel run writes one XML per scenario; the archive always carries
    exactly one <testsuites>, whatever the executor was."""
    _write(
        project / "output" / "junit" / RUN_ID / "second.xml",
        '<?xml version="1.0"?><testsuite name="pytest" tests="1" failures="0" errors="0" skipped="1" time="1.0">'
        '<testcase classname="tests.e2e.test_b" name="test_b"><skipped/></testcase></testsuite>',
    )
    path = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz").path
    with tarfile.open(path, "r:gz") as tar:
        xml = tar.extractfile("junit.xml").read().decode("utf-8")

    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    assert root.tag == "testsuites"
    assert root.get("tests") == "3" and root.get("failures") == "1" and root.get("skipped") == "1"
    assert len(root.findall("testsuite")) == 2


# ── inspect / verify ────────────────────────────────────────────────────────


def test_inspect_reads_the_manifest_without_extracting(project: Path, tmp_path: Path):
    path = create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.gz").path
    manifest = inspect_bundle(path)
    assert manifest.steppy_bundle == 1
    assert manifest.run.run_id == RUN_ID


@pytest.mark.parametrize(
    "fmt",
    ["gz", pytest.param("zst", marks=pytest.mark.skipif(not zstandard_available(), reason="needs steppy-dev[bundle]"))],
)
def test_verify_accepts_an_intact_bundle_and_catches_corruption(project: Path, tmp_path: Path, fmt: str):
    path = create_bundle(project_root=project, fmt=fmt, target=tmp_path / f"b.tar.{fmt}").path
    _manifest, problems = verify_bundle(path)
    assert problems == []

    # Which error a flipped byte causes depends on where it lands — a tar header,
    # a compressed block, the stream's checksum — and the compressed layout
    # differs between zlib builds. So flip bytes all along the archive: each one
    # must be a BundleError (the manifest is unreadable) or a reported problem,
    # never another exception and never a clean pass. The gzip header's mtime,
    # flags and OS bytes (4-9) are outside its checksum by design.
    good = path.read_bytes()
    start = 10 if fmt == "gz" else 0
    for pos in range(start, len(good), max(1, len(good) // 80)):
        raw = bytearray(good)
        raw[pos] ^= 0xFF
        path.write_bytes(bytes(raw))
        try:
            _manifest, problems = verify_bundle(path)
        except BundleError:
            continue
        assert problems, f"a flipped byte at offset {pos} of {len(good)} went unnoticed"


def test_inspect_rejects_a_non_bundle(tmp_path: Path):
    plain = tmp_path / "not-a-bundle.tar.gz"
    with tarfile.open(plain, "w:gz") as tar:
        source = tmp_path / "x.txt"
        source.write_text("hi", encoding="utf-8")
        tar.add(source, arcname="x.txt")
    with pytest.raises(BundleError, match="no manifest.json|contains no"):
        inspect_bundle(plain)


# ── codec dispatch ──────────────────────────────────────────────────────────


def test_explicit_zst_without_the_extra_errors_rather_than_downgrading(project: Path, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("steppy.bundle.archive.zstandard_available", lambda: False)
    with pytest.raises(BundleError, match=r"steppy-dev\[bundle\]"):
        create_bundle(project_root=project, fmt="zst", target=tmp_path / "b.tar.zst")


def test_auto_falls_back_to_gzip_when_the_extra_is_absent(project: Path, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("steppy.bundle.archive.zstandard_available", lambda: False)
    result = create_bundle(project_root=project, fmt="auto", target=tmp_path)
    assert result.fmt == "gz"
    assert result.path.name.endswith(".tar.gz")


def test_output_filename_and_format_may_not_contradict(project: Path, tmp_path: Path):
    with pytest.raises(BundleError, match="contradicts"):
        create_bundle(project_root=project, fmt="gz", target=tmp_path / "b.tar.zst")


def test_a_previous_bundle_is_not_archived_into_the_next(project: Path):
    """output/bundles/ is the default destination; without excluding it each
    run would swallow its predecessor."""
    create_bundle(project_root=project, fmt="gz")
    second = create_bundle(project_root=project, fmt="gz")
    assert not any(f.path.startswith("output/bundles/") for f in second.manifest.files)


# ── the easy shape ──────────────────────────────────────────────────────────


def test_demo_guide_fixture_bundles_end_to_end(tmp_path: Path):
    """`fixtures/demo-guide/output` is flat — no locale, no layout, no events.
    A project that never opted into --bundle must still bundle."""
    import shutil

    source = Path(__file__).parent.parent / "fixtures" / "demo-guide" / "output"
    root = tmp_path / "demo"
    shutil.copytree(source, root / "output")

    result = create_bundle(project_root=root, fmt="gz", run_id="FIXED")
    kinds = {f.kind for f in result.manifest.files}
    assert kinds == {"screenshot", "sidecar"}
    assert {f.scenario for f in result.manifest.files} == {"onboarding", "invoices"}
    assert all(f.locale is None and f.layout is None for f in result.manifest.files)
    assert verify_bundle(result.path)[1] == []
