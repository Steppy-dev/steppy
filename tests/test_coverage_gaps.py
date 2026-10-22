"""End-to-end proof (browser-free): record fixtures -> merge -> refresh -> gap report.

Pins the user-facing contract:
- a single scenario run upserts the map idempotently (merge twice == merge once),
- the gap report lists declared-but-unreached endpoints,
- weak-assert rule, triage annotation, module decoding via inline maps.

The record fixtures use the exact schema the generated conftest recorder
emits (``steppy_coverage_record: 1``).
"""

import gzip
import hashlib
import json
from textwrap import dedent

import pytest

from steppy.coverage.gaps import compute_gaps
from steppy.coverage.manifest import refresh_declared
from steppy.coverage.merge import CoverageSettings, MergeError, merge_run
from steppy.coverage.report import gaps_to_json, print_summary
from steppy.coverage.store import dump_canonical, open_store, read_map, set_triage

RUN_ID = "20260611T100000Z-aaaaaa"

_MAP = {
    "version": 3,
    "sources": ["webpack:///./src/orders/orders.component.ts", "webpack:///./src/app.ts"],
    "names": [],
    "mappings": "AAAA,uBCAA",  # col 0 -> sources[0], col 23 -> sources[1]
}
_MAP_BYTES = json.dumps(_MAP).encode("utf-8")
_MAP_HASH = hashlib.sha256(_MAP_BYTES).hexdigest()

_OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/orders": {"get": {"operationId": "listOrders"}, "post": {"operationId": "createOrder"}},
        "/orders/{orderId}": {"get": {"operationId": "getOrder"}},
        "/webhooks/stripe": {"post": {"operationId": "stripeCallback"}},
        "/admin/jobs": {"post": {"operationId": "runJobs"}},
    },
}

_SCENARIO_MD = dedent("""\
    ---
    title: Order flow
    auth: false
    ---

    ### 1. Open orders
    - role: editor
    - action: navigate
    - target: /orders

    ### 2. Verify order row
    - role: editor
    - action: assert
    - expect: visible([data-testid="order-row"])
    """)

_GUIDE_SCENARIO_MD = dedent("""\
    ---
    title: Guided tour
    auth: false
    ---

    ### 1. Open the tour
    - role: editor
    - action: navigate
    - target: /tour
    """)


def _record(**overrides) -> dict:
    record = {
        "steppy_coverage_record": 1,
        "run_id": RUN_ID,
        "scenario": "order-flow",
        "role": "editor",
        "noauth": False,
        "locale": "fr",
        "layout": "desktop",
        "asserts_enabled": True,
        "guide_mode": False,
        "outcome": "passed",
        "started_at": "2026-06-11T10:00:00Z",
        "finished_at": "2026-06-11T10:00:30Z",
        "nav_events": [
            {"u": "http://localhost:4200/", "k": "nav", "t": 1.0},
            {"u": "http://localhost:4200/orders", "k": "nav", "t": 2.0},
            {"u": "http://localhost:4200/orders/123", "k": "nav", "t": 4.0},
        ],
        "spa_events": [
            {"u": "http://localhost:4200/orders", "k": "push", "t": 2.0},
            {"u": "http://localhost:4200/orders/123", "k": "push", "t": 4.0},
        ],
        "requests": [
            {"m": "GET", "u": "http://localhost:8080/orders", "rt": "xhr"},
            {"m": "GET", "u": "http://localhost:8080/orders/123?expand=lines", "rt": "fetch"},
            {"m": "GET", "u": "http://localhost:4200/assets/logo.png", "rt": "image"},
            {"m": "POST", "u": "https://analytics.example.com/track", "rt": "fetch"},
        ],
        "requests_dropped": 0,
        "js_coverage": {
            "ok": True,
            "reason": None,
            "scripts": [
                {
                    "url": "http://localhost:4200/main.js",
                    "executed_offsets": [0, 23],
                    "line_starts": [0],
                    "map": {"kind": "inline", "hash": _MAP_HASH},
                }
            ],
        },
    }
    record.update(overrides)
    return record


