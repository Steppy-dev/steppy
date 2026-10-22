"""Persistent coverage map store (sqlite, stdlib-only).

The store is the single source of truth for the coverage map:

- ``nodes`` — route patterns, source modules, API endpoints. Identity is
  ``(kind, api, method, key)``; ``api``/``method`` are ``''`` when not
  applicable — NOT NULL, because SQLite UNIQUE indexes treat NULLs as
  distinct and would silently allow duplicate nodes.
- ``declared`` — provenance rows from manifest sources (``openapi:<name>``,
  ``sitemap``). Replaced per-source on refresh; nodes are never deleted.
- ``scenario_coverage`` — which scenario exercised which node, with capped
  raw-sample evidence. Replaced per-scenario on merge: the whole prior
  contribution of a scenario is deleted and re-inserted in one transaction,
  so re-running a scenario never accumulates stale rows.
- ``triage`` — manual ``ui_testable | non_ui | unknown`` flags. Never
  touched by merge or refresh.
- ``scenarios`` — per-scenario metadata (assert count parsed from the
  scenario markdown at merge time).

Provenance (declared / discovered / tested) is DERIVED at read time, never
stored. All timestamps are supplied by callers — the store never reads the
wall clock — so merging identical inputs twice produces byte-identical rows.
``dump_canonical`` additionally strips all operational metadata (timestamps,
run ids) so idempotency can be asserted by direct comparison.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

VALID_KINDS = ("route", "module", "endpoint")
VALID_TRIAGE_FLAGS = ("ui_testable", "non_ui", "unknown")

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('route', 'module', 'endpoint')),
    api        TEXT NOT NULL DEFAULT '',
    method     TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, api, method, key)
);

CREATE TABLE IF NOT EXISTS declared (
    node_id    INTEGER NOT NULL REFERENCES nodes(id),
    source     TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (node_id, source)
);
CREATE INDEX IF NOT EXISTS idx_declared_source ON declared(source);

CREATE TABLE IF NOT EXISTS scenario_coverage (
    node_id         INTEGER NOT NULL REFERENCES nodes(id),
    scenario_id     TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '{}',
    asserts_enabled INTEGER NOT NULL DEFAULT 1,
    run_id          TEXT NOT NULL DEFAULT '',
    recorded_at     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (node_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS idx_sc_scenario ON scenario_coverage(scenario_id);

CREATE TABLE IF NOT EXISTS triage (
    node_id    INTEGER PRIMARY KEY REFERENCES nodes(id),
    flag       TEXT NOT NULL CHECK (flag IN ('ui_testable', 'non_ui', 'unknown')),
    note       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id      TEXT PRIMARY KEY,
    asserts_count    INTEGER NOT NULL DEFAULT 0,
    last_run_id      TEXT NOT NULL DEFAULT '',
    last_recorded_at TEXT NOT NULL DEFAULT ''
);
"""


class StoreError(RuntimeError):
    """Raised on schema-version mismatch or invalid store input."""


@dataclass(frozen=True)
class Contribution:
    """One (node, scenario) coverage fact produced by a merge.

    ``evidence`` is a JSON-serializable dict; the conventional shape is
    ``{"samples": [...], "count": N, "roles": [...], "guide_mode": bool,
    "outcome": "passed"|"failed"|"unknown"}`` with samples capped, sorted,
    and query-stripped.
    """

    kind: str
    key: str
    api: str = ""
    method: str = ""
    evidence: dict = field(default_factory=dict)
    asserts_enabled: bool = True

    def identity(self) -> tuple[str, str, str, str]:
        return (self.kind, self.api, self.method, self.key)


