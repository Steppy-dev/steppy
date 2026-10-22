"""Tests for TOTP/MFA automated login (FLAWS §6.2).

Steppy computes the one-time code with a stdlib RFC 6238 helper (no ``pyotp``)
so the generated ``conftest.py.j2`` stays self-contained. These pin:
- ``_totp_now`` against the RFC 6238 Appendix-B SHA1 test vectors,
- the model fields (``FormAuthConfig.totp_selector`` / ``AuthCredentials.totp_secret``)
  and the IdP-preset backfill,
- ``auth.py``'s ``_fill_totp_if_configured`` behavior on a fake page,
- and that the template embeds a byte-identical copy of ``_totp_now`` + the step.
"""

import inspect
import types
from base64 import b32encode
from pathlib import Path

import pytest

import steppy.totp as totp_mod
from steppy import auth as auth_mod
from steppy.totp import _totp_now
from steppy.models import AuthCredentials, FormAuthConfig

_CONFTEST_SRC = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"
_AUTH_SRC = Path(__file__).resolve().parents[1] / "src" / "steppy" / "auth.py"

# RFC 6238 Appendix B seed for SHA1: the ASCII string "12345678901234567890".
_SEED = b32encode(b"12345678901234567890").decode()


# ── RFC 6238 known-answer vectors ────────────────────────────────────────────


@pytest.mark.parametrize(
    "t, expected",
    [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ],
)
def test_totp_now_rfc6238_vectors(monkeypatch, t, expected):
    monkeypatch.setattr(totp_mod, "time", types.SimpleNamespace(time=lambda: t))
    assert _totp_now(_SEED) == expected


def test_totp_now_tolerates_lowercase_and_spaces(monkeypatch):
    monkeypatch.setattr(totp_mod, "time", types.SimpleNamespace(time=lambda: 59))
    spaced = " ".join(_SEED[i : i + 4] for i in range(0, len(_SEED), 4)).lower()
    assert _totp_now(spaced) == "287082"


# ── model fields + preset backfill ───────────────────────────────────────────


def test_form_config_totp_fields_default_none():
    fc = FormAuthConfig()
    assert fc.totp_selector is None
    assert fc.totp_submit_selector is None


def test_credentials_totp_secret_default_none():
    creds = AuthCredentials(username="u", password="p")
    assert creds.totp_secret is None


def test_keycloak_preset_fills_totp_selector():
    assert FormAuthConfig(preset="keycloak").totp_selector == "#otp"


def test_explicit_totp_selector_wins_over_preset():
    fc = FormAuthConfig(preset="keycloak", totp_selector="#my-otp")
    assert fc.totp_selector == "#my-otp"


# ── auth.py _fill_totp_if_configured (fake page) ─────────────────────────────


class _FakePage:
    def __init__(self, prompt_present=True):
        self.calls = []
        self._prompt_present = prompt_present

    def wait_for_selector(self, selector, **kwargs):
        self.calls.append(("wait", selector))
        if not self._prompt_present:
            raise RuntimeError("no such selector (simulated trusted device)")

    def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    def click(self, selector):
        self.calls.append(("click", selector))


def test_fill_totp_fills_code_and_submits(monkeypatch):
    monkeypatch.setattr(totp_mod, "time", types.SimpleNamespace(time=lambda: 59))
    page = _FakePage()
    form = FormAuthConfig(totp_selector="#otp")
    creds = AuthCredentials(username="u", password="p", totp_secret=_SEED)
    auth_mod._fill_totp_if_configured(page, form, creds)
    fills = [c for c in page.calls if c[0] == "fill"]
    assert fills == [("fill", "#otp", "287082")]
    # falls back to submit_selector when no totp_submit_selector
    assert ("click", form.submit_selector) in page.calls


def test_fill_totp_noop_without_secret():
    page = _FakePage()
    form = FormAuthConfig(totp_selector="#otp")
    creds = AuthCredentials(username="u", password="p")  # no totp_secret
    auth_mod._fill_totp_if_configured(page, form, creds)
    assert page.calls == []


def test_fill_totp_noop_without_selector():
    page = _FakePage()
    form = FormAuthConfig()  # no totp_selector
    creds = AuthCredentials(username="u", password="p", totp_secret=_SEED)
    auth_mod._fill_totp_if_configured(page, form, creds)
    assert page.calls == []


def test_fill_totp_tolerates_missing_prompt(monkeypatch):
    monkeypatch.setattr(totp_mod, "time", types.SimpleNamespace(time=lambda: 59))
    page = _FakePage(prompt_present=False)
    form = FormAuthConfig(totp_selector="#otp")
    creds = AuthCredentials(username="u", password="p", totp_secret=_SEED)
    auth_mod._fill_totp_if_configured(page, form, creds)
    # waited, but never filled/clicked (no MFA this session)
    assert [c for c in page.calls if c[0] in ("fill", "click")] == []


def test_fill_totp_honors_custom_submit_selector(monkeypatch):
    monkeypatch.setattr(totp_mod, "time", types.SimpleNamespace(time=lambda: 59))
    page = _FakePage()
    form = FormAuthConfig(totp_selector="#otp", totp_submit_selector="#otp-submit")
    creds = AuthCredentials(username="u", password="p", totp_secret=_SEED)
    auth_mod._fill_totp_if_configured(page, form, creds)
    assert ("click", "#otp-submit") in page.calls


# ── template parity + source-grep guards ─────────────────────────────────────


def test_template_embeds_identical_totp_now():
    """The generated conftest can't import steppy, so it embeds a copy of
    _totp_now — assert it is byte-identical to the canonical stdlib source."""
    canonical = inspect.getsource(_totp_now)
    assert canonical in _CONFTEST_SRC.read_text(encoding="utf-8")


def test_auth_and_conftest_contain_totp_step():
    assert "_fill_totp_if_configured" in _AUTH_SRC.read_text(encoding="utf-8")
    conftest = _CONFTEST_SRC.read_text(encoding="utf-8")
    assert "_fill_totp(page, form" in conftest
    assert "def _fill_totp(" in conftest
