"""Tests for the auth reframe (FLAWS §6.2): auth0->form rename, IdP presets,
and token `header` injection."""

from pathlib import Path

from steppy.auth import authenticate_token
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    Auth0Config,
    FormAuthConfig,
    GlobalAuthConfig,
    SteppyConfig,
    TokenConfig,
    Step,
)

_CONFTEST_SRC = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


# ── mode / config rename ─────────────────────────────────────────────────────


def test_auth0_config_alias_is_form_config():
    assert Auth0Config is FormAuthConfig


def test_mode_auth0_normalizes_to_form():
    assert AppAuthConfig(mode="auth0").mode == "form"
    assert AppAuthConfig(mode="form").mode == "form"


def test_global_legacy_auth0_key_maps_to_form():
    g = GlobalAuthConfig(auth0={"username_selector": "#legacy"})
    assert g.form is not None
    assert g.form.username_selector == "#legacy"


def test_global_form_key_preferred():
    g = GlobalAuthConfig(form={"username_selector": "#new"})
    assert g.form.username_selector == "#new"


# ── IdP presets ──────────────────────────────────────────────────────────────


def test_keycloak_preset_fills_selectors():
    f = FormAuthConfig(preset="keycloak")
    assert f.username_selector == "#username"
    assert f.password_selector == "#password"
    assert f.submit_selector == "#kc-login"


def test_explicit_selector_wins_over_preset():
    f = FormAuthConfig(preset="keycloak", username_selector="#custom")
    assert f.username_selector == "#custom"
    assert f.password_selector == "#password"  # backfilled


def test_unknown_preset_errors():
    import pytest

    with pytest.raises(Exception) as exc:
        FormAuthConfig(preset="nope")
    assert "keycloak" in str(exc.value)


# ── login action alias ───────────────────────────────────────────────────────


def test_login_action_and_auth0_login_alias():
    assert Step(action="login", role="editor").action == "login"
    assert Step(action="auth0-login", role="editor").action == "login"


# ── token header injection ───────────────────────────────────────────────────


class _FakeContext:
    def __init__(self):
        self.headers = None

    def set_extra_http_headers(self, headers):
        self.headers = headers


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()

    def new_context(self, **kwargs):
        return self.context


def test_token_header_injection(monkeypatch):
    """`inject_as: header` sets a default request header instead of raising."""
    import steppy.auth as auth_mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "abc123"}

    monkeypatch.setattr(auth_mod.httpx, "request", lambda **k: _Resp())

    app = AppConfig(
        name="Editor",
        url="http://localhost:4200",
        auth=AppAuthConfig(
            mode="token",
            token=TokenConfig(endpoint="http://idp/token", inject_as="header", key="Authorization"),
        ),
    )
    config = SteppyConfig(apps={"editor": app})
    browser = _FakeBrowser()

    ctx = authenticate_token(browser, "editor", app, config)
    assert ctx.headers == {"Authorization": "abc123"}


def test_header_injection_no_longer_raises_not_implemented():
    src = (Path(__file__).resolve().parents[1] / "src" / "steppy" / "auth.py").read_text(encoding="utf-8")
    assert "Header injection is not yet implemented" not in src
    assert "set_extra_http_headers" in src


# ── generated conftest carries token/header dispatch ─────────────────────────


def test_conftest_template_dispatches_token_and_header():
    src = _CONFTEST_SRC.read_text(encoding="utf-8")
    assert 'mode == "token"' in src
    assert "_authenticate_token" in src
    assert "set_extra_http_headers" in src
    # form + legacy auth0 both accepted at dispatch.
    assert 'mode in ("form", "auth0")' in src