@pytest.fixture
def project(tmp_path):
    """A fake consumer project: raw run records + scenarios + openapi.json."""
    run_dir = tmp_path / "output" / "coverage" / "raw" / RUN_ID
    (run_dir / "order-flow").mkdir(parents=True)
    (run_dir / "order-flow" / "editor_page__fr-desktop.json").write_text(json.dumps(_record()), encoding="utf-8")
    maps_dir = run_dir / "_maps"
    maps_dir.mkdir()
    (maps_dir / f"{_MAP_HASH}.json.gz").write_bytes(gzip.compress(_MAP_BYTES))

    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "order-flow.md").write_text(_SCENARIO_MD, encoding="utf-8")
    (scenarios / "guide-tour.md").write_text(_GUIDE_SCENARIO_MD, encoding="utf-8")

    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(_OPENAPI), encoding="utf-8")

    settings = CoverageSettings(
        store_path=tmp_path / ".steppy" / "coverage.db",
        records_dir=tmp_path / "output" / "coverage",
        scenarios_dir=scenarios,
        apis=[{"name": "editor-api", "openapi": str(spec), "base_urls": ["http://localhost:8080"]}],
    )
    return settings


def _refresh(settings):
    conn = open_store(settings.store_path)
    try:
        refresh_declared(conn, settings.apis, updated_at="t1")
    finally:
        conn.close()


def test_single_scenario_merge_is_idempotent(project):
    summary = merge_run(project, RUN_ID)
    assert summary["scenarios"]["order-flow"]["records"] == 1
    conn = open_store(project.store_path)
    first = dump_canonical(conn)
    conn.close()

    merge_run(project, RUN_ID)  # same run again — must be a no-op
    conn = open_store(project.store_path)
    assert dump_canonical(conn) == first
    conn.close()


def test_gap_report_lists_declared_unreached(project):
    merge_run(project, RUN_ID)
    _refresh(project)
    conn = open_store(project.store_path)
    gaps = compute_gaps(read_map(conn))
    conn.close()

    unreached = {(e["method"], e["key"]) for e in gaps["endpoints"]["declared_unreached"]}
    assert unreached == {("POST", "/orders"), ("POST", "/webhooks/stripe"), ("POST", "/admin/jobs")}

    tested = {(e["method"], e["key"]) for e in gaps["endpoints"]["tested"]}
    assert tested == {("GET", "/orders"), ("GET", "/orders/{orderId}")}
    assert all(e["strength"] == "strong" for e in gaps["endpoints"]["tested"])

    # The analytics call matched no configured API -> kept, surfaced as unclassified.
    assert [e["key"] for e in gaps["endpoints"]["unclassified"]] == ["/track"]

    # Routes (no sitemap): discovered, strongly covered.
    discovered_routes = {e["key"] for e in gaps["routes"]["discovered_tested"]}
    assert discovered_routes == {"/", "/orders", "/orders/:id"}

    # Modules decoded through the inline map blob.
    modules = {m["key"] for m in gaps["modules"]["covered"]}
    assert modules == {"src/orders/orders.component.ts", "src/app.ts"}

    counts = gaps["counts"]["endpoints"]
    assert counts["declared"] == 5
    assert counts["tested"] == 2
    assert counts["declared_unreached"] == 3
    assert counts["declared_unreached_actionable"] == 3


def test_refresh_before_merge_converges_to_same_buckets(project):
    _refresh(project)
    merge_run(project, RUN_ID)
    conn = open_store(project.store_path)
    gaps = compute_gaps(read_map(conn))
    conn.close()
    tested = {(e["method"], e["key"]) for e in gaps["endpoints"]["tested"]}
    assert tested == {("GET", "/orders"), ("GET", "/orders/{orderId}")}
    assert len(gaps["endpoints"]["declared_unreached"]) == 3


