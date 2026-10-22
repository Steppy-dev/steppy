"""Manifest ingest: OpenAPI parsing, sitemap routes, refresh + re-link pass."""

import json

import pytest

from steppy.coverage.manifest import (
    ManifestError,
    parse_manifest_document,
    parse_openapi_endpoints,
    parse_sitemap_routes,
    refresh_declared,
)
from steppy.coverage.store import (
    Contribution,
    dump_canonical,
    open_store,
    replace_scenario_contribution,
)

_OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/orders": {
            "get": {"operationId": "listOrders", "tags": ["orders"]},
            "post": {"operationId": "createOrder"},
            "parameters": [{"name": "tenant", "in": "header"}],  # not a method
            "x-internal": True,  # extension key, skipped
        },
        "/orders/{orderId}": {
            "get": {"operationId": "getOrder", "summary": "Fetch one order"},
            "delete": {"operationId": "deleteOrder"},
        },
        "/webhooks/stripe": {"post": {"operationId": "stripeCallback"}},
    },
}

_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://app.example.com/orders/123</loc></url>
  <url><loc>https://app.example.com/orders/456</loc></url>
  <url><loc>https://app.example.com/settings</loc></url>
</urlset>
"""


@pytest.fixture
def store(tmp_path):
    conn = open_store(tmp_path / "coverage.db")
    yield conn
    conn.close()


def test_parse_openapi_endpoints():
    entries = parse_openapi_endpoints(_OPENAPI, "editor-api")
    keys = [(e.method, e.key) for e in entries]
    assert keys == [
        ("GET", "/orders"),
        ("POST", "/orders"),
        ("GET", "/orders/{orderId}"),
        ("DELETE", "/orders/{orderId}"),
        ("POST", "/webhooks/stripe"),
    ]
    get_order = next(e for e in entries if e.details.get("operationId") == "getOrder")
    assert get_order.details == {"operationId": "getOrder", "summary": "Fetch one order"}
    assert all(e.api == "editor-api" and e.kind == "endpoint" for e in entries)


def test_parse_openapi_rejects_non_object():
    with pytest.raises(ManifestError, match="not a mapping"):
        parse_openapi_endpoints(["nope"], "a")


# ── the document arrives as JSON or as YAML ─────────────────────────────────

_OPENAPI_YAML = """
openapi: 3.1.0
paths:
  /orders:
    get:
      operationId: listOrders
      tags: [orders]
    post:
      operationId: createOrder
  /orders/{orderId}:
    get:
      operationId: getOrder
      summary: Fetch one order
