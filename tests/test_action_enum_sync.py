"""Drift guard: Python's VALID_ACTIONS vs the player's ManifestAction enum.

The set of scenario actions is declared twice, in two languages:

  * ``VALID_ACTIONS``  -- src/steppy/models.py
  * ``ManifestAction`` -- packages/player/src/manifest/schema.ts

Nothing connected them before this test. That asymmetry is dangerous in one
direction: ``ManifestAction`` sits inside a ``.strict()`` ``ManifestStep``, and
``parseManifest`` returns ``{ok: false, code: 'schema_invalid'}`` on any issue.
So an action added on the Python side but forgotten in ``schema.ts`` does not
degrade one step -- it makes the **entire interactive guide fail to render in
the viewer's browser**, while pytest stays green, Vitest stays green, and
``steppy build`` reports success.

The existing Python->TS bridge test (packages/player/test/bridge-from-python.test.ts)
cannot catch it: its fixture manifest contains a single action (``click``).

Mirrors the house pattern in tests/test_config_schema.py.
"""

import re
from pathlib import Path

import pytest

from steppy.models import VALID_ACTIONS

_SCHEMA_TS = Path(__file__).resolve().parents[1] / "packages" / "player" / "src" / "manifest" / "schema.ts"

# `auth0-login` is a deprecated alias that models.py normalizes to `login`
# before validation (FLAWS §6.2), so Python can never emit it. The player still
# accepts it so that a manifest built by an older Steppy keeps loading.
_TS_ONLY_LEGACY = {"auth0-login"}


def _parse_manifest_action_enum() -> set[str]:
    """Extract the member list of `const ManifestAction = z.enum([...])`."""
    src = _SCHEMA_TS.read_text(encoding="utf-8")
    match = re.search(r"const\s+ManifestAction\s*=\s*z\.enum\(\s*\[(.*?)\]\s*\)", src, re.DOTALL)
    assert match, f"could not locate `const ManifestAction = z.enum([...])` in {_SCHEMA_TS}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_schema_ts_is_present():
    assert _SCHEMA_TS.is_file(), f"missing {_SCHEMA_TS}"


def test_every_python_action_is_accepted_by_the_player():
    """The direction that breaks production if it drifts."""
    ts_actions = _parse_manifest_action_enum()
    missing = sorted(VALID_ACTIONS - ts_actions)
    assert not missing, (
        f"Actions valid in Python but rejected by the player: {missing}.\n"
        f"Add them to the ManifestAction enum in {_SCHEMA_TS.name}, then rebuild and "
        "re-vendor the player bundle:\n"
        "  npm --prefix packages/player run build\n"
        "  (copy dist/ artifacts to src/steppy/_player_bundle/)\n"
        "Without this the player rejects the whole manifest and the interactive "
        "guide renders nothing -- with every test suite still green."
    )


def test_player_accepts_no_unknown_actions():
    """The reverse direction -- a stale entry no generator can ever emit."""
    ts_actions = _parse_manifest_action_enum()
    extra = sorted(ts_actions - VALID_ACTIONS - _TS_ONLY_LEGACY)
    assert not extra, (
        f"ManifestAction lists actions Python cannot emit: {extra}. "
        f"Either add them to VALID_ACTIONS in models.py, remove them from "
        f"{_SCHEMA_TS.name}, or record them in _TS_ONLY_LEGACY with a reason."
    )


def test_legacy_alias_is_still_accepted_by_the_player():
    """`auth0-login` must stay in the TS enum: old manifests still carry it."""
    assert _TS_ONLY_LEGACY <= _parse_manifest_action_enum()


def test_deprecated_alias_normalizes_and_is_not_in_valid_actions():
    """Pin why `auth0-login` is legitimately absent from VALID_ACTIONS."""
    from steppy.models import Step

    assert "auth0-login" not in VALID_ACTIONS
    assert Step(action="auth0-login", role="admin").action == "login"


@pytest.mark.parametrize("action", sorted(VALID_ACTIONS))
def test_each_action_name_is_a_plain_slug(action: str):
    """Guards the regex above: quoting/format assumptions stay valid."""
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", action), action


# ── Cursor-table coverage ───────────────────────────────────────────────────
#
# The third surface an action has to reach (after models.py and schema.ts) is
# the guide cursor. Unlike the other two, a gap here is *silent*: an action
# absent from every cursor branch falls through `_SIMPLE_CURSOR.get(action)` to
# `return None`, which is indistinguishable from an action that deliberately
# has no cursor (navigate, assert, wait...). So the step renders with
# `cursor: null` and `interactive: false`, the manifest validates, the build
# reports success, and nobody finds out until they watch the published guide.


def _cursor_covered_actions() -> set[str]:
    """Every action the cursor layer knowingly handles.

    `fill-form` has its own branch in `_compute_cursor_behavior`; `custom` and
    `call` share one, because neither has a selector the cursor could target —
    both honour a scenario-authored `guide-cursor:` and show nothing without
    one. Everything else is a table row.
    """
    from steppy.guide import _HIDDEN_CURSOR_ACTIONS, _SIMPLE_CURSOR

    return set(_SIMPLE_CURSOR) | set(_HIDDEN_CURSOR_ACTIONS) | {"custom", "call", "fill-form"}


def test_every_action_has_a_deliberate_cursor_decision():
    missing = sorted(VALID_ACTIONS - _cursor_covered_actions())
    assert not missing, (
        f"Actions with no cursor decision: {missing}.\n"
        "Add each to exactly one of (src/steppy/guide.py):\n"
        "  * _SIMPLE_CURSOR        — it animates like a click/type/hover\n"
        "  * _HIDDEN_CURSOR_ACTIONS — it is non-visual (no cursor by design)\n"
        "  * a bespoke branch in _compute_cursor_behavior (only fill-form needs one)\n"
        "Without this the step silently renders with cursor: null and "
        "interactive: false — every test suite stays green."
    )


def test_cursor_tables_list_no_unknown_actions():
    """The reverse: a stale row for an action that no longer exists."""
    extra = sorted(_cursor_covered_actions() - VALID_ACTIONS)
    assert not extra, (
        f"Cursor tables reference actions absent from VALID_ACTIONS: {extra}. "
        "Remove the stale rows, or add the actions to models.py."
    )
