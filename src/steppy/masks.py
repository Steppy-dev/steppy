"""Screenshot masks: the ``mask:`` of a scenario's front matter and of a step.

A mask is one selector, or a list of them. Playwright covers every element it
matches with a solid box when a screenshot is taken, so the pixels never reach
the disk. ``screenshot.mask`` in ``config.yaml`` applies to every scenario; a
scenario adds its own, and so does a step for its own captures. It is always a
union: nothing a scenario writes can remove a project's mask.

This module is the one home for three things:

  * the **rules** a mask must follow (:func:`normalize_masks`), shared by
    ``Scenario``, ``Step`` and ``ScreenshotConfig``;
  * the **union** (:func:`merge_masks`);
  * the **reading of the raw text**, because a bare attribute selector is shaped
    exactly like a YAML flow sequence. ``mask: [data-testid="iban"]`` must mean
    that selector, and YAML reads it as a list holding ``data-testid="iban"``,
    which Playwright accepts, matches with nothing, and masks nothing. So the
    parser hands the author's text to :func:`quote_front_matter`,
    :func:`quote_items` or :func:`inline_yaml` before YAML sees it.

A mask that is silently wrong is a leak, so everything doubtful is an error here
rather than a guess. What this module cannot know is whether a well-formed
selector matches anything: that shows at run time, as with ``screenshot.mask``.

Stdlib-only leaf: it imports nothing from ``steppy``, so ``models.py`` can import
it on the eager CLI path (like ``assertions``, ``durations`` and ``exclusive``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

__all__ = [
    "MaskSyntaxError",
    "inline_yaml",
    "merge_masks",
    "normalize_masks",
    "quote_front_matter",
    "quote_items",
]


class MaskSyntaxError(ValueError):
    """Raised when a ``mask:`` is not a selector or a list of selectors."""


# ── The rules ────────────────────────────────────────────────────────────────

#: ``data-testid="iban"``: an attribute selector whose brackets YAML took for a
#: list. ``text="…"`` and the ``internal:`` engines are the only selectors
#: Playwright spells ``<engine>="…"``.
_LOST_BRACKETS = re.compile(r"""^(?!text\s*=|internal:)[A-Za-z_][\w:.-]*\s*[~|^$*]?=\s*["']""")

_EMPTY = "'mask' is empty: give a selector, or an indented list of them (remove the key to mask nothing)"


def normalize_masks(value: Any, *, env_refs: bool = False) -> list[str]:
    """``value`` as a list of selectors, in the author's order, without duplicates.

    Raises :class:`MaskSyntaxError` for anything else, with what to write instead.
    ``env_refs`` is for ``screenshot.mask``: ``config.yaml`` replaces ``${VAR}``
    before this reads it, and one left unset must not stop every command.
    """
    if isinstance(value, str):
        value = [value]
    if value is None or (isinstance(value, (list, tuple)) and not value):
        raise MaskSyntaxError(_EMPTY)
    if not isinstance(value, (list, tuple)):
        raise MaskSyntaxError(
            f"'mask' must be a selector, or an indented list of them, got: {value!r} (quote a selector YAML "
            "would read as something else)"
        )
    return merge_masks(_check(item, env_refs) for item in value)


def _check(item: Any, env_refs: bool) -> str:
    if not isinstance(item, str):
        raise MaskSyntaxError(
            f"'mask' entries must be selectors written as text, got: {item!r} (quote a selector YAML would read "
            "as something else)"
        )
    selector = item.strip()
    if not selector:
        raise MaskSyntaxError(_EMPTY)
    if "\n" in selector:
        raise MaskSyntaxError("a 'mask' selector is one line; write several as an indented list")
    if "{{" in selector or ("${" in selector and not env_refs):
        raise MaskSyntaxError(
            f"'mask' selectors are used as written, so {{{{var}}}} and ${{VAR}} are not replaced: {selector!r}"
        )
    if _LOST_BRACKETS.match(selector):
        raise MaskSyntaxError(
            f"'mask' entry {selector!r} has lost its brackets (YAML reads an unquoted [...] as a list): "
            f"quote it, '[{selector}]'"
        )
    return selector


def merge_masks(*groups: Optional[Iterable[str]]) -> list[str]:
    """The union of several masks: first occurrence wins, order kept."""
    return list(dict.fromkeys(selector for group in groups if group for selector in group))


# ── The author's text ────────────────────────────────────────────────────────

_QUOTED = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*')"""

#: ``[".a", '.b']``: the one flow sequence that is a list of masks.
_QUOTED_LIST = re.compile(rf"\[\s*{_QUOTED}(?:\s*,\s*{_QUOTED})*\s*,?\s*\]")

#: A CSS attribute selector at the start of a value: ``[name]``, ``[name="x"]``,
#: ``[name^='x' i]``. The name excludes everything that would make it a list.
_ATTRIBUTE_SELECTOR = re.compile(
    r"""\[\s*(?:\\.|[^\s\[\]"'=,.#>+~|^$*()\\])+\s*"""
    r"""(?:[~|^$*]?=\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s\[\]"',]+)\s*[iIsS]?\s*)?\]"""
)

