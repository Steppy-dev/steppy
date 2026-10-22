"""Every action is classified for in-app tours.

``steppy.tours.actions.ACTION_TOUR_KIND`` decides what each scenario action
becomes in a tour. An action added to ``models.VALID_ACTIONS`` without a row
here would raise a ``KeyError`` at compile time — or, worse, be silently left
out of every tour if someone "fixed" that with a default. This test makes the
new row part of the "Adding a new action" checklist (CLAUDE.md, step 7).
"""

from steppy.models import VALID_ACTIONS
from steppy.tours.actions import ACTION_TOUR_KIND, PAGE_NEUTRAL_ACTIONS

KINDS = {"click", "change", "input", "fill-form", "upload", "check", "cursor", "unguided", "navigation", "skip"}


def test_every_action_has_exactly_one_tour_kind():
    assert set(ACTION_TOUR_KIND) == VALID_ACTIONS, (
        f"missing: {sorted(VALID_ACTIONS - set(ACTION_TOUR_KIND))}, "
        f"unknown: {sorted(set(ACTION_TOUR_KIND) - VALID_ACTIONS)}"
    )


def test_kinds_are_from_the_documented_set():
    assert set(ACTION_TOUR_KIND.values()) <= KINDS


def test_page_neutral_actions_are_real_actions_that_never_act():
    # A renamed action would silently start dropping gates; one that acts would keep stale ones.
    assert PAGE_NEUTRAL_ACTIONS <= VALID_ACTIONS
    assert {ACTION_TOUR_KIND[a] for a in PAGE_NEUTRAL_ACTIONS} <= {"check", "skip"}
