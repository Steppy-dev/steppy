"""Machine-readable deprecation registry (FLAWS §7.7 prerequisite).

This is the seed data behind a future ``steppy update --check`` command (FLAWS
§7.7): a per-version list of things that were renamed or deprecated, so a project
can be scanned against the installed Steppy version and told, concretely, what
will stop working and when — instead of a CHANGELOG paragraph.

The command itself is deliberately deferred to the first post-launch 0.x (FLAWS
§9). Only this data ships now, because the entries must be recorded *as the
deprecations happen* — retrofitting a "what changed in 0.4" registry after the
fact is exactly the ambiguity §6.4's version field exists to avoid.

Each entry is intentionally declarative (no live matcher callables yet): the
``matches`` field describes, in prose, what a scanner should look for. When the
``update`` command lands it will grow real matchers keyed off these entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class Deprecation:
    """One deprecated spelling and its replacement.

    Attributes:
        id: Stable slug (used as the future ``--check`` message anchor).
        introduced_in: Steppy version that introduced the replacement.
        removed_in: Version the deprecated form is scheduled to stop working, or
            ``None`` for "kept-forever" aliases.
        surface: Where the deprecated form appears (``scenario`` | ``config``).
        matches: Prose description of what a scanner should flag.
        message: User-facing guidance printed by ``steppy update --check``.
        severity: How loud to be while the alias still works.
        auto_fixable: Whether a future ``--migrate`` could rewrite it mechanically.
    """

    id: str
    introduced_in: str
    removed_in: str | None
    surface: Literal["scenario", "config"]
    matches: str
    message: str
    severity: Severity
    auto_fixable: bool


# The three §6 aliases, normalized transparently at load time today (see
# models.py), plus the §4.6 inert `output.reports_dir`. All are "info": the
# aliases still resolve and the dead key still parses, so nothing breaks yet.
DEPRECATIONS: tuple[Deprecation, ...] = (
    Deprecation(
        id="auth-mode-auth0-to-form",
        introduced_in="0.4.0",
        removed_in=None,  # kept-forever alias
        surface="config",
        matches="apps.<role>.auth.mode == 'auth0'",
        message=(
            "auth mode 'auth0' was renamed to 'form' (the form-fill works against "
            "any IdP, not just Auth0). The 'auth0' spelling is a kept-forever alias."
        ),
        severity="info",
        auto_fixable=True,
    ),
    Deprecation(
        id="auth-global-key-auth0-to-form",
        introduced_in="0.4.0",
        removed_in=None,
        surface="config",
        matches="top-level auth.auth0: block (should be auth.form:)",
        message=(
            "the global 'auth.auth0:' block was renamed to 'auth.form:'. The old "
            "key is still accepted and copied to 'form' when 'form' is absent."
        ),
        severity="info",
        auto_fixable=True,
    ),
    Deprecation(
        id="output-reports-dir-inert",
        introduced_in="0.4.0",
        removed_in="1.0.0",
        surface="config",
        matches="output.reports_dir",
        message=(
            "output.reports_dir is deprecated and ignored — nothing writes output/reports. "
            "Traces and videos go to output/traces and output/videos; JUnit and HTML reports "
            "take their paths from report.junit_xml / report.html. Remove the key "
            "(output.screenshots_dir and output.guides_dir are live and unaffected)."
        ),
        severity="info",
        auto_fixable=True,
    ),
    Deprecation(
        id="action-auth0-login-to-login",
        introduced_in="0.4.0",
        removed_in=None,
        surface="scenario",
        matches="step action 'auth0-login'",
        message=(
            "the step action 'auth0-login' was renamed to 'login'. The 'auth0-login' spelling is a kept-forever alias."
        ),
        severity="info",
        auto_fixable=True,
    ),
)
