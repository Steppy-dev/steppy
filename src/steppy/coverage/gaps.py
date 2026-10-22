"""Gap computation: pure bucket math over the coverage map.

Consumes ``store.read_map()`` output, produces the bucket structure the
report renders. No IO, no clock — same map in, same buckets out.

Buckets per kind (endpoints are the primary surface, routes when a sitemap
is configured):

- ``tested`` — declared AND exercised by at least one scenario, with a
  ``strength``: *strong* when some covering scenario both ran with asserts
  enabled and contains at least one assert step; *weak* otherwise.
- ``declared_unreached`` — in the manifest, never hit by any scenario. The
  key bucket. Each entry carries its triage flag so non-UI endpoints
  (webhooks, cron callbacks) can be discounted, plus ``triage_source``
  saying whether that came from ``config.yaml`` or the store's own table.
- ``discovered_uncovered`` — seen in a trail but only weakly covered.
- ``discovered_tested`` — exercised + strongly asserted but absent from the
  manifest (stale spec, or a base-URL misconfiguration).
- ``unclassified`` (endpoints only) — requests that matched no configured
  API base URL.

Modules have no declared source in v1 — they are reported as a covered
inventory only.

``e2e_surface`` sits beside ``counts`` and answers the badge's question for each
surface (see :func:`surface_ratio`). It is derived, not another bucket: nothing
here decides *which* nodes are non-UI — that is the read-time overlay in
:mod:`steppy.coverage.triage`, applied to the map before it arrives.
"""

from __future__ import annotations

#: Surfaces the e2e ratio can be computed over. ``all`` is the default because
#: the declared surface is "routes and API endpoints" — either alone silently
#: drops half of what a project declared.
SURFACES = ("all", "endpoints", "routes")


def surface_ratio(counts: dict, surface: str = "all") -> dict:
    """``tested / declared`` over one surface, with non_ui removed from both.

    The formula as written in the docs — ``tested / (declared - non_ui)`` — can
    exceed 1, because a *tested* endpoint flagged ``non_ui`` stays in the
    numerator while leaving the denominator. Subtracting from both keeps the ratio
    in range and keeps the question honest: *of the surface a UI test could reach,
    how much does the suite reach?*

    ``total == 0`` returns ``ratio``/``percent`` of ``None`` rather than 0, so a
    project with nothing declared reads as "not measured" instead of "0% tested".
    """
    kinds = ("endpoints", "routes") if surface == "all" else (surface,)
    tested = 0
    total = 0
    for kind in kinds:
        section = counts.get(kind) or {}
        tested += section.get("tested", 0) - section.get("tested_non_ui", 0)
        total += section.get("declared", 0) - section.get("declared_non_ui", 0)
    if total <= 0:
        return {"surface": surface, "tested": max(tested, 0), "declared": max(total, 0), "ratio": None, "percent": None}
    ratio = tested / total
    return {
        "surface": surface,
        "tested": tested,
        "declared": total,
        "ratio": round(ratio, 4),
        "percent": int(round(ratio * 100)),
    }


def _strength(node: dict, scenarios_meta: dict) -> str:
    """Strong iff some covering scenario has asserts enabled AND assert steps."""
    for cov in node["scenarios"]:
        meta = scenarios_meta.get(cov["scenario_id"], {})
        if cov["asserts_enabled"] and meta.get("asserts_count", 0) > 0:
            return "strong"
    return "weak"


def _covered_entry(node: dict, scenarios_meta: dict) -> dict:
    # ``triage`` rides on a *covered* entry too, which it historically did not.
    # Without it the e2e-surface ratio cannot subtract a non_ui endpoint that
    # happens to be tested, and the number could exceed 100%.
    triage = node.get("triage") or {}
    return {
        "api": node["api"],
        "method": node["method"],
        "key": node["key"],
        "scenarios": [c["scenario_id"] for c in node["scenarios"]],
        "strength": _strength(node, scenarios_meta),
        "triage": triage.get("flag", "unknown"),
        "triage_source": triage.get("source", ""),
        "note": triage.get("note", ""),
    }


