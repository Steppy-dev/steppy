"""Coverage store: idempotent upsert, replace semantics, triage persistence.

Pins the Task-2 contract: running one scenario UPSERTS into the map (keyed
(node, scenario_id)); re-running replaces that scenario's prior contributions
instead of accumulating stale ones; manual triage survives merges and
manifest refreshes.
"""

import pytest

from steppy.coverage.store import (
    Contribution,
    DeclaredEntry,
    StoreError,
    dump_canonical,
    ensure_node,
    list_triage,
    observed_endpoint_nodes,
    open_store,
    replace_declared_source,
    replace_scenario_contribution,
    repoint_node_coverage,
    set_triage,
)


@pytest.fixture
def store(tmp_path):
    conn = open_store(tmp_path / ".steppy" / "coverage.db")
    yield conn
    conn.close()


def _contribs():
    return [
        Contribution(kind="route", key="/orders/:id", api="editor", evidence={"samples": ["/orders/12"], "count": 2}),
        Contribution(
            kind="endpoint",
            key="/api/orders/{id}",
            api="editor-api",
            method="GET",
            evidence={"samples": ["/api/orders/12"], "count": 1},
        ),
        Contribution(kind="module", key="src/app/orders/orders.component.ts", evidence={"count": 1}),
    ]


def test_merge_twice_is_idempotent(store):
    replace_scenario_contribution(store, "order-flow", _contribs(), asserts_count=3, run_id="r1", recorded_at="t1")
    first = dump_canonical(store)
    replace_scenario_contribution(store, "order-flow", _contribs(), asserts_count=3, run_id="r2", recorded_at="t2")
    assert dump_canonical(store) == first


def test_remerge_replaces_stale_contributions(store):
    replace_scenario_contribution(store, "order-flow", _contribs(), recorded_at="t1")
    # Second run only touches the route — endpoint/module rows must disappear.
    replace_scenario_contribution(
        store,
        "order-flow",
        [Contribution(kind="route", key="/orders/:id", api="editor", evidence={"count": 1})],
        recorded_at="t2",
    )
    dump = dump_canonical(store)
    covered = [n for n in dump["nodes"] if n["scenarios"]]
    assert [(n["kind"], n["key"]) for n in covered] == [("route", "/orders/:id")]
    # Stale nodes are kept (they may carry triage/declared rows), just uncovered.
    assert len(dump["nodes"]) == 3


def test_two_scenarios_share_one_node(store):
    replace_scenario_contribution(
        store,
        "scn-a",
        [Contribution(kind="endpoint", key="/api/users/{id}", api="a", method="GET")],
    )
    replace_scenario_contribution(
        store,
        "scn-b",
        [Contribution(kind="endpoint", key="/api/users/{id}", api="a", method="GET")],
    )
    dump = dump_canonical(store)
    assert len(dump["nodes"]) == 1
    assert [s["scenario_id"] for s in dump["nodes"][0]["scenarios"]] == ["scn-a", "scn-b"]


def test_empty_string_identity_does_not_duplicate(store):
    # SQLite UNIQUE treats NULLs as distinct; '' identities must collapse.
    id1 = ensure_node(store, "route", "/home")
    id2 = ensure_node(store, "route", "/home")
    assert id1 == id2


def test_duplicate_contribution_raises(store):
    dup = [Contribution(kind="route", key="/x"), Contribution(kind="route", key="/x")]
    with pytest.raises(StoreError, match="duplicate contribution"):
        replace_scenario_contribution(store, "scn", dup)


def test_declared_replacement_keeps_nodes_and_triage(store):
    entries = [
        DeclaredEntry(kind="endpoint", key="/api/orders", api="editor-api", method="POST"),
        DeclaredEntry(kind="endpoint", key="/api/webhooks/stripe", api="editor-api", method="POST"),
    ]
    replace_declared_source(store, "openapi:editor-api", entries, updated_at="t1")
    set_triage(
        store,
        kind="endpoint",
        key="/api/webhooks/stripe",
        api="editor-api",
        method="POST",
        flag="non_ui",
        note="payment callback",
        updated_at="t1",
    )

    # Manifest shrinks: webhook vanishes from the spec.
    result = replace_declared_source(store, "openapi:editor-api", entries[:1], updated_at="t2")
    assert result["removed"] == 1

    dump = dump_canonical(store)
    webhook = next(n for n in dump["nodes"] if n["key"] == "/api/webhooks/stripe")
    assert webhook["declared"] == []  # declared row gone
    assert webhook["triage"]["flag"] == "non_ui"  # node + triage survived