def test_triage_non_ui_discounted_from_actionable(project):
    merge_run(project, RUN_ID)
    _refresh(project)
    conn = open_store(project.store_path)
    set_triage(
        conn,
        kind="endpoint",
        key="/webhooks/stripe",
        api="editor-api",
        method="POST",
        flag="non_ui",
        note="payment callback",
        updated_at="t2",
    )
    gaps = compute_gaps(read_map(conn))
    conn.close()
    counts = gaps["counts"]["endpoints"]
    assert counts["declared_unreached"] == 3
    assert counts["declared_unreached_actionable"] == 2
    webhook = next(e for e in gaps["endpoints"]["declared_unreached"] if e["key"] == "/webhooks/stripe")
    assert webhook["triage"] == "non_ui"


def test_weak_coverage_when_guide_mode_and_no_asserts(project):
    # A second scenario recorded in guide mode (asserts disabled) whose
    # markdown contains zero assert steps -> weak -> discovered_uncovered.
    run_dir = project.records_dir / "raw" / RUN_ID
    (run_dir / "guide-tour").mkdir()
    guide_record = _record(
        scenario="guide-tour",
        asserts_enabled=False,
        guide_mode=True,
        nav_events=[{"u": "http://localhost:4200/tour", "k": "nav", "t": 1.0}],
        spa_events=[],
        requests=[],
        js_coverage={"ok": False, "reason": "cdp unavailable", "scripts": []},
    )
    (run_dir / "guide-tour" / "editor_page__fr-desktop.json").write_text(json.dumps(guide_record), encoding="utf-8")

    summary = merge_run(project, RUN_ID)
    assert summary["scenarios"]["guide-tour"]["modules_unavailable"] is True

    conn = open_store(project.store_path)
    gaps = compute_gaps(read_map(conn))
    conn.close()
    weak_routes = {e["key"] for e in gaps["routes"]["discovered_uncovered"]}
    assert weak_routes == {"/tour"}
    strong_routes = {e["key"] for e in gaps["routes"]["discovered_tested"]}
    assert "/orders" in strong_routes


def test_rerun_with_fewer_signals_replaces_contribution(project):
    merge_run(project, RUN_ID)
    # Re-record the scenario: this time it only visits the home page.
    new_run = "20260611T110000Z-bbbbbb"
    run_dir = project.records_dir / "raw" / new_run / "order-flow"
    run_dir.mkdir(parents=True)
    slim = _record(
        run_id=new_run,
        nav_events=[{"u": "http://localhost:4200/", "k": "nav", "t": 1.0}],
        spa_events=[],
        requests=[],
        js_coverage={"ok": True, "reason": None, "scripts": []},
    )
    (run_dir / "editor_page__fr-desktop.json").write_text(json.dumps(slim), encoding="utf-8")

    merge_run(project, run_id=None)  # None -> latest run dir wins
    conn = open_store(project.store_path)
    gaps = compute_gaps(read_map(conn))
    conn.close()
    covered_routes = {e["key"] for e in gaps["routes"]["discovered_tested"]}
    assert covered_routes == {"/"}  # stale /orders contributions are gone
    assert gaps["counts"]["endpoints"]["discovered_tested"] == 0


def test_merge_missing_run_raises(project):
    with pytest.raises(MergeError, match="not found"):
        merge_run(project, "20990101T000000Z-zzzzzz")


def test_report_renders(project):
    from rich.console import Console

    merge_run(project, RUN_ID)
    _refresh(project)
    conn = open_store(project.store_path)
    gaps = compute_gaps(read_map(conn))
    conn.close()

    payload = json.loads(gaps_to_json(gaps))
    assert payload["counts"]["endpoints"]["declared_unreached"] == 3

    console = Console(record=True, width=200)
    print_summary(gaps, console)
    text = console.export_text()
    assert "declared-unreached" in text
    assert "/webhooks/stripe" in text