def _unreached_entry(node: dict) -> dict:
    triage = node.get("triage") or {}
    return {
        "api": node["api"],
        "method": node["method"],
        "key": node["key"],
        "triage": triage.get("flag", "unknown"),
        # Which rule discounted this endpoint: "config" (a coverage.non_ui
        # pattern), "store" (a triage row), or "" (neither). A number nobody can
        # trace is a number nobody trusts.
        "triage_source": triage.get("source", ""),
        "note": triage.get("note", ""),
    }


def compute_gaps(map_view: dict) -> dict:
    """Bucket every node of the map. ``map_view`` comes from ``store.read_map``."""
    scenarios_meta = map_view["scenarios"]
    buckets: dict = {
        "endpoints": {
            "tested": [],
            "declared_unreached": [],
            "discovered_uncovered": [],
            "discovered_tested": [],
            "unclassified": [],
        },
        "routes": {
            "tested": [],
            "declared_unreached": [],
            "discovered_uncovered": [],
            "discovered_tested": [],
        },
        "modules": {"covered": []},
    }

    for node in map_view["nodes"]:
        declared = bool(node["declared"])
        covered = bool(node["scenarios"])
        if not declared and not covered:
            continue  # placeholder left by a re-link, or triage-only node

        if node["kind"] == "module":
            if covered:
                buckets["modules"]["covered"].append(
                    {
                        "key": node["key"],
                        "scenarios": [c["scenario_id"] for c in node["scenarios"]],
                    }
                )
            continue

        kind_bucket = buckets["endpoints" if node["kind"] == "endpoint" else "routes"]

        if node["kind"] == "endpoint" and covered and not declared and node["api"] == "":
            kind_bucket["unclassified"].append(_covered_entry(node, scenarios_meta))
            continue

        if declared and covered:
            kind_bucket["tested"].append(_covered_entry(node, scenarios_meta))
        elif declared:
            kind_bucket["declared_unreached"].append(_unreached_entry(node))
        else:
            entry = _covered_entry(node, scenarios_meta)
            if entry["strength"] == "weak":
                kind_bucket["discovered_uncovered"].append(entry)
            else:
                kind_bucket["discovered_tested"].append(entry)

    counts: dict = {"scenarios": len(scenarios_meta)}
    for kind in ("endpoints", "routes"):
        section = buckets[kind]
        tested = section["tested"]
        unreached = section["declared_unreached"]
        tested_non_ui = sum(1 for e in tested if e["triage"] == "non_ui")
        unreached_non_ui = sum(1 for e in unreached if e["triage"] == "non_ui")
        counts[kind] = {
            "declared": len(tested) + len(unreached),
            "tested": len(tested),
            "tested_strong": sum(1 for e in tested if e["strength"] == "strong"),
            "declared_unreached": len(unreached),
            "declared_unreached_actionable": len(unreached) - unreached_non_ui,
            "discovered_uncovered": len(section["discovered_uncovered"]),
            "discovered_tested": len(section["discovered_tested"]),
            # non_ui across tested UNION unreached. Counting it only among the
            # unreached (which ``declared_unreached_actionable`` does, and which
            # is right for its own purpose) would leave a tested-but-non_ui
            # endpoint in the numerator and out of the denominator.
            "tested_non_ui": tested_non_ui,
            "declared_non_ui": tested_non_ui + unreached_non_ui,
        }
    counts["endpoints"]["unclassified"] = len(buckets["endpoints"]["unclassified"])
    counts["modules"] = {"covered": len(buckets["modules"]["covered"])}
    buckets["counts"] = counts
    buckets["e2e_surface"] = {surface: surface_ratio(counts, surface) for surface in SURFACES}
    return buckets
