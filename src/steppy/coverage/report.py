"""Render the gap report: deterministic JSON, a shields badge, a human summary."""

from __future__ import annotations

import json

_MAX_ROWS = 50

#: shields.io endpoint colours, coarsest-first. Deliberately the palette a
#: README already uses for test/coverage badges, so a Steppy badge sits in a row
#: of them without looking foreign.
_BADGE_COLORS = ((50, "red"), (70, "orange"), (85, "yellow"), (95, "green"))
_BADGE_COLOR_BEST = "brightgreen"
_BADGE_COLOR_UNKNOWN = "lightgrey"

DEFAULT_BADGE_LABEL = "e2e surface"


def gaps_to_json(gaps: dict) -> str:
    """Machine-readable report: sorted keys, no wall clock — diffable."""
    return json.dumps(gaps, indent=2, sort_keys=True)


def badge_color(percent: int) -> str:
    """Shields colour for a percentage."""
    for ceiling, color in _BADGE_COLORS:
        if percent < ceiling:
            return color
    return _BADGE_COLOR_BEST


def badge_payload(gaps: dict, *, surface: str = "all", label: str = DEFAULT_BADGE_LABEL) -> dict:
    """A shields.io **endpoint** object for the e2e surface ratio.

    An endpoint JSON rather than a generated SVG: it inherits whatever style the
    rest of the README's badge row uses, it is cacheable by shields, and no image
    rendering lands in this repo. Consumed as
    ``https://img.shields.io/endpoint?url=<raw-url-to-this-file>``.

    Nothing declared ⇒ ``"n/a"`` in grey. A badge reading ``0%`` when the truth is
    "no OpenAPI surface is configured" is worse than one admitting it does not
    know, because the first invites someone to go fix a number that is not broken.
    """
    ratio = (gaps.get("e2e_surface") or {}).get(surface) or {}
    percent = ratio.get("percent")
    if percent is None:
        return {"schemaVersion": 1, "label": label, "message": "n/a", "color": _BADGE_COLOR_UNKNOWN}
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{percent}%",
        "color": badge_color(percent),
    }


def badge_to_json(gaps: dict, *, surface: str = "all", label: str = DEFAULT_BADGE_LABEL) -> str:
    """:func:`badge_payload` as deterministic JSON text."""
    return json.dumps(badge_payload(gaps, surface=surface, label=label), indent=2, sort_keys=True) + "\n"


def _print_bucket_table(console, title: str, rows: list[dict], columns: list[str]) -> None:
    from rich.table import Table

    table = Table(title=f"{title} ({len(rows)})", title_justify="left")
    for col in columns:
        table.add_column(col)
    for row in rows[:_MAX_ROWS]:
        table.add_row(*(str(row.get(col, "")) for col in columns))
    console.print(table)
    if len(rows) > _MAX_ROWS:
        console.print(f"[dim]… and {len(rows) - _MAX_ROWS} more (see --json output)[/dim]")


def _count_sources(entries: list[dict]) -> list[tuple[str, int]]:
    """``[(source, count)]`` sorted for stable output."""
    tally: dict[str, int] = {}
    for entry in entries:
        source = entry.get("triage_source", "")
        tally[source] = tally.get(source, 0) + 1
    return sorted(tally.items())


def _print_surface(gaps: dict, console) -> None:
    """The one line the badge is derived from, so the two can never disagree."""
    ratio = (gaps.get("e2e_surface") or {}).get("all") or {}
    percent = ratio.get("percent")
    if percent is None:
        console.print(
            "[bold]E2E surface[/bold]: not measured — nothing declared. "
            "Configure [bold]coverage.apis[/bold] (and optionally [bold]coverage.sitemap[/bold]), "
            "then run [bold]steppy coverage refresh[/bold].\n"
        )
        return
    console.print(
        f"[bold]E2E surface[/bold]: {percent}% "
        f"({ratio['tested']}/{ratio['declared']} declared endpoints + routes a UI test could reach)\n"
    )


def print_summary(gaps: dict, console) -> None:
    """Short human summary: counts first, then the bucket lists."""
    counts = gaps["counts"]
    ep = counts["endpoints"]
    console.print(
        f"[bold]Endpoints[/bold]: {ep['declared']} declared | "
        f"{ep['tested']} tested ({ep['tested_strong']} strong) | "
        f"[red]{ep['declared_unreached']} declared-unreached[/red] "
        f"({ep['declared_unreached_actionable']} actionable, rest non_ui) | "
        f"{ep['discovered_uncovered']} discovered-uncovered | "
        f"{ep['unclassified']} unclassified"
    )
    rt = counts["routes"]
    console.print(
        f"[bold]Routes[/bold]:    {rt['declared']} declared | {rt['tested']} tested | "
        f"[red]{rt['declared_unreached']} declared-unreached[/red] | "
        f"{rt['discovered_uncovered']} discovered-uncovered"
    )
    console.print(
        f"[bold]Modules[/bold]:   {counts['modules']['covered']} covered | scenarios in map: {counts['scenarios']}"
    )
    _print_surface(gaps, console)

    unreached = gaps["endpoints"]["declared_unreached"]
    actionable = [e for e in unreached if e["triage"] != "non_ui"]
    if actionable:
        _print_bucket_table(
            console,
            "Declared but unreached (actionable)",
            actionable,
            ["api", "method", "key", "triage", "note"],
        )
    non_ui = [e for e in unreached if e["triage"] == "non_ui"]
    if non_ui:
        by_source = _count_sources(non_ui)
        detail = ", ".join(f"{n} from {src or 'unspecified'}" for src, n in by_source)
        console.print(f"[dim]{len(non_ui)} declared-unreached triaged non_ui, hidden ({detail})[/dim]")

    if gaps["endpoints"]["discovered_uncovered"]:
        _print_bucket_table(
            console,
            "Discovered but weakly covered (endpoints)",
            gaps["endpoints"]["discovered_uncovered"],
            ["api", "method", "key", "scenarios"],
        )
    if gaps["routes"]["declared_unreached"]:
        _print_bucket_table(
            console,
            "Declared but unreached (routes)",
            gaps["routes"]["declared_unreached"],
            ["key", "triage", "note"],
        )
    if gaps["routes"]["discovered_uncovered"]:
        _print_bucket_table(
            console,
            "Discovered but weakly covered (routes)",
            gaps["routes"]["discovered_uncovered"],
            ["key", "scenarios"],
        )
    if gaps["endpoints"]["unclassified"]:
        console.print(
            f"[yellow]{len(gaps['endpoints']['unclassified'])} unclassified API request "
            "pattern(s)[/yellow] — requests matching no configured coverage.apis base_urls; "
            "check base_urls (include internal_url origins for parallel runs)."
        )
    if gaps["endpoints"]["discovered_tested"]:
        console.print(
            f"[yellow]{len(gaps['endpoints']['discovered_tested'])} tested endpoint(s) missing "
            "from the manifest[/yellow] — stale spec or base-URL prefix mismatch; "
            "run `steppy coverage refresh`."
        )

    declared_total = ep["declared"]
    if declared_total and ep["tested"] == 0:
        console.print(
            "[yellow]No declared endpoint was ever matched by an observed request — "
            "if scenarios did run, coverage.apis base_urls probably don't mirror the "
            "OpenAPI servers[].url prefix.[/yellow]"
        )
