"""Well-formedness guard for the deprecation registry (FLAWS §7.7 prereq)."""

from steppy.compat import DEPRECATIONS, Deprecation


def test_registry_is_well_formed():
    assert DEPRECATIONS, "registry should not be empty"
    seen_ids = set()
    for d in DEPRECATIONS:
        assert isinstance(d, Deprecation)
        assert d.id and d.id not in seen_ids, f"duplicate/empty id: {d.id!r}"
        seen_ids.add(d.id)
        assert d.introduced_in
        assert d.surface in ("scenario", "config")
        assert d.severity in ("info", "warning", "error")
        assert d.message and d.matches
        assert isinstance(d.auto_fixable, bool)


def test_known_section6_aliases_present():
    ids = {d.id for d in DEPRECATIONS}
    # The three live §6 aliases must be recorded as the first registry entries.
    assert "auth-mode-auth0-to-form" in ids
    assert "auth-global-key-auth0-to-form" in ids
    assert "action-auth0-login-to-login" in ids
