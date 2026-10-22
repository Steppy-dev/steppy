"""Tests for FLAWS §3.1 — credential hygiene.

`config.yaml` should never carry plaintext secrets. Steppy resolves
`${VAR}` / `${VAR:-default}` references against the environment (so secrets live
in the env or the gitignored `credentials.yaml` overlay) and warns when it finds
a plaintext credential literal in the committed config.
"""

from steppy.config import (
    _check_credential_hygiene,
    _interpolate_env,
    load_config,
)


# ── _interpolate_env ─────────────────────────────────────────────────────────


def test_interpolate_simple(monkeypatch):
    monkeypatch.setenv("STEPPY_FOO", "bar")
    assert _interpolate_env("x-${STEPPY_FOO}-y") == "x-bar-y"


def test_interpolate_default_used_when_unset(monkeypatch):
    monkeypatch.delenv("STEPPY_MISSING_X", raising=False)
    assert _interpolate_env("${STEPPY_MISSING_X:-fallback}") == "fallback"


def test_interpolate_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("STEPPY_BAR", "real")
    assert _interpolate_env("${STEPPY_BAR:-fallback}") == "real"


def test_interpolate_missing_no_default_left_literal_and_warns(monkeypatch):
    monkeypatch.delenv("STEPPY_NOPE", raising=False)
    warnings: list = []
    assert _interpolate_env("${STEPPY_NOPE}", warnings) == "${STEPPY_NOPE}"
    assert any("STEPPY_NOPE" in w for w in warnings)


def test_interpolate_recurses_dicts_and_lists(monkeypatch):
    monkeypatch.setenv("STEPPY_U", "user")
    obj = {"a": ["${STEPPY_U}", 1, True], "b": {"c": "${STEPPY_U}"}}
    assert _interpolate_env(obj) == {"a": ["user", 1, True], "b": {"c": "user"}}


def test_interpolate_leaves_non_strings_untouched():
    assert _interpolate_env(42) == 42
    assert _interpolate_env(None) is None


# ── _check_credential_hygiene ────────────────────────────────────────────────


def test_hygiene_warns_on_plaintext_credentials():
    warnings: list = []
    raw = {"apps": {"editor": {"auth": {"credentials": {"username": "u@example.com", "password": "pw"}}}}}
    _check_credential_hygiene(raw, warnings)
    joined = "\n".join(warnings)
    assert "editor" in joined and "plaintext" in joined
    assert "username" in joined and "password" in joined


def test_hygiene_silent_on_env_refs():
    warnings: list = []
    raw = {"apps": {"editor": {"auth": {"credentials": {"username": "${STEPPY_U}", "password": "${STEPPY_P}"}}}}}
    _check_credential_hygiene(raw, warnings)
    assert warnings == []


def test_hygiene_silent_when_no_credentials():
    warnings: list = []
    raw = {"apps": {"editor": {"auth": {"mode": "auth0"}}}}
    _check_credential_hygiene(raw, warnings)
    assert warnings == []


# ── load_config end-to-end ───────────────────────────────────────────────────


_MINIMAL_CONFIG = """
apps:
  editor:
    name: "Editor"
    url: "http://localhost:4200"
    auth:
      mode: auth0
      credentials:
        username: "${STEPPY_TEST_USER}"
        password: "${STEPPY_TEST_PW}"
"""


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_config_resolves_env_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("STEPPY_TEST_USER", "alice@example.com")
    monkeypatch.setenv("STEPPY_TEST_PW", "s3cret")
    config = load_config(_write(tmp_path, _MINIMAL_CONFIG))
    assert config.apps["editor"].auth.credentials.username == "alice@example.com"
    assert config.apps["editor"].auth.credentials.password == "s3cret"


def test_load_config_default_url(tmp_path, monkeypatch):
    monkeypatch.delenv("STEPPY_APP_URL", raising=False)
    text = _MINIMAL_CONFIG.replace(
        'url: "http://localhost:4200"',
        'url: "${STEPPY_APP_URL:-http://localhost:9999}"',
    )
    config = load_config(_write(tmp_path, text))
    assert config.apps["editor"].url == "http://localhost:9999"


def test_load_config_warns_on_plaintext(tmp_path, monkeypatch, capsys):
    text = _MINIMAL_CONFIG.replace('"${STEPPY_TEST_USER}"', '"real@example.com"')
    load_config(_write(tmp_path, text))
    err = capsys.readouterr().err
    assert "WARNING" in err and "plaintext" in err


def test_load_config_no_warning_for_env_refs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STEPPY_TEST_USER", "a@x.com")
    monkeypatch.setenv("STEPPY_TEST_PW", "pw")
    load_config(_write(tmp_path, _MINIMAL_CONFIG))
    err = capsys.readouterr().err
    assert "plaintext" not in err
