"""One wording for counts, lists, places and verdicts, shared with Steppy Cloud.

The run page in the cloud says "Fails on mobile in fr and de" and "2 of 7 scenarios
failing, 2 causes"; the local report says the same sentences for the same run. The
rules live here, as pure functions, so a sentence is never assembled in a template.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional, Sequence

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def num(n: int) -> str:
    """``1,875``."""
    return f"{n:,}"


def counted(noun: str, n: int) -> str:
    """``1 variant``, ``1,875 variants``."""
    return f"{num(n)} {noun}{'' if n == 1 else 's'}"


def join_list(items: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def few_list(items: Sequence[str], most: int = 3) -> str:
    """``fr, de and es``; past *most*, ``fr, de, es and 8 more``."""
    if len(items) <= most:
        return join_list(items)
    return f"{', '.join(items[:most])} and {num(len(items) - most)} more"


def _covers(chosen: Sequence[str], every: Sequence[str]) -> bool:
    return len(every) > 1 and len(chosen) == len(every) and set(every) <= set(chosen)


def locale_list(chosen: Sequence[str], every: Sequence[str]) -> str:
    """Locales in a sentence: ``all 15 locales``, ``fr, de and es``."""
    return f"all {num(len(every))} locales" if _covers(chosen, every) else few_list(chosen)


def locale_share(chosen: Sequence[str], every: Sequence[str]) -> str:
    """Locales as a share of the run: ``fr``, ``11 of 15 locales (fr, de, es and 8 more)``."""
    if len(chosen) == 1:
        return chosen[0]
    if _covers(chosen, every):
        return f"all {num(len(every))} locales"
    return f"{num(len(chosen))} of {num(len(every))} locales ({few_list(chosen)})"


def in_order(values: Iterable[str], order: Sequence[str]) -> list[str]:
    """*values* in the run's declared order; what it does not name goes last, alphabetically."""
    rank = {value: i for i, value in enumerate(order)}
    return sorted(set(values), key=lambda v: (rank.get(v, len(order)), v.casefold(), v))


def group_by_locales(
    by_layout: dict[str, list[str]], layouts: Sequence[str], locales: Sequence[str]
) -> list[tuple[list[str], list[str]]]:
    """``[(layouts, locales)]``: layouts that share exactly the same locales, merged, in run order."""
    groups: list[tuple[list[str], list[str]]] = []
    for layout in in_order(by_layout, layouts):
        chosen = in_order(by_layout[layout], locales)
        if not chosen:
            continue
        same = next((g for g in groups if g[1] == chosen), None)
        if same:
            same[0].append(layout)
        else:
            groups.append(([layout], chosen))
    return groups


def where_text(pairs: Sequence[tuple[str, str]], locales: Sequence[str], layouts: Sequence[str]) -> str:
    """Where a scenario fails or is flaky: ``on mobile in fr and de``, ``in every variant``.

    *pairs* are ``(locale, layout)`` names, ``""`` for a run without that dimension.
    """
    if not pairs:
        return ""
    if not layouts:
        chosen = [v for v in in_order((locale for locale, _ in pairs), locales) if v]
        return f"in {locale_list(chosen, locales)}" if chosen else ""
    by_layout: dict[str, list[str]] = {}
    for locale, layout in pairs:
        by_layout.setdefault(layout, []).append(locale)
    groups = group_by_locales(by_layout, layouts, locales)
    if len(groups) == 1 and len(groups[0][0]) == len(layouts) and (not locales or len(groups[0][1]) == len(locales)):
        return "in every variant"
    parts = []
    for names, chosen in groups:
        chosen = [v for v in chosen if v]
        where = join_list(names)
        if not chosen:
            parts.append(f"on {where}")
        elif len(names) == 1:
            parts.append(f"on {where} in {locale_list(chosen, locales)}")
        else:
            parts.append(f"in {locale_list(chosen, locales)} on {where}")
    return "; ".join(parts)


def verdict_text(*, tests: int, scenarios: int, failing: int, causes: int) -> str:
    """The verdict, a sentence: ``2 of 42 scenarios failing, 2 causes``."""
    if not tests:
        return "No test outcomes recorded"
    if not failing:
        return f"All {num(scenarios)} scenarios passing"
    sentence = f"{num(failing)} of {num(scenarios)} scenarios failing"
    return f"{sentence}, {counted('cause', causes)}" if causes else sentence


def when_text(value: str) -> str:
    """``25 Nov 2026, 20:14 UTC`` from the run's own timestamp; the text itself when it is not one.

    Absolute on purpose: a report is a file, and "3 hours ago" would be false by the
    time anybody opens it. No ``strftime`` month name either, which follows the locale.
    """
    try:
        at = dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    zone = ""
    if at.tzinfo is not None:
        at = at.astimezone(dt.timezone.utc)
        zone = " UTC"
    return f"{at.day} {_MONTHS[at.month - 1]} {at.year}, {at.hour:02d}:{at.minute:02d}{zone}"


_GITHUB_RUN = re.compile(r"/actions/runs/(\d+)")
_GITLAB_JOB = re.compile(r"/-/jobs/(\d+)")
_GITLAB_PIPELINE = re.compile(r"/-/pipelines/(\d+)")


def ci_label(provider: str, url: Optional[str]) -> str:
    """``GitHub Actions run 4821``, ``GitLab job 4242``, or the provider's name."""
    for pattern, words in (
        (_GITHUB_RUN, "GitHub Actions run"),
        (_GITLAB_JOB, "GitLab job"),
        (_GITLAB_PIPELINE, "GitLab pipeline"),
    ):
        match = pattern.search(url or "")
        if match:
            return f"{words} {match.group(1)}"
    return {"github-actions": "GitHub Actions", "gitlab-ci": "GitLab CI"}.get(provider, provider or "CI run")


#: An address a page may link to: http or https, with a host, and nothing a browser
#: would strip or reinterpret (white space, a control character, a backslash).
_WEB_URL = re.compile(r"https?://[^\s\x00-\x20\x7f\\/?#]+[^\s\x00-\x20\x7f\\]*", re.IGNORECASE)


def web_url(value: Optional[str]) -> Optional[str]:
    """*value* when it may be a link, else ``None``: everything in a bundle is untrusted."""
    return value if isinstance(value, str) and _WEB_URL.fullmatch(value) else None
