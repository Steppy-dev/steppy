"""Tour and step ids.

Step ids travel in every ``tour-step`` event an app forwards to analytics, so
they must survive edits: they are never positional. A targeted step is named
after its test-id (stable as long as the element is); a centred step after its
heading. Repeats get ``-2``, ``-3``… in order.
"""

from __future__ import annotations

import re
import unicodedata

from ..tour_spec import STEP_ID_PATTERN
from .model import TourStepPlan

_MAX_ID = 128


def slugify_heading(title: str) -> str:
    """``"7b. Ouvrir la fiche"`` → ``"7b-ouvrir-la-fiche"`` (ASCII, ≤ 64 chars)."""
    ascii_text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:64].strip("-")
    return slug or "step"


def assign_step_ids(steps: list[TourStepPlan]) -> None:
    """Set ``step_id`` on every plan, deduplicating in order against every id so far."""
    used: set[str] = set()
    for step in steps:
        base = step.target or slugify_heading(step.step_title)
        if not re.fullmatch(STEP_ID_PATTERN, base):
            base = slugify_heading(base)
        candidate, n = base, 1
        while candidate in used:
            n += 1
            suffix = f"-{n}"
            candidate = base[: _MAX_ID - len(suffix)] + suffix
        used.add(candidate)
        step.step_id = candidate