def open_store(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the coverage store at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # explicit BEGIN/COMMIT only
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    elif row["value"] != str(SCHEMA_VERSION):
        raise StoreError(
            f"coverage store schema version {row['value']} != supported {SCHEMA_VERSION}; "
            "delete the store file to recreate it (triage flags will be lost) or upgrade steppy"
        )


def ensure_node(
    conn: sqlite3.Connection,
    kind: str,
    key: str,
    api: str = "",
    method: str = "",
    created_at: str = "",
) -> int:
    """Insert the node if missing and return its id. Never updates existing rows."""
    if kind not in VALID_KINDS:
        raise StoreError(f"invalid node kind {kind!r} (expected one of {VALID_KINDS})")
    if not key:
        raise StoreError("node key must be non-empty")
    conn.execute(
        "INSERT OR IGNORE INTO nodes (kind, api, method, key, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, api, method, key, created_at),
    )
    row = conn.execute(
        "SELECT id FROM nodes WHERE kind = ? AND api = ? AND method = ? AND key = ?",
        (kind, api, method, key),
    ).fetchone()
    return int(row["id"])


def replace_scenario_contribution(
    conn: sqlite3.Connection,
    scenario_id: str,
    contributions: list[Contribution],
    *,
    asserts_count: int = 0,
    run_id: str = "",
    recorded_at: str = "",
) -> dict:
    """Atomically replace ``scenario_id``'s entire contribution to the map.

    Idempotent: same inputs -> same rows. Re-running with a smaller set drops
    the stale rows. Nodes referenced by prior contributions are kept (they may
    carry declared rows or triage flags).
    """
    if not scenario_id:
        raise StoreError("scenario_id must be non-empty")
    seen: set[tuple[str, str, str, str]] = set()
    for c in contributions:
        ident = c.identity()
        if ident in seen:
            raise StoreError(f"duplicate contribution for node {ident} in scenario {scenario_id!r}")
        seen.add(ident)
    ordered = sorted(contributions, key=Contribution.identity)

    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = []
        for c in ordered:
            node_id = ensure_node(conn, c.kind, c.key, c.api, c.method, created_at=recorded_at)
            rows.append(
                (
                    node_id,
                    scenario_id,
                    json.dumps(c.evidence, sort_keys=True),
                    1 if c.asserts_enabled else 0,
                    run_id,
                    recorded_at,
                )
            )
        conn.execute("DELETE FROM scenario_coverage WHERE scenario_id = ?", (scenario_id,))
        conn.executemany(
            "INSERT INTO scenario_coverage"
            " (node_id, scenario_id, evidence, asserts_enabled, run_id, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "INSERT INTO scenarios (scenario_id, asserts_count, last_run_id, last_recorded_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(scenario_id) DO UPDATE SET"
            " asserts_count = excluded.asserts_count,"
            " last_run_id = excluded.last_run_id,"
            " last_recorded_at = excluded.last_recorded_at",
            (scenario_id, asserts_count, run_id, recorded_at),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"scenario_id": scenario_id, "nodes": len(rows)}


@dataclass(frozen=True)
class DeclaredEntry:
    """One declared-surface item from a manifest source."""

    kind: str
    key: str
    api: str = ""
    method: str = ""
    details: dict = field(default_factory=dict)

    def identity(self) -> tuple[str, str, str, str]:
        return (self.kind, self.api, self.method, self.key)


def replace_declared_source(
    conn: sqlite3.Connection,
    source: str,
    entries: list[DeclaredEntry],
    *,
    updated_at: str = "",
) -> dict:
    """Atomically replace the declared rows contributed by ``source``.

    Declared rows for entries that vanished from the manifest are deleted;
    the nodes themselves (and any coverage/triage attached) are kept.
    """
    if not source:
        raise StoreError("declared source must be non-empty")
    seen: set[tuple[str, str, str, str]] = set()
    for e in entries:
        ident = e.identity()
        if ident in seen:
            raise StoreError(f"duplicate declared entry {ident} for source {source!r}")
        seen.add(ident)
    ordered = sorted(entries, key=DeclaredEntry.identity)

    conn.execute("BEGIN IMMEDIATE")
    try:
        node_ids: list[int] = []
        for e in ordered:
            node_id = ensure_node(conn, e.kind, e.key, e.api, e.method, created_at=updated_at)
            node_ids.append(node_id)
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            removed = conn.execute(
                f"SELECT COUNT(*) AS n FROM declared WHERE source = ? AND node_id NOT IN ({placeholders})",
                (source, *node_ids),
            ).fetchone()["n"]
            conn.execute(
                f"DELETE FROM declared WHERE source = ? AND node_id NOT IN ({placeholders})",
                (source, *node_ids),
            )
        else:
            removed = conn.execute("SELECT COUNT(*) AS n FROM declared WHERE source = ?", (source,)).fetchone()["n"]
            conn.execute("DELETE FROM declared WHERE source = ?", (source,))
        for e, node_id in zip(ordered, node_ids):
            conn.execute(
                "INSERT INTO declared (node_id, source, details, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(node_id, source) DO UPDATE SET"
                " details = excluded.details, updated_at = excluded.updated_at",
                (node_id, source, json.dumps(e.details, sort_keys=True), updated_at),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"source": source, "declared": len(node_ids), "removed": int(removed)}


def set_triage(
    conn: sqlite3.Connection,
    *,
    kind: str,
    key: str,
    api: str = "",
    method: str = "",
    flag: str,
    note: str = "",
    updated_at: str = "",
) -> int:
    """Set the persisted triage flag on a node (creating the node if absent).

    Creating on demand lets an operator pre-triage a known non-UI endpoint
    (webhook, cron callback) before any run or refresh has seen it; the same
    node identity is reused when the manifest later declares it.
    """
    if flag not in VALID_TRIAGE_FLAGS:
        raise StoreError(f"invalid triage flag {flag!r} (expected one of {VALID_TRIAGE_FLAGS})")
    conn.execute("BEGIN IMMEDIATE")
    try:
        node_id = ensure_node(conn, kind, key, api, method, created_at=updated_at)
        conn.execute(
            "INSERT INTO triage (node_id, flag, note, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(node_id) DO UPDATE SET"
            " flag = excluded.flag, note = excluded.note, updated_at = excluded.updated_at",
            (node_id, flag, note, updated_at),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return node_id


def list_triage(conn: sqlite3.Connection, flag: str | None = None) -> list[dict]:
    sql = "SELECT n.kind, n.api, n.method, n.key, t.flag, t.note FROM triage t JOIN nodes n ON n.id = t.node_id"
    params: tuple = ()
    if flag is not None:
        sql += " WHERE t.flag = ?"
        params = (flag,)
    sql += " ORDER BY n.kind, n.api, n.method, n.key"
    return [dict(row) for row in conn.execute(sql, params)]


def repoint_node_coverage(conn: sqlite3.Connection, src_node_id: int, dst_node_id: int) -> int:
    """Move scenario_coverage rows from ``src`` to ``dst`` (refresh re-link pass).

    On (dst, scenario) conflicts the row with the higher evidence ``count``
    wins; ties keep the existing dst row. Returns the number of rows moved.
    """
    if src_node_id == dst_node_id:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        moved = 0
        src_rows = conn.execute(
            "SELECT * FROM scenario_coverage WHERE node_id = ? ORDER BY scenario_id",
            (src_node_id,),
        ).fetchall()
        for row in src_rows:
            existing = conn.execute(
                "SELECT evidence FROM scenario_coverage WHERE node_id = ? AND scenario_id = ?",
                (dst_node_id, row["scenario_id"]),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "UPDATE scenario_coverage SET node_id = ? WHERE node_id = ? AND scenario_id = ?",
                    (dst_node_id, src_node_id, row["scenario_id"]),
                )
                moved += 1
            else:
                src_count = (json.loads(row["evidence"]) or {}).get("count", 0)
                dst_count = (json.loads(existing["evidence"]) or {}).get("count", 0)
                if src_count > dst_count:
                    conn.execute(
                        "DELETE FROM scenario_coverage WHERE node_id = ? AND scenario_id = ?",
                        (dst_node_id, row["scenario_id"]),
                    )
                    conn.execute(
                        "UPDATE scenario_coverage SET node_id = ? WHERE node_id = ? AND scenario_id = ?",
                        (dst_node_id, src_node_id, row["scenario_id"]),
                    )
                    moved += 1
                else:
                    conn.execute(
                        "DELETE FROM scenario_coverage WHERE node_id = ? AND scenario_id = ?",
                        (src_node_id, row["scenario_id"]),
                    )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return moved


def observed_endpoint_nodes(conn: sqlite3.Connection) -> list[dict]:
    """Endpoint nodes that have coverage but no declared row — re-link candidates.

    Each entry carries the union of evidence ``samples`` across scenarios so
    the caller can re-match raw paths against newly declared templates.
    """
    rows = conn.execute(
        "SELECT n.id, n.api, n.method, n.key FROM nodes n"
        " WHERE n.kind = 'endpoint'"
        " AND EXISTS (SELECT 1 FROM scenario_coverage sc WHERE sc.node_id = n.id)"
        " AND NOT EXISTS (SELECT 1 FROM declared d WHERE d.node_id = n.id)"
        " ORDER BY n.api, n.method, n.key"
    ).fetchall()
    result = []
    for row in rows:
        samples: set[str] = set()
        for cov in conn.execute("SELECT evidence FROM scenario_coverage WHERE node_id = ?", (row["id"],)):
            samples.update((json.loads(cov["evidence"]) or {}).get("samples", []))
        result.append(
            {
                "id": int(row["id"]),
                "api": row["api"],
                "method": row["method"],
                "key": row["key"],
                "samples": sorted(samples),
            }
        )
    return result


def read_map(conn: sqlite3.Connection) -> dict:
    """Deterministic full view of the map for gap computation and reporting.

    Operational metadata (timestamps, run ids) is excluded; everything is
    sorted, so two stores built from the same inputs read identically.
    """
    nodes = []
    for n in conn.execute("SELECT id, kind, api, method, key FROM nodes ORDER BY kind, api, method, key").fetchall():
        declared = [
            {"source": d["source"], "details": json.loads(d["details"])}
            for d in conn.execute(
                "SELECT source, details FROM declared WHERE node_id = ? ORDER BY source",
                (n["id"],),
            )
        ]
        scenarios = [
            {
                "scenario_id": sc["scenario_id"],
                "evidence": json.loads(sc["evidence"]),
                "asserts_enabled": bool(sc["asserts_enabled"]),
            }
            for sc in conn.execute(
                "SELECT scenario_id, evidence, asserts_enabled FROM scenario_coverage"
                " WHERE node_id = ? ORDER BY scenario_id",
                (n["id"],),
            )
        ]
        triage_row = conn.execute("SELECT flag, note FROM triage WHERE node_id = ?", (n["id"],)).fetchone()
        nodes.append(
            {
                "kind": n["kind"],
                "api": n["api"],
                "method": n["method"],
                "key": n["key"],
                "declared": declared,
                "scenarios": scenarios,
                "triage": dict(triage_row) if triage_row else None,
            }
        )
    scenario_meta = {
        row["scenario_id"]: {"asserts_count": int(row["asserts_count"])}
        for row in conn.execute("SELECT scenario_id, asserts_count FROM scenarios ORDER BY scenario_id")
    }
    return {"nodes": nodes, "scenarios": scenario_meta}


def dump_canonical(conn: sqlite3.Connection) -> dict:
    """Canonical store dump for idempotency comparison in tests."""
    return {"schema_version": SCHEMA_VERSION, **read_map(conn)}