"""


def test_parse_manifest_document_reads_json():
    assert parse_manifest_document(json.dumps(_OPENAPI), "editor-api") == _OPENAPI


def test_parse_manifest_document_reads_yaml():
    """`load_text` has always accepted a local path, and a spec committed to a
    repo is usually .yaml — so a JSON-only parser rejected the very files the
    config invited you to point at."""
    doc = parse_manifest_document(_OPENAPI_YAML, "editor-api")
    assert sorted(doc["paths"]) == ["/orders", "/orders/{orderId}"]

    entries = parse_openapi_endpoints(doc, "editor-api")
    assert sorted((e.method, e.key) for e in entries) == [
        ("GET", "/orders"),
        ("GET", "/orders/{orderId}"),
        ("POST", "/orders"),
    ]


def test_parse_manifest_document_error_names_both_formats():
    """The message is what a user acts on: one naming only JSON sends someone
    converting a file that was already fine."""
    with pytest.raises(ManifestError, match="not valid JSON or YAML"):
        parse_manifest_document("{unclosed: [", "editor-api")


def test_refresh_declares_endpoints_from_a_yaml_spec(tmp_path):
    """End to end through the path a project actually configures: a relative
    `coverage.apis[].openapi` pointing at a committed .yaml file."""
    spec = tmp_path / "openapi.yaml"
    spec.write_text(_OPENAPI_YAML, encoding="utf-8")

    conn = open_store(tmp_path / "map.db")
    try:
        summary = refresh_declared(conn, [{"name": "editor-api", "openapi": str(spec)}])
        assert summary["apis"][0]["declared"] == 3
    finally:
        conn.close()


def test_parse_sitemap_routes_namespaced_and_normalized():
    entries = parse_sitemap_routes(_SITEMAP)
    assert [e.key for e in entries] == ["/orders/:id", "/settings"]
    assert all(e.kind == "route" and e.api == "" for e in entries)


def test_parse_sitemap_invalid_xml():
    with pytest.raises(ManifestError, match="invalid sitemap XML"):
        parse_sitemap_routes("<urlset><loc>broken")


def test_refresh_from_local_files_is_idempotent(store, tmp_path):
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(_OPENAPI), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(_SITEMAP, encoding="utf-8")
    apis = [{"name": "editor-api", "openapi": str(spec)}]

    summary = refresh_declared(store, apis, str(sitemap), updated_at="t1")
    assert summary["apis"][0]["declared"] == 5
    assert summary["sitemap"]["declared"] == 2
    first = dump_canonical(store)

    summary2 = refresh_declared(store, apis, str(sitemap), updated_at="t2")
    assert summary2["apis"][0]["removed"] == 0
    assert dump_canonical(store) == first


def test_refresh_missing_file_raises(store, tmp_path):
    with pytest.raises(ManifestError, match="failed to read"):
        refresh_declared(store, [{"name": "a", "openapi": str(tmp_path / "absent.json")}])


def test_relink_moves_observed_coverage_onto_declared(store, tmp_path):
    # Merge happened BEFORE any refresh: observed endpoint stored under its
    # heuristic pattern, with raw samples kept as evidence.
    replace_scenario_contribution(
        store,
        "order-flow",
        [
            Contribution(
                kind="endpoint",
                key="/orders/:id",
                api="editor-api",
                method="GET",
                evidence={"samples": ["/orders/12"], "count": 3},
            ),
            Contribution(
                kind="endpoint",
                key="/totally/unknown",
                api="editor-api",
                method="GET",
                evidence={"samples": ["/totally/unknown"], "count": 1},
            ),
        ],
        recorded_at="t0",
    )
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(_OPENAPI), encoding="utf-8")

    summary = refresh_declared(store, [{"name": "editor-api", "openapi": str(spec)}], updated_at="t1")
    assert summary["relinked"] == 1

    dump = dump_canonical(store)
    declared_node = next(n for n in dump["nodes"] if n["key"] == "/orders/{orderId}" and n["method"] == "GET")
    assert [s["scenario_id"] for s in declared_node["scenarios"]] == ["order-flow"]
    # The emptied observed node is kept but uncovered; the unmatched one keeps its coverage.
    observed = next(n for n in dump["nodes"] if n["key"] == "/orders/:id")
    assert observed["scenarios"] == []
    unknown = next(n for n in dump["nodes"] if n["key"] == "/totally/unknown")
    assert len(unknown["scenarios"]) == 1


def test_refresh_then_merge_equals_merge_then_refresh(store, tmp_path):
    """Order independence: the converged map is identical either way."""
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(_OPENAPI), encoding="utf-8")
    apis = [{"name": "editor-api", "openapi": str(spec)}]

    def contribute(conn):
        # What a merge produces when declared templates are already known:
        # the path matches a template, so coverage attaches to the declared key.
        # When they are NOT known yet, merge stores the heuristic pattern and
        # the re-link pass moves it later.
        replace_scenario_contribution(
            conn,
            "order-flow",
            [
                Contribution(
                    kind="endpoint",
                    key="/orders/{orderId}",
                    api="editor-api",
                    method="GET",
                    evidence={"samples": ["/orders/12"], "count": 3},
                )
            ],
            recorded_at="t0",
        )

    def contribute_pre_refresh(conn):
        replace_scenario_contribution(
            conn,
            "order-flow",
            [
                Contribution(
                    kind="endpoint",
                    key="/orders/:id",
                    api="editor-api",
                    method="GET",
                    evidence={"samples": ["/orders/12"], "count": 3},
                )
            ],
            recorded_at="t0",
        )

    refresh_declared(store, apis, updated_at="t1")
    contribute(store)
    refresh_declared(store, apis, updated_at="t2")
    a = dump_canonical(store)

    conn2 = open_store(tmp_path / "other.db")
    contribute_pre_refresh(conn2)
    refresh_declared(conn2, apis, updated_at="t1")
    b = dump_canonical(conn2)
    conn2.close()

    # Same covered nodes either way; the only difference is the empty
    # observed placeholder node left behind by the re-link path.
    covered_a = [n for n in a["nodes"] if n["scenarios"] or n["declared"]]
    covered_b = [n for n in b["nodes"] if n["scenarios"] or n["declared"]]
    assert covered_a == covered_b
