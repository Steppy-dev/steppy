"""Statement-level rewrite rules: one Playwright call → one declarative step.

The unit is a *statement*, not a block, because the corpus is overwhelmingly
made of short blocks that chain two or three of them with ``;``. Measured over
4 967 real ``custom`` bodies, twenty rules of this shape match every statement in
2 747 of them.

Each rule is a regex over a normalised statement plus a builder that returns the
step dict a scenario would have written. A rule returning ``None`` means "not
this one"; a statement no rule matches makes the whole block unconvertible, and
the block is left alone.

Pure and stdlib-only: no Playwright, no Jinja, no config. The decision about
whether a *block* may be rewritten lives in :mod:`steppy.migrate.plan`, and the
proof that the rewrite is equivalent lives in :mod:`steppy.migrate.verify`.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Callable, Optional

__all__ = ["RULES", "parse_block", "split_statements", "statements"]

# ── Statement splitting ─────────────────────────────────────────────────────

# `page`, or `<role>_page`, as the corpus writes it.
_PAGE = r"(?:\w+_)?page"
_STR = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
_MATCH = r"(?:\.(?:first|last|nth\(\d+\)))?"


def split_statements(code: str) -> list[str]:
    """Split a ``custom`` body into statements, respecting quotes and brackets.

    Bodies come in two shapes and both have to work: a YAML block scalar with
    one statement per line, and a single quoted line chaining several with
    ``;``. 3 984 of one project's 4 967 blocks are the second kind.
    """
    out: list[str] = []
    for line in code.splitlines():
        parts: list[str] = []
        buf: list[str] = []
        depth = 0
        quote: Optional[str] = None
        escaped = False
        for ch in line:
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
            if ch == ";" and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
            buf.append(ch)
        parts.append("".join(buf))
        for part in parts:
            stripped = part.strip()
            if stripped and not stripped.startswith("#"):
                out.extend(_split_tuple_expression(stripped))
    return out


#: Keywords whose own grammar puts a comma at the top level of a statement.
_COMMA_KEYWORDS = frozenset(
    {"assert", "raise", "del", "return", "yield", "import", "from", "global", "nonlocal", "with", "for", "lambda"}
)


def _split_tuple_expression(statement: str) -> list[str]:
    """``a(...), b(...)`` -> two statements.

    Python's comma operator lets two calls share a line, and the corpus uses it:
    ``page.locator(sel).wait_for(...), ensure_open(page, sel)`` appears in 74
    blocks. That is two statements wearing one coat, and splitting it here means
    every rule sees the shape it expects instead of needing a tuple variant.

    Only top-level commas count, and only when the statement is neither an
    assignment (``a, b = f()`` is one statement) nor led by a keyword whose own
    syntax uses a top-level comma. ``assert x, "message"`` is the one that
    matters: splitting it silently threw away the failure message, which 78% of
    the corpus's assert blocks carry.
    """
    if statement.split("(", 1)[0].split()[0:1] and statement.split()[0] in _COMMA_KEYWORDS:
        return [statement]
    depth = 0
    quote: Optional[str] = None
    escaped = False
    cuts: list[int] = []
    for index, ch in enumerate(statement):
        if escaped:
            escaped = False
            continue
        if quote:
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and depth == 0 and statement[index - 1 : index] not in ("=", "!", "<", ">"):
            return [statement]  # an assignment; the comma binds the target list
        elif ch == "," and depth == 0:
            cuts.append(index)
    if not cuts:
        return [statement]
    pieces, previous = [], 0
    for cut in cuts:
        pieces.append(statement[previous:cut].strip())
        previous = cut + 1
    pieces.append(statement[previous:].strip())
    return [piece for piece in pieces if piece]


#: Kept under the historical name used by the analysis scripts.
statements = split_statements


def _literal(text: str) -> Any:
    """A Python literal as its value, or the raw text if it is not one."""
    try:
        return ast.literal_eval(text.strip())
    except (ValueError, SyntaxError):
        return text.strip()


def _match_field(suffix: str) -> Optional[str]:
    """``.first`` / ``.nth(2)`` → the ``match:`` value it corresponds to."""
    if not suffix:
        return None
    if suffix == ".first":
        return "first"
    if suffix == ".last":
        return "last"
    nth = re.fullmatch(r"\.nth\((\d+)\)", suffix)
    return nth.group(1) if nth else None


def _with_match(step: dict, suffix: str) -> dict:
    value = _match_field(suffix)
    if value is not None:
        step["match"] = value
    return step


def _ms(raw: Optional[str]) -> Optional[int]:
    return int(raw) if raw else None


# ── The rules ───────────────────────────────────────────────────────────────
#
# Order matters only where one pattern could shadow another; each is anchored,
# so in practice they are independent.

Rule = tuple[str, re.Pattern, Callable[[re.Match], Optional[dict]]]


def _rule(name: str, pattern: str, build: Callable[[re.Match], Optional[dict]]) -> Rule:
    return name, re.compile(pattern), build


def _click(m: re.Match) -> dict:
    step = {"action": "click", "selector": _literal(m.group("sel"))}
    if m.group("force"):
        step["force"] = True
    return _with_match(step, m.group("match") or "")


def _check(m: re.Match) -> dict:
    # `toggle` is idempotent where a bare `.check()` is not, which is strictly
    # better: re-running a scenario cannot untick a box that was already ticked.
    step = {
        "action": "toggle",
        "selector": _literal(m.group("sel")),
        "control": "self",
        "state": "off" if m.group("un") else "on",
    }
    return _with_match(step, m.group("match") or "")


def _ambiguous_selector(selector: str) -> bool:
    """True when a bare selector holds a comma the `expect:` grammar cannot place.

    `count(#a, #b, >=1)` has no reading: the grammar splits on the first comma
    and cannot tell the selector list from the argument separator. Rather than
    emit an assertion that means something else, the block stays custom.
    """
    depth = 0
    for ch in selector:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return True
    return False


def _assert(check: str, message: Optional[str]) -> Optional[dict]:
    """An assert step, carrying its failure message when there is one.

    The message is the reason the author wrote the assertion by hand at all —
    78% of the corpus's assert blocks have one — so a rewrite that silently
    dropped it would trade a described failure for a bare traceback. Only a
    literal message is kept: an f-string interpolates the observed value, which
    a step cannot express, so those blocks stay custom.
    """
    inner = check[check.index("(") + 1 : check.rindex(")")]
    if _ambiguous_selector(inner.rsplit(",", 1)[0]):
        return None
    if message is None:
        return {"action": "assert", "expect": check}
    try:
        text = ast.literal_eval(message.strip())
    except (ValueError, SyntaxError):
        return None
    if not isinstance(text, str):
        return None
    return {"action": "assert", "expect": [{"check": check, "message": text}]}


def _wait_for(m: re.Match) -> dict:
    step: dict = {"action": "wait-for", "selector": _literal(m.group("sel"))}
    if m.group("state"):
        step["state"] = _literal(m.group("state"))
    if m.group("timeout"):
        step["timeout"] = _ms(m.group("timeout"))
    return _with_match(step, m.group("match") or "")


def _wait_for_selector(m: re.Match) -> dict:
    step: dict = {"action": "wait-for", "selector": _literal(m.group("sel"))}
    if m.group("state"):
        step["state"] = _literal(m.group("state"))
    if m.group("timeout"):
        step["timeout"] = _ms(m.group("timeout"))
    return step


RULES: list[Rule] = [
    _rule(
        "click",
        rf"^{_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.click\((?P<force>force=True)?\)$",
        _click,
    ),
    _rule(
        "click-direct",
        rf"^{_PAGE}\.click\((?P<sel>{_STR})\)$",
        lambda m: {"action": "click", "selector": _literal(m.group("sel"))},
    ),
    _rule(
        "check",
        rf"^{_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.(?P<un>un)?check\((?:force=True)?\)$",
        _check,
    ),
    _rule(
        "wait-for",
        rf"^{_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.wait_for\("
        rf"(?:state=(?P<state>{_STR}))?(?:,\s*)?(?:timeout=(?P<timeout>\d+))?\)$",
        _wait_for,
    ),
    _rule(
        "wait-for-selector",
        rf"^{_PAGE}\.wait_for_selector\((?P<sel>{_STR})"
        rf"(?:,\s*state=(?P<state>{_STR}))?(?:,\s*timeout=(?P<timeout>\d+))?\)$",
        _wait_for_selector,
    ),
    _rule(
        "sleep",
        rf"^{_PAGE}\.wait_for_timeout\((?P<ms>\d+)\)$",
        lambda m: {"action": "wait", "duration": f"{m.group('ms')}ms"},
    ),
    _rule(
        "navigate",
        rf"^{_PAGE}\.goto\((?P<target>{_STR})\)$",
        lambda m: {"action": "navigate", "target": _literal(m.group("target"))},
    ),
    _rule(
        "reload",
        rf"^{_PAGE}\.reload\((?:wait_until=(?P<wait>{_STR}))?\)$",
        lambda m: {
            "action": "navigate",
            "reload": True,
            **({"wait": _literal(m.group("wait"))} if m.group("wait") else {}),
        },
    ),
    _rule(
        "load-state",
        rf"^{_PAGE}\.wait_for_load_state\((?P<wait>{_STR})?\)$",
        # Nothing to emit on its own: a bare load-state wait is a modifier on the
        # navigation before it, and `plan` folds it into that step.
        lambda m: {"action": "_load-state", "wait": _literal(m.group("wait")) if m.group("wait") else "load"},
    ),
    _rule(
        "fill",
        rf"^{_PAGE}\.fill\((?P<sel>{_STR}),\s*(?P<value>{_STR})\)$",
        lambda m: {"action": "fill", "selector": _literal(m.group("sel")), "value": _literal(m.group("value"))},
    ),
    _rule(
        "fill-locator",
        rf"^{_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.fill\((?P<value>{_STR})\)$",
        lambda m: _with_match(
            {"action": "fill", "selector": _literal(m.group("sel")), "value": _literal(m.group("value"))},
            m.group("match") or "",
        ),
    ),
    _rule(
        "hover",
        rf"^{_PAGE}\.hover\((?P<sel>{_STR})\)$",
        lambda m: {"action": "hover", "selector": _literal(m.group("sel"))},
    ),
    _rule(
        "scroll",
        rf"^{_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.scroll_into_view_if_needed\(\)$",
        lambda m: _with_match({"action": "scroll", "selector": _literal(m.group("sel"))}, m.group("match") or ""),
    ),
    _rule(
        "keyboard",
        rf"^{_PAGE}\.keyboard\.press\((?P<key>{_STR})\)$",
        lambda m: {"action": "keyboard", "value": _literal(m.group("key"))},
    ),
    _rule(
        "assert-count",
        rf"^assert {_PAGE}\.locator\((?P<sel>{_STR})\)\.count\(\)\s*"
        rf"(?P<cmp>>=|<=|==|!=|>|<)\s*(?P<n>\d+)\s*(?:,\s*(?P<msg>.*))?$",
        lambda m: _assert(f"count({_literal(m.group('sel'))}, {m.group('cmp')}{m.group('n')})", m.group("msg")),
    ),
    _rule(
        "assert-visible",
        rf"^assert {_PAGE}\.locator\((?P<sel>{_STR})\)(?P<match>{_MATCH})\.is_visible\(\)\s*(?:,\s*(?P<msg>.*))?$",
        lambda m: _with_match(_assert(f"visible({_literal(m.group('sel'))})", m.group("msg")), m.group("match") or ""),
    ),
    _rule(
        "call",
        # `from x_helpers import y` has already been stripped by `parse_block`;
        # what is left is the call itself.
        r"^(?P<fn>[a-z_]\w*)\((?P<page>(?:\w+_)?page)(?:,\s*(?P<args>.*))?\)$",
        # The module is filled in by `parse_block`, which is the only place that
        # has seen the import line.
        lambda m: {"action": "call", "_fn_name": m.group("fn"), "_args_src": (m.group("args") or "").strip()},
    ),
]

#: Statements that carry no behaviour and are simply dropped.
#:
#: The ``sys.path`` preamble opens 2 334 blocks in one project and was never
#: necessary: pytest is launched as ``python -m pytest`` from the project root,
#: and ``python -m`` puts the working directory on ``sys.path[0]``.
_NOISE = re.compile(
    r"^(?:"
    r"None"
    r"|import\s+sys(?:\s*,\s*\w+)*"
    r"|import\s+(?:datetime|re|json|time|os)"
    r"|sys\.path\.insert\(.*\)"
    r")$"
)

_IMPORT = re.compile(r"^from\s+(?P<module>[\w.]+)\s+import\s+(?P<names>.+)$")


def parse_block(code: str) -> Optional[list[dict]]:
    """Every statement in ``code`` as a step dict, or ``None`` if any is unknown.

    All-or-nothing on purpose. A block that half-converts would leave a step
    doing less than the author wrote, and the reader has no way to see it: the
    scenario would still look complete.
    """
    steps: list[dict] = []
    imported: dict[str, str] = {}  # helper name -> module

    for statement in split_statements(code):
        if _NOISE.match(statement):
            continue
        imp = _IMPORT.match(statement)
        if imp:
            for name in imp.group("names").split(","):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    imported[name] = imp.group("module")
            continue

        for _name, pattern, build in RULES:
            found = pattern.match(statement)
            if not found:
                continue
            step = build(found)
            if step is None:
                continue
            if step.get("action") == "call":
                fn_name = step.pop("_fn_name")
                args_src = step.pop("_args_src")
                module = imported.get(fn_name)
                if module is None:
                    # A bare call to something never imported here: it may be a
                    # builtin, a local, or an import from an earlier step. Not
                    # safe to rewrite.
                    return None
                step["fn"] = f"{module}.{fn_name}"
                args = _parse_call_args(args_src)
                if args is None:
                    return None
                positional, keywords = args
                if positional:
                    step["args"] = positional
                if keywords:
                    step["kwargs"] = keywords
            steps.append(step)
            break
        else:
            return None
    return steps or None


def _parse_call_args(src: str) -> Optional[tuple[list, dict]]:
    """Literal positional and keyword arguments of a helper call.

    ``None`` when any argument is an expression rather than a literal — a
    computed date, another call, a ``globals()`` read. Those carry behaviour the
    step form cannot hold, so the block stays custom.
    """
    if not src:
        return [], {}
    try:
        call = ast.parse(f"_f({src})", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    positional: list = []
    for arg in call.args:
        try:
            positional.append(ast.literal_eval(arg))
        except ValueError:
            return None
    keywords: dict = {}
    for keyword in call.keywords:
        if keyword.arg is None:  # **kwargs
            return None
        try:
            keywords[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError:
            return None
    return positional, keywords
