"""The in-app tour data contract, v1: vocabulary, grammars and limits.

Shared by the scenario models (annotation enums), the tours compiler, the
producer-side contract (:mod:`steppy.contracts.tours`) and — through
``tests/test_tour_contract_sync.py`` — the TypeScript player, whose
``packages/player/src/tour/contract.ts`` must hold the very same values. The
player is the security boundary (it validates every file it loads, bundled or
remote); the compiler must therefore never emit anything these patterns or
limits reject, or a tour would fail with ``invalid-data`` in production.

Patterns are written without backslashes so the TypeScript mirror can carry
them as plain string literals, and are always applied with ``re.fullmatch``
(Python's ``$`` also matches before a trailing newline).

A stdlib-only leaf: ``models.py`` imports it on the eager CLI path.
"""

from __future__ import annotations

import re

#: The contract version. A player rejects any other major with
#: ``schema-unsupported`` instead of guessing.
TOUR_SCHEMA_VERSION = 1
ACCEPTED_TOUR_SCHEMA_MAJORS = (1,)

# ── Vocabulary ────────────────────────────────────────────────────────────────

#: How a step advances. ``visible`` waits for the step's target to appear;
#: ``manual`` waits for the Next button.
ADVANCE_KINDS = ("click", "input", "change", "visible", "manual")
#: Advance kinds that need a target element — a targetless (centred) step can
#: only advance on ``manual``.
TARGETED_ADVANCE_KINDS = frozenset({"click", "input", "change", "visible"})

PLACEMENTS = ("top", "bottom", "left", "right", "auto")

#: What a click on an information step's target does (a targeted ``manual``
#: step): ``pass`` lets it reach the element, ``next`` swallows it and advances,
#: ``block`` swallows it and does nothing.
TARGET_CLICK_MODES = ("pass", "next", "block")

#: Values of the step-level ``tour:`` annotation.
STEP_TOUR_MODES = ("skip", "keep", "start", "end")

#: DOM events the overlay dispatches (bubbling, composed). Colon-free on
#: purpose: Angular templates read ``(a:b)`` as a global event target.
TOUR_EVENTS = ("tour-start", "tour-step", "tour-complete", "tour-dismiss", "tour-offpath", "tour-error")

#: ``tour-error`` reasons.
TOUR_ERROR_REASONS = ("target-timeout", "entry-params", "load-failed", "schema-unsupported", "invalid-data")

#: Languages the overlay ships chrome strings for (buttons, progress, generic
#: hints). Any other tour language shows them in English unless the app sets
#: the host's ``strings`` — the compiler warns ``chrome-strings-missing``.
CHROME_STRING_LOCALES = ("en", "fr", "nl", "de")

#: ``part`` names the overlay exposes for ``::part()`` theming. The replay test
#: helpers locate the bubble and its buttons through these, never through text.
TOUR_PARTS = (
    "layer",
    "spotlight",
    "bubble",
    "title",
    "body",
    "progress",
    "button",
    "primary",
    "secondary",
    "next",
    "close",
    "skip",
    "end",
    "resume",
    "pill",
    "guide-link",
)

#: Attributes the overlay sets on its bubble (``section[part~=bubble]``). The
#: replay helpers scope the Next button by ``data-step-id`` and read
#: ``data-state`` to tell an anchored popover from the fallback one.
ATTR_TOUR_ID = "data-tour-id"
ATTR_STEP_ID = "data-step-id"
ATTR_STATE = "data-state"
ATTR_ADVANCE = "data-advance"
BUBBLE_STATES = ("shown", "fallback")

# ── Grammars ──────────────────────────────────────────────────────────────────

TEST_ID_PATTERN = r"^[A-Za-z0-9_.:/-]{1,128}$"
#: Step ids are test-ids or heading slugs, so the same grammar holds.
STEP_ID_PATTERN = TEST_ID_PATTERN
TEST_ID_ATTRIBUTE_PATTERN = r"^data-[a-z0-9-]+$"
TOUR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
LAYOUT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
ROLE_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
#: Coarse shape only; the compiler additionally runs ``locales.validate_locale_tag``.
LOCALE_PATTERN = r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,5}$"
SRI_PATTERN = r"^sha384-[A-Za-z0-9+/]{64}$"
TOUR_FILE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}[.][A-Za-z0-9_-]{1,64}[.]json$"
#: A base-relative route: ``/`` or ``/seg/:param/...``. Literal segments use the
#: path-safe RFC 3986 set (percent-encoded otherwise); ``.``/``..`` segments
#: (``%2e`` counts as a dot, as URL parsers read it) and bad ``%XX`` escapes are
#: rejected by :func:`is_route`, not by the regex.
ROUTE_PATTERN = r"^/$|^(?:/(?::[A-Za-z_][A-Za-z0-9_]{0,63}|[A-Za-z0-9._~%!$&()*+,;=@-]{1,128})){1,32}$"
MEDIA_QUERY_PATTERN = r"^[A-Za-z0-9 :().,<>=/_-]{1,256}$"

# ── Limits (a hostile payload must not be able to freeze the page) ────────────

MAX_TOURS = 500
MAX_STEPS = 100
MAX_LOCALES = 32
MAX_LAYOUTS = 16
MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 2000
MAX_ROUTE_CHARS = 512
MAX_URL_CHARS = 2048
MAX_MEDIA_QUERY_CHARS = 256
MAX_TOUR_FILE_BYTES = 262_144
MAX_MANIFEST_BYTES = 1_048_576

# Reserved on Windows for the basename *before* the extension, so `con.en.json`
# is un-creatable just as `con` is. Same list as `exclusive._WINDOWS_RESERVED`.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _full(pattern: str, value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def is_test_id(value: object) -> bool:
    return _full(TEST_ID_PATTERN, value)


def is_tour_id(value: object) -> bool:
    """A tour id names files (``<id>.<locale>.json``), so Windows device names are out."""
    return _full(TOUR_ID_PATTERN, value) and str(value).casefold() not in _WINDOWS_RESERVED


def is_layout_name(value: object) -> bool:
    return _full(LAYOUT_NAME_PATTERN, value)


def is_route(value: object) -> bool:
    """A same-origin, base-relative route pattern the player will accept."""
    if not isinstance(value, str) or len(value) > MAX_ROUTE_CHARS or not _full(ROUTE_PATTERN, value):
        return False
    if _PERCENT_RE.search(value):
        return False
    return not any(is_dot_segment(seg) for seg in value.split("/")[1:])


def is_dot_segment(segment: str) -> bool:
    """``.``/``..``, also spelled ``%2e`` — URL parsers resolve both, escaping the app's base path."""
    return segment.lower().replace("%2e", ".") in (".", "..")


def is_media_query(value: object) -> bool:
    return _full(MEDIA_QUERY_PATTERN, value)


def tour_file_name(tour_id: str, locale: str) -> str:
    """``<tour-id>.<locale>.json`` — the only file names a manifest may list."""
    return f"{tour_id}.{locale}.json"
