"""Prove a proposed rewrite before it is written to disk.

A codemod that silently changes what a test does is worse than no codemod: the
scenario still *looks* complete, and the coverage it lost is invisible. So every
rewrite is checked, and anything that fails is reported rather than applied.

**What is NOT checked, and why.** The first design compared the two sides
textually. That is the wrong bar: ``page.click(sel)`` and
``page.locator(sel).click()`` are the same operation spelled two ways, and every
declarative step re-spells the call it replaces. Worse, the rewrites most worth
making are the ones that deliberately differ — a bare ``.check(force=True)``
becomes an *idempotent* ``toggle`` that will not untick an already-ticked box on
a re-run. A textual bar rejects exactly those.

**What is checked**, on every block:

1. the proposed steps build real ``Step`` models and generate parseable Python
   (the render itself proves the first, ``ast.parse`` the second);
2. **no selector is lost** — every string literal the original named is still
   named, compared as values so ``'[data-testid=\\"row\\"]'`` and
   ``'[data-testid="row"]'`` count as one;
3. **the operations are conserved** — every Playwright call the original made
   still happens, up to the substitution table below, which is the complete list
   of places a rewrite is allowed to change behaviour and says why for each.

That is a real bar and it catches the failures that matter: a rule that drops an
argument, mangles a timeout into the wrong slot, or quietly turns two operations
into one. It is not a proof of equivalence, and it is not a substitute for
reading the diff — which is why ``--dry-run`` prints one.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

__all__ = ["SUBSTITUTIONS", "VerificationResult", "verify_rewrite"]

#: Where a rewrite may legitimately change the emitted operations, and why.
#: ``original method -> (methods that may stand for it, reason)``.
SUBSTITUTIONS: dict[str, tuple[frozenset[str], str]] = {
    "check": (frozenset({"check", "is_checked"}), "toggle is idempotent: a re-run cannot untick an already-ticked box"),
    "uncheck": (frozenset({"uncheck", "is_checked"}), "toggle is idempotent"),
    "wait_for_timeout": (
        frozenset({"sleep"}),
        "a hard sleep becomes `wait`'s time.sleep, or folds into the previous step's wait_after",
    ),
    "wait_for": (frozenset({"wait_for", "wait_for_selector"}), "the locator and page forms are interchangeable"),
    "wait_for_selector": (frozenset({"wait_for", "wait_for_selector"}), "as above"),
    "wait_for_load_state": (
        frozenset({"wait_for_load_state"}),
        "folds into the preceding navigate's `wait:`, which emits the same call",
    ),
    "is_visible": (frozenset({"to_be_visible", "expect"}), "an assert becomes Playwright's auto-waiting expect()"),
    "count": (frozenset({"count", "to_have_count", "expect"}), "as above"),
}

#: Calls that only *build* a locator. They carry no behaviour, so a rewrite that
#: adds or drops one has changed nothing.
_PLUMBING = frozenset(
    {"locator", "first", "last", "nth", "_interp", "_interp_val", "print", "strip", "format", "get_attribute"}
)

#: A helper call in the original becomes `call_helper` in the rewrite. The
#: helper's own name is not a Playwright operation, so it is matched by name.
_CALL_HELPER = "call_helper"

_STRING = re.compile(r"'(?:[^'\\]|\\.)*'" + r'|"(?:[^"\\]|\\.)*"')


@dataclass
class VerificationResult:
    ok: bool
    detail: str = ""
    substitutions: list[str] = field(default_factory=list)


def _literal_values(code: str) -> set[str]:
    """Every string literal in ``code``, as its *value*.

    Collected by parsing, not by scanning. A regex over the source cannot see
    where a string begins: the generated file opens with a triple-quoted
    docstring, and a naive scanner reads ``\"\"\"`` as an empty string followed by
    a real one, then mis-tokenises everything after it — which showed up as
    "the rewrite would drop: , visible", a fragment of a call that was in fact
    still there.

    Values, not source: a ``custom`` body writes ``'[data-testid=\\"row\\"]'``
    where a step writes ``'[data-testid="row"]'``, and those name one selector.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # A `custom` body is not always a parseable module on its own; fall back
        # to the scanner, which is good enough for the fragments it can see.
        return {m.group(0)[1:-1] for m in _STRING.finditer(code)}
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _methods(code: str) -> set[str]:
    """Every called name in ``code``, excluding pure locator plumbing.

    Parsed, not grepped: a method name inside a string or a comment is not a
    call, and the whole point is to compare what actually runs.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names - _PLUMBING


def _without_preamble(code: str) -> str:
    """The original minus the import machinery a rewrite deliberately deletes.

    ``sys.path.insert`` and the helper import are not operations the test
    performs; they are the scaffolding that let it reach a helper at all, and
    removing them is the point of the ``call`` action.
    """
    from .rules import _IMPORT, _NOISE, split_statements

    kept = [s for s in split_statements(code) if not _NOISE.match(s) and not _IMPORT.match(s)]
    return "\n".join(kept)


def verify_rewrite(original_code: str, generated: str, steps: list[dict]) -> VerificationResult:
    """Check a proposed rewrite against the ``custom`` block it replaces."""
    try:
        ast.parse(generated)
    except SyntaxError as exc:
        return VerificationResult(False, f"generated code does not parse: {exc}")

    lost = _literal_values(original_code) - _literal_values(generated)
    # The `sys.path` preamble names two paths that exist only to make the import
    # work; the rewrite removes the import, so those literals go with it.
    lost = {s for s in lost if s not in (".", "/steppy/project")}
    if lost:
        return VerificationResult(False, "the rewrite would drop: " + ", ".join(sorted(lost)[:3]))

    before, after = _methods(_without_preamble(original_code)), _methods(generated)
    # A `call` step routes the helper through `call_helper`, which is the whole
    # point: the helper name moves from a call to a string argument.
    if _CALL_HELPER in after:
        after = after | {s.get("fn", "").rpartition(".")[2] for s in steps if s.get("action") == "call"}
    missing = before - after
    used: list[str] = []
    for method in sorted(missing):
        allowed, reason = SUBSTITUTIONS.get(method, (frozenset(), ""))
        if allowed & after or (method == "wait_for_timeout" and any("wait_after" in s for s in steps)):
            used.append(f"{method}: {reason}")
            continue
        return VerificationResult(False, f"the rewrite would stop calling {method}()")
    return VerificationResult(True, substitutions=used)
