"""Test-id grammar and selector → test-id extraction.

In-app tours store a single test-id value per step, never a CSS selector
(spec fixed decision 2). Scenarios, however, write free-form Playwright
selectors. :func:`extract_test_id` decides whether an authored selector *is* a
test-id in disguise and returns its value, or says precisely why not — with a
fix-it hint the compiler prints verbatim.

Accepted shapes (``A`` = the configured attribute, ``data-testid`` by default)::

    [A="X"]   [A='X']   [A=X]        a pure test-id
    tag[A="X"]                        tag-qualified
    [A="X"] tag                       host + one bare descendant tag, reduced to X:
                                      clicks/inputs inside the host bubble to it

Everything else — attribute prefix matches, descendant qualifiers, selector
lists, Playwright engines and ``>>`` chains, ``{{var}}`` ids, ``within:`` and
``match:`` other than ``first`` — cannot be expressed as one stable test-id.

A stdlib-only leaf (it reuses :func:`steppy.assertions.split_top_level`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .assertions import split_top_level
from .tour_spec import TEST_ID_ATTRIBUTE_PATTERN, TEST_ID_PATTERN

DEFAULT_TEST_ID_ATTRIBUTE = "data-testid"

_ENGINE_PREFIXES = ("text=", "css=", "xpath=", "role=", "id=", "internal:", "//", "..")

#: Rejection reason -> (explanation, fix-it hint).
REASONS: dict[str, tuple[str, str]] = {
    "within": (
        "the step is scoped with `within:`, so the test-id alone is not unique",
        "give the element its own unique test-id in the app, or mark the step `tour: skip`",
    ),
    "match": (
        "the step picks one of several matches with `match:`",
        "give the element its own unique test-id in the app, or mark the step `tour: skip`",
    ),
    "empty": ("the selector is empty", "point the step at a test-id"),
    "interpolated": (
        "the test-id is computed at run time ({{var}})",
        "point the step at a stable test-id, or mark it `tour: skip`",
    ),
    "playwright-engine": (
        "it uses a Playwright selector engine, which the in-app player cannot evaluate",
        "point the step at a test-id, or mark it `tour: skip`",
    ),
    "playwright-chain": (
        "it is a Playwright `>>` chain",
        "point the step at a single test-id, or mark it `tour: skip`",
    ),
    "selector-list": (
        "it is a comma-separated selector list",
        "point the step at a single test-id, or mark it `tour: skip`",
    ),
    "partial-match": (
        "it matches the test-id by prefix/suffix/substring",
        "point the step at an exact test-id, or mark it `tour: skip`",
    ),
    "no-test-id": (
        "it does not use the test-id attribute",
        "add a test-id in the app (or set `tours.test_id_attribute`), mark the step `tour: skip`, "
        "or `tour: keep` to show it as a centred note",
    ),
    "qualified-descendant": (
        "it reaches an element inside the test-id host with a qualified selector",
        "point the step at the host test-id, add a test-id on the inner element, mark the step `tour: skip`, "
        "or `tour: keep` to show it as a centred note",
    ),
    "invalid-value": (
        "the test-id value has characters outside [A-Za-z0-9_.:/-] (or is longer than 128)",
        "rename the test-id in the app",
    ),
}


@dataclass(frozen=True)
class TargetExtraction:
    """Outcome of :func:`extract_test_id`: a test-id, or the reason there is none."""

    test_id: Optional[str] = None
    reason: Optional[str] = None
    #: The bare descendant tag the selector was reduced from (``[A="X"] button`` → ``button``).
    reduced_from: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.test_id is not None

    def explain(self) -> str:
        """``<explanation> — <hint>`` for a failed extraction (empty when ok)."""
        if self.ok or self.reason is None:
            return ""
        why, hint = REASONS[self.reason]
        return f"{why} — {hint}"


def is_test_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(TEST_ID_PATTERN, value) is not None


def check_test_id_attribute(value: object) -> str:
    """Validate ``tours.test_id_attribute``; raise ``ValueError`` with the rule otherwise."""
    if not isinstance(value, str) or re.fullmatch(TEST_ID_ATTRIBUTE_PATTERN, value) is None:
        raise ValueError(f"test-id attribute {value!r} must match {TEST_ID_ATTRIBUTE_PATTERN} (e.g. 'data-testid')")
    return value


def _shape(attribute: str) -> re.Pattern[str]:
    a = re.escape(attribute)
    return re.compile(
        r"\s*(?P<tag>[A-Za-z][A-Za-z0-9-]*)?"
        r"\[\s*" + a + r"\s*=\s*"
        r"(?:\"(?P<dq>[^\"\\]*)\"|'(?P<sq>[^'\\]*)'|(?P<uq>-?[A-Za-z_][A-Za-z0-9_-]*))"
        r"\s*\]"
        r"(?:\s+(?P<desc>[A-Za-z][A-Za-z0-9-]*))?\s*"
    )


def extract_test_id(
    selector: Optional[str],
    attribute: str = DEFAULT_TEST_ID_ATTRIBUTE,
    *,
    within: Optional[str] = None,
    match: Optional[str] = None,
) -> TargetExtraction:
    """Return the single test-id ``selector`` designates, or why it designates none.

    The checks run in a fixed order and the first that applies decides, so the
    reason a scenario author sees is always the most specific one.
    """
    if within:
        return TargetExtraction(reason="within")
    if match is not None and str(match).strip().lower() != "first":
        return TargetExtraction(reason="match")
    sel = (selector or "").strip()
    if not sel:
        return TargetExtraction(reason="empty")
    if "{{" in sel:
        return TargetExtraction(reason="interpolated")
    if sel.startswith(_ENGINE_PREFIXES):
        return TargetExtraction(reason="playwright-engine")
    if ">>" in sel:
        return TargetExtraction(reason="playwright-chain")
    if len(split_top_level(sel)) > 1:
        return TargetExtraction(reason="selector-list")
    if re.search(re.escape(attribute) + r"\s*[\^$*~|]=", sel):
        return TargetExtraction(reason="partial-match")
    if not re.search(r"\[\s*" + re.escape(attribute) + r"\s*(?:[\^$*~|]?=|\])", sel):
        return TargetExtraction(reason="no-test-id")
    m = _shape(attribute).fullmatch(sel)
    if m is None:
        return TargetExtraction(reason="qualified-descendant")
    value = (
        m.group("dq") if m.group("dq") is not None else m.group("sq") if m.group("sq") is not None else m.group("uq")
    )
    if not is_test_id(value):
        return TargetExtraction(reason="invalid-value")
    return TargetExtraction(test_id=value, reduced_from=m.group("desc"))