def test_triage_survives_merge_and_refresh(store):
    set_triage(
        store,
        kind="endpoint",
        key="/api/jobs/cron",
        api="a",
        method="POST",
        flag="non_ui",
        note="cron",
        updated_at="t0",
    )
    replace_scenario_contribution(
        store,
        "scn",
        [Contribution(kind="endpoint", key="/api/jobs/cron", api="a", method="POST")],
    )
    replace_declared_source(
        store,
        "openapi:a",
        [DeclaredEntry(kind="endpoint", key="/api/jobs/cron", api="a", method="POST")],
    )
    assert list_triage(store) == [
        {"kind": "endpoint", "api": "a", "method": "POST", "key": "/api/jobs/cron", "flag": "non_ui", "note": "cron"}
    ]


def test_triage_creates_missing_node(store):
    set_triage(store, kind="endpoint", key="/api/webhooks/einvoice", api="a", method="POST", flag="non_ui")
    assert len(list_triage(store, flag="non_ui")) == 1
    # Later refresh reuses the same node identity — still one node.
    replace_declared_source(
        store,
        "openapi:a",
        [DeclaredEntry(kind="endpoint", key="/api/webhooks/einvoice", api="a", method="POST")],
    )
    assert len(dump_canonical(store)["nodes"]) == 1


def test_invalid_inputs_rejected(store):
    with pytest.raises(StoreError, match="invalid node kind"):
        ensure_node(store, "widget", "/x")
    with pytest.raises(StoreError, match="invalid triage flag"):
        set_triage(store, kind="route", key="/x", flag="maybe")
    with pytest.raises(StoreError, match="non-empty"):
        replace_scenario_contribution(store, "", [])


def test_schema_version_mismatch_raises(tmp_path):
    db = tmp_path / "cov.db"
    conn = open_store(db)
    conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.close()
    with pytest.raises(StoreError, match="schema version 999"):
        open_store(db)


def test_dump_survives_reopen(tmp_path):
    db = tmp_path / "cov.db"
    conn = open_store(db)
    replace_scenario_contribution(conn, "scn", _contribs(), recorded_at="t1")
    before = dump_canonical(conn)
    conn.close()
    conn = open_store(db)
    assert dump_canonical(conn) == before
    conn.close()


def test_repoint_node_coverage(store):
    # Observed node gets re-linked onto a declared node; higher count wins on conflict.
    obs = ensure_node(store, "endpoint", "/api/orders/:id", api="a", method="GET")
    decl = ensure_node(store, "endpoint", "/api/orders/{orderId}", api="a", method="GET")
    replace_scenario_contribution(
        store,
        "scn-a",
        [
            Contribution(
                kind="endpoint",
                key="/api/orders/:id",
                api="a",
                method="GET",
                evidence={"samples": ["/api/orders/5"], "count": 7},
            )
        ],
    )
    replace_scenario_contribution(
        store,
        "scn-b",
        [
            Contribution(
                kind="endpoint",
                key="/api/orders/{orderId}",
                api="a",
                method="GET",
                evidence={"samples": ["/api/orders/9"], "count": 2},
            ),
            Contribution(
                kind="endpoint",
                key="/api/orders/:id",
                api="a",
                method="GET",
                evidence={"samples": ["/api/orders/5"], "count": 1},
            ),
        ],
    )
    moved = repoint_node_coverage(store, obs, decl)
    assert moved == 1  # scn-a moved; scn-b conflict kept the higher-count dst row
    dump = dump_canonical(store)
    observed = next(n for n in dump["nodes"] if n["key"] == "/api/orders/:id")
    declared = next(n for n in dump["nodes"] if n["key"] == "/api/orders/{orderId}")
    assert observed["scenarios"] == []
    assert {s["scenario_id"] for s in declared["scenarios"]} == {"scn-a", "scn-b"}
    assert next(s for s in declared["scenarios"] if s["scenario_id"] == "scn-b")["evidence"]["count"] == 2


def test_observed_endpoint_nodes_lists_relink_candidates(store):
    replace_scenario_contribution(
        store,
        "scn",
        [
            Contribution(
                kind="endpoint",
                key="/api/items/:id",
                api="a",
                method="GET",
                evidence={"samples": ["/api/items/3", "/api/items/4"], "count": 2},
            ),
            Contribution(kind="route", key="/items/:id", api="editor"),
        ],
    )
    replace_declared_source(
        store,
        "openapi:a",
        [DeclaredEntry(kind="endpoint", key="/api/declared", api="a", method="GET")],
    )
    candidates = observed_endpoint_nodes(store)
    assert len(candidates) == 1
    assert candidates[0]["key"] == "/api/items/:id"
    assert candidates[0]["samples"] == ["/api/items/3", "/api/items/4"]
