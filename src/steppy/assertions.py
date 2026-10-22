"""The ``expect:`` assertion grammar — parsed in ONE place.

Historically this lived only in ``generator._format_assertion``, which made
codegen the sole authority on what an assertion means. That had a hole with
teeth: ``_format_assertion`` returned ``{"type": "unknown"}`` for anything it did
not recognise, ``assert_body``'s ``{% else %}`` rendered ``pass``, and
``steppy validate`` said nothing. A typo — or ``status("done")``, which *parsed*
but had no template branch — produced a green test that asserted nothing.

So the grammar moved here, to a stdlib-only leaf both sides import:

  * ``generator._format_assertion`` delegates to :func:`parse_assertion`;
  * ``Step.validate_expect_grammar`` (models.py) calls it too, so an
    unrecognised form is a loud ``ValidationError`` at ``steppy validate``
    instead of a silent ``pass``.

``models.py`` is on the eager CLI path, so this module imports stdlib only —
the same rule ``captures.py``, ``runmeta.py`` and ``history.py`` follow.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = [
    "ASSERTION_FORMS",
    "AssertionSyntaxError",
    "parse_assertion",
    "split_top_level",
]


class AssertionSyntaxError(ValueError):
    """Raised when an ``expect:`` string is not a recognised assertion."""


#: Every recognised head, for the error message. Declaration order matches the
#: way the docs list them.
ASSERTION_FORMS: tuple[str, ...] = (
    'toast("message")',
    "visible(selector)",
    "hidden(selector)",
    'text(selector, "expected")',
    'contains(selector, "expected")',
    'url-contains("/path")',
    "count(selector, N) | count(selector, >=N)",
    'row-contains("text")',
    "not-empty(selector)",
    "disabled(selector)",
    "enabled(selector)",
    # Round 2. Every one of these was previously a silently-passing assertion:
    # unrecognised forms fell through to `pass`, so a scenario asserting
    # `value(...)` was green and checking nothing.
    'value(selector, "expected")',
    'attribute(selector, "name", "expected")',
    'not-contains(selector, "text")',
    "not-visible(selector)",
    'icontains(selector, "text")',
    "checked(selector)",
    "unchecked(selector)",
    'stays-hidden(selector, "3s")',
)

_COMPARATOR = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+)$")


def split_top_level(inner: str, maxsplit: int = -1) -> list[str]:
    """Split ``inner`` on commas that are not inside quotes or brackets.

    ``inner.split(",", 1)`` — what this replaces — splits on the FIRST comma
    wherever it falls. Selectors are written bare here (``contains([data-testid=
    "row"], "Approved")``), so a comma inside the attribute brackets or inside a
    quoted attribute value used to cut the selector in half and hand codegen two
    nonsense fragments. Tracking quote and bracket depth is the whole fix; a
    selector with no comma splits identically to before.

    A bare, unbracketed selector *list* (``#a, #b``) stays ambiguous — it is
    indistinguishable from the argument separator — and is not supported, same
    as before.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for ch in inner:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if quote:
            buf.append(ch)
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0 and (maxsplit < 0 or len(parts) < maxsplit):
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unquote(value: str) -> str:
    """Strip one layer of surrounding quotes, matching the historical
    ``.strip('"').strip("'")`` exactly (including its tolerance of unbalanced
    quotes) so existing scenarios keep generating byte-identically."""
    return value.strip('"').strip("'")


def _inner(expect: str, head: str) -> str:
    """The text between ``head(`` and the closing ``)``.

    The old code sliced ``expect[len(head):-1]`` unconditionally, so a missing
    ``)`` silently ate the last character instead of failing. Checking it here
    turns that into a validation error.
    """
    if not expect.endswith(")"):
        raise AssertionSyntaxError(f"assertion {expect!r} is missing its closing ')'")
    return expect[len(head) + 1 : -1]


def parse_assertion(expect: str) -> dict[str, str]:
    """Parse an ``expect:`` string into the codegen descriptor.

    Raises :class:`AssertionSyntaxError` for anything unrecognised — the
    behaviour that replaces the old silent ``{"type": "unknown"}`` -> ``pass``.
    """
    expect = (expect or "").strip()
    if not expect:
        raise AssertionSyntaxError("expect: is empty")

    if expect.startswith("toast("):
        return {"type": "toast", "message": _unquote(_inner(expect, "toast"))}

    if expect.startswith("visible("):
        return {"type": "visible", "selector": _inner(expect, "visible").strip()}

    if expect.startswith("hidden("):
        return {"type": "hidden", "selector": _inner(expect, "hidden").strip()}

    if expect.startswith("text("):
        parts = split_top_level(_inner(expect, "text"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('text(...) needs two arguments: text(selector, "expected")')
        return {"type": "text", "selector": parts[0].strip(), "text": _unquote(parts[1].strip())}

    # Checked before ``contains(`` only for readability; the heads do not overlap.
    if expect.startswith("url-contains("):
        return {"type": "url-contains", "path": _unquote(_inner(expect, "url-contains").strip())}

    if expect.startswith("contains("):
        parts = split_top_level(_inner(expect, "contains"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('contains(...) needs two arguments: contains(selector, "expected")')
        return {"type": "contains", "selector": parts[0].strip(), "text": _unquote(parts[1].strip())}

    if expect.startswith("count("):
        parts = split_top_level(_inner(expect, "count"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError("count(...) needs two arguments: count(selector, N) or count(selector, >=N)")
        selector, raw = parts[0].strip(), parts[1].strip()
        cmp_match = _COMPARATOR.match(raw)
        if cmp_match:
            return {
                "type": "count-cmp",
                "selector": selector,
                "comparator": cmp_match.group(1),
                "count": cmp_match.group(2),
            }
        if not raw.isdigit():
            # This used to fall through to ``to_have_count(<raw>)``, which emits
            # a SyntaxError in the generated file for e.g. ``count(sel, >={{n}})``.
            raise AssertionSyntaxError(
                "count(...) expects an integer, or a comparator (>=, <=, ==, !=, >, <) "
                f"followed by an integer, got {raw!r}"
            )
        return {"type": "count", "selector": selector, "count": raw}

    if expect.startswith("value("):
        parts = split_top_level(_inner(expect, "value"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('value(...) needs two arguments: value(selector, "expected")')
        return {"type": "value", "selector": parts[0].strip(), "text": _unquote(parts[1].strip())}

    if expect.startswith("attribute("):
        parts = split_top_level(_inner(expect, "attribute"), maxsplit=2)
        if len(parts) != 3:
            raise AssertionSyntaxError('attribute(...) needs three arguments: attribute(selector, "name", "expected")')
        return {
            "type": "attribute",
            "selector": parts[0].strip(),
            "attribute": _unquote(parts[1].strip()),
            "text": _unquote(parts[2].strip()),
        }

    # Checked before `contains(` and `visible(` would ever match: neither head
    # is a prefix of these, but keeping them adjacent makes the negation set
    # easy to read.
    if expect.startswith("not-contains("):
        parts = split_top_level(_inner(expect, "not-contains"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('not-contains(...) needs two arguments: not-contains(selector, "text")')
        return {"type": "not-contains", "selector": parts[0].strip(), "text": _unquote(parts[1].strip())}

    if expect.startswith("not-visible("):
        return {"type": "not-visible", "selector": _inner(expect, "not-visible").strip()}

    if expect.startswith("icontains("):
        parts = split_top_level(_inner(expect, "icontains"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('icontains(...) needs two arguments: icontains(selector, "text")')
        return {"type": "icontains", "selector": parts[0].strip(), "text": _unquote(parts[1].strip())}

    if expect.startswith("unchecked("):
        return {"type": "unchecked", "selector": _inner(expect, "unchecked").strip()}

    if expect.startswith("checked("):
        return {"type": "checked", "selector": _inner(expect, "checked").strip()}

    if expect.startswith("stays-hidden("):
        parts = split_top_level(_inner(expect, "stays-hidden"), maxsplit=1)
        if len(parts) != 2:
            raise AssertionSyntaxError('stays-hidden(...) needs two arguments: stays-hidden(selector, "3s")')
        return {"type": "stays-hidden", "selector": parts[0].strip(), "duration": _unquote(parts[1].strip())}

    if expect.startswith("row-contains("):
        return {"type": "row-contains", "text": _unquote(_inner(expect, "row-contains").strip())}

    if expect.startswith("not-empty("):
        return {"type": "not-empty", "selector": _inner(expect, "not-empty").strip()}

    if expect.startswith("disabled("):
        return {"type": "disabled", "selector": _inner(expect, "disabled").strip()}

    if expect.startswith("enabled("):
        return {"type": "enabled", "selector": _inner(expect, "enabled").strip()}

    raise AssertionSyntaxError(f"unknown assertion {expect!r}. Valid forms: " + " | ".join(ASSERTION_FORMS))