#: A plain scalar YAML may resolve to a boolean, a number, a date or null.
_RESOLVABLE = re.compile(r"[\w.~:+-]+")

_LIST_HINT = (
    "a list of masks is indented, one selector per line, or a [...] of quoted selectors; an unquoted [...] is one "
    "attribute selector"
)


def _comment_at(text: str) -> Optional[int]:
    """Where an unquoted ``#`` starts a YAML comment in ``text``, else ``None``."""
    quote = ""
    for i, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#" and (i == 0 or text[i - 1].isspace()):
            return i
    return None


def _quote_value(raw: str) -> str:
    """One YAML value position, rewritten so that YAML reads what the author wrote.

    Only a value YAML would misread changes: an attribute selector (a list to
    YAML) and ``*[…]`` (an alias). A quoted or otherwise plain value is left as
    it is, comment included. An id selector after a space is refused: to YAML it
    is a comment, so ``.card #iban`` would silently mask every ``.card``.
    """
    if not raw or raw[0] in "\"'|>{":
        return raw
    comment = _comment_at(raw)
    if comment is not None:
        if raw[comment + 1 : comment + 2].strip():
            raise MaskSyntaxError(f"YAML reads {raw[comment:]!r} as a comment in mask {raw!r}: quote the selector")
        raw, rest = raw[:comment].rstrip(), "  " + raw[comment:]
    else:
        rest = ""
    if not raw or raw[0] not in "[*" or _QUOTED_LIST.fullmatch(raw):
        return raw + rest
    if raw[0] == "[" and not _ATTRIBUTE_SELECTOR.match(raw):
        # A flow sequence left open continues on the next lines: YAML's to read.
        if re.fullmatch(r"\[\s*\]", raw) or not raw.endswith("]"):
            return raw + rest
        raise MaskSyntaxError(f"cannot read mask {raw!r}: {_LIST_HINT}")
    return json.dumps(raw, ensure_ascii=False) + rest


_ITEM = re.compile(r"^(?P<lead>[ \t]*-[ \t]+)(?P<value>\S.*?)[ \t]*$")


def _quote_item_line(line: str) -> str:
    cr = "\r" if line.endswith("\r") else ""
    m = _ITEM.match(line.removesuffix("\r"))
    return f"{m.group('lead')}{_quote_value(m.group('value'))}{cr}" if m else line


def quote_items(text: str) -> str:
    """The indented lines under a step's ``- mask:``, ready for YAML."""
    return "\n".join(_quote_item_line(line) for line in text.split("\n"))


#: Only the top-level key: a ``mask:`` nested in another block is not ours.
_FRONT_MATTER_KEY = re.compile(r"""^(?P<key>(?:mask|"mask"|'mask')[ \t]*:)(?:[ \t]+(?P<value>\S.*?))?[ \t]*$""")

#: Two ``mask:`` keys: YAML keeps the last and drops the other without a word.
DUPLICATE_HINT = "'mask' is written twice, and only one would apply: write several selectors as one indented list"


def quote_front_matter(header: str) -> str:
    """A front matter's YAML with its top-level ``mask:`` ready for YAML.

    Returned unchanged when there is no such key, or nothing in it to rewrite.
    """
    out = []
    in_list = seen = False
    for line in header.split("\n"):
        text = line.removesuffix("\r")
        m = _FRONT_MATTER_KEY.match(text)
        if m:
            if seen:
                raise MaskSyntaxError(DUPLICATE_HINT)
            seen = True
            value = m.group("value") or ""
            in_list = not value or re.match(r"#(?:\s|$)", value) is not None
            if not in_list:
                line = f"{m.group('key')} {_quote_value(value)}" + ("\r" if line.endswith("\r") else "")
        elif in_list:
            if text.strip() and not text[0].isspace() and text[0] not in "-#":
                in_list = False
            else:
                line = _quote_item_line(line)
        out.append(line)
    return "\n".join(out)


def inline_yaml(raw: str) -> Optional[str]:
    """What YAML should load for a step's one-line ``- mask: <raw>``, or ``None``.

    A step's ``- key: value`` is not YAML: the value is the selector as written
    (``- mask: .card #iban``), and ``None`` says to take it so. YAML is wanted
    only where it decides what the value is: a ``[...]`` of quoted selectors, a
    quoted selector, and the plain words it reads as something other than text
    (``true``, ``12``, ``~``), which are no selector at all.
    """
    if re.fullmatch(_QUOTED, raw) or _QUOTED_LIST.fullmatch(raw) or re.fullmatch(r"\[\s*\]", raw):
        return raw
    comment = _comment_at(raw)
    if comment is not None and not raw[comment + 1 : comment + 2].strip():
        # `#` before a space is in no selector, and a step's line has no comments.
        raise MaskSyntaxError(f"a step's line takes no comment: remove {raw[comment:]!r} from mask {raw!r}")
    if raw.startswith("[") and not _ATTRIBUTE_SELECTOR.match(raw):
        raise MaskSyntaxError(f"cannot read mask {raw!r}: {_LIST_HINT}")
    if raw.startswith("{") or _RESOLVABLE.fullmatch(raw):
        return raw
    return None
