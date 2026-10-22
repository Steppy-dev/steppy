"""Configuration loader for Steppy."""

import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from .models import SteppyConfig


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    pass


# ${VAR} or ${VAR:-default}. Only ASCII env-var names; the optional ``:-default``
# supplies a fallback used when the variable is unset.
_ENV_REF = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win.

    NOTE: intentionally mirrored by ``_deep_merge`` in the rendered
    ``templates/conftest.py.j2`` (and its bundled copy) — that copy is
    standalone runtime code in each generated project and cannot import from
    here. Keep the two in sync if the merge semantics ever change.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _interpolate_env(obj: Any, _warnings: Optional[list] = None) -> Any:
    """Recursively substitute ``${VAR}`` / ``${VAR:-default}`` in every string leaf.

    Resolution per reference: use the env var if set; else the ``:-default`` if
    that form was used; else leave the literal ``${...}`` in place and record a
    warning. Keeps secrets out of committed config — put ``${STEPPY_PW}`` in
    ``config.yaml`` and the real value in the environment.
    """
    if isinstance(obj, dict):
        return {k: _interpolate_env(v, _warnings) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_env(v, _warnings) for v in obj]
    if isinstance(obj, str):

        def _sub(m: "re.Match") -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            if _warnings is not None:
                _warnings.append(
                    f"environment variable ${{{name}}} referenced in config is not "
                    f"set; leaving the literal '{m.group(0)}' in place"
                )
            return m.group(0)

        return _ENV_REF.sub(_sub, obj)
    return obj


def _check_credential_hygiene(raw_config: dict, _warnings: list) -> None:
    """Warn when credentials are committed as inline plaintext in config.yaml.

    Runs on the base (pre-interpolation) config: a username/password that is a
    non-empty literal (not a ``${VAR}`` reference) means a real secret is sitting
    in a file that is typically committed. The gitignored ``credentials.yaml``
    overlay or ``${ENV_VAR}`` references are the safe alternatives.
    """
    apps = raw_config.get("apps")
    if not isinstance(apps, dict):
        return
    for role, app in apps.items():
        if not isinstance(app, dict):
            continue
        creds = app.get("auth", {}).get("credentials") if isinstance(app.get("auth"), dict) else None
        if not isinstance(creds, dict):
            continue
        for field in ("username", "password", "totp_secret"):
            value = creds.get(field)
            if isinstance(value, str) and value and not _ENV_REF.search(value):
                _warnings.append(
                    f"app '{role}' has a plaintext {field} in config.yaml. Prefer a "
                    f"${{ENV_VAR}} reference or the gitignored credentials.yaml overlay "
                    f"(see SECURITY.md)."
                )

    _check_tts_key_hygiene(raw_config, _warnings)


def _check_tts_key_hygiene(raw_config: dict, _warnings: list) -> None:
    """Same rule for the video narration provider's API key.

    A cloud TTS key is a billable credential — a committed one is someone else's
    invoice, not just an access risk.
    """
    video = raw_config.get("video")
    tts = video.get("tts") if isinstance(video, dict) else None
    if not isinstance(tts, dict):
        return
    value = tts.get("api_key")
    if isinstance(value, str) and value and not _ENV_REF.search(value):
        _warnings.append(
            "video.tts.api_key is a plaintext value in config.yaml. Prefer a "
            "${ENV_VAR} reference or the gitignored credentials.yaml overlay "
            "(see SECURITY.md) — a cloud TTS key is billable."
        )


def _check_deprecated_output_keys(raw_config: dict, _warnings: list) -> None:
    """Warn on ``output.reports_dir``, which is accepted but does nothing.

    Kept parseable rather than removed so an existing ``config.yaml`` still
    loads (``SteppyConfig`` forbids extras), but a key that silently does
    nothing is precisely the failure FLAWS §4.6 was about — so it says so once
    per load. ``steppy update --check`` reports the same thing, CI-ably.
    """
    output = raw_config.get("output")
    if isinstance(output, dict) and output.get("reports_dir") is not None:
        _warnings.append(
            "output.reports_dir is deprecated and ignored — nothing writes output/reports. "
            "Playwright traces and videos go to output/traces and output/videos; JUnit and HTML "
            "reports take their paths from report.junit_xml / report.html. Remove the key."
        )


def _emit_warnings(warnings: list) -> None:
    """Print collected config warnings to stderr (config.py has no console)."""
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)


def _load_credentials(config_path: Path) -> Optional[dict]:
    """Load credentials.yaml from the same directory as config_path, if it exists."""
    credentials_path = config_path.parent / "credentials.yaml"
    if not credentials_path.exists():
        return None

    try:
        with open(credentials_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML in {credentials_path}:\n  {e}")
    except Exception as e:
        raise ConfigError(f"Failed to read credentials file: {e}")

    return data if data else None


def load_config(config_path: Optional[Path] = None) -> SteppyConfig:
    """
    Load and validate the Steppy configuration from config.yaml.

    Args:
        config_path: Path to config.yaml. If None, looks for config.yaml in current directory.

    Returns:
        Validated SteppyConfig instance.

    Raises:
        ConfigError: If config file doesn't exist or is invalid.
    """
    if config_path is None:
        config_path = Path.cwd() / "config.yaml"

    if not config_path.exists():
        error_msg = f"Configuration file not found: {config_path}\n"
        error_msg += "\nHint: Create a config.yaml file with your application settings."
        error_msg += "\n      See STEPPY-REFERENCE.md or the README configuration section."
        raise ConfigError(error_msg)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        error_msg = f"Failed to parse YAML in {config_path}:\n"
        error_msg += f"  {e}\n"
        error_msg += "\nHint: Check for syntax errors (indentation, quotes, colons)"
        raise ConfigError(error_msg)
    except Exception as e:
        raise ConfigError(f"Failed to read config file: {e}")

    if raw_config is None:
        error_msg = f"Configuration file is empty: {config_path}\n"
        error_msg += "\nHint: Add your application configuration to config.yaml"
        raise ConfigError(error_msg)

    warnings: list = []
    # Flag plaintext secrets in the committed config *before* they get overlaid
    # or interpolated (so we only warn about what's literally in config.yaml).
    _check_credential_hygiene(raw_config, warnings)
    _check_deprecated_output_keys(raw_config, warnings)

    # Merge credentials from separate file if it exists
    credentials = _load_credentials(config_path)
    if credentials:
        raw_config = _deep_merge(raw_config, credentials)

    # Resolve ${VAR} / ${VAR:-default} references against the environment.
    raw_config = _interpolate_env(raw_config, warnings)

    _emit_warnings(warnings)

    try:
        config = SteppyConfig(**raw_config)
    except ValidationError as e:
        error_msg = f"Configuration validation failed in {config_path}:\n\n"

        # Extract and format validation errors more clearly
        for error in e.errors():
            field = " -> ".join(str(x) for x in error["loc"])
            msg = error["msg"]
            error_msg += f"  Field '{field}': {msg}\n"

        error_msg += "\nHint: Check that all required fields are present and have correct types."
        error_msg += "\n      See STEPPY-REFERENCE.md or the README configuration section."
        raise ConfigError(error_msg)

    return config
