"""The report's link fragments, the same in the local report and in Steppy Cloud (spec V3).

- ``#<scenario>`` a scenario;
- ``#<scenario>/<locale>/<layout>`` one of its variants;
- ``#<scenario>/<locale>/<layout>/step-<n>`` one step, ``n`` the scenario's own step
  number (sparse, never renumbered).

A variant with no locale or no layout writes ``-`` for that segment. Each segment is
percent-encoded where a URL needs it, exactly as JavaScript's ``encodeURI`` does it
(what Steppy Cloud's router writes), plus ``/`` itself, which no real segment holds.

Two segments are never a link. That is the room the report keeps for its own
anchors: ``#cause/<fingerprint>``, and ``ui/…`` for the ids of its controls.

The encoded fragment is also the element's ``id``: a browser looks the fragment up as
written before it decodes it, so the link works without JavaScript whatever the
segments hold.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

#: A missing locale or layout.
NONE = "-"

#: What ``encodeURI`` leaves as it is beside letters, digits and ``_.-~``, less the
#: ``/`` that separates the segments.
_SAFE = ";,?:@&=+$!*'()#"


def segment(text: str) -> str:
    """One segment of a fragment, percent-encoded."""
    return quote(text, safe=_SAFE, errors="replace")


def scenario_fragment(slug: str) -> str:
    return segment(slug)


def variant_fragment(slug: str, locale: Optional[str], layout: Optional[str]) -> str:
    return "/".join((segment(slug), segment(locale or NONE), segment(layout or NONE)))


def step_fragment(slug: str, locale: Optional[str], layout: Optional[str], step: int) -> str:
    return f"{variant_fragment(slug, locale, layout)}/step-{step}"


def cause_fragment(fingerprint: str) -> str:
    """The anchor of a failure cause: two segments, so no scenario's link can be it."""
    return f"cause/{segment(fingerprint)}"
