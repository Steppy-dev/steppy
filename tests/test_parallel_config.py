"""Pydantic validation for the parallel-mode config additions.

Covers:
  - AppConfig.internal_url is optional
  - SteppyConfig.parallel is optional
  - ParallelConfig defaults are sensible
  - workers accepts int >= 1 or 'auto' (and rejects 0/negative)
  - extra fields under parallel are rejected
"""

import pytest
from pydantic import ValidationError

from steppy.models import AppConfig, ParallelConfig, PreAuthConfig, SteppyConfig


def _minimal_app(internal_url: str | None = None) -> dict:
    out = {
        "name": "Test",
        "url": "http://localhost:4200",
        "auth": {"mode": "auth0", "credentials": {"username": "u", "password": "p"}},
    }
    if internal_url is not None:
        out["internal_url"] = internal_url
    return out


def test_app_config_internal_url_optional():
    AppConfig(**_minimal_app())  # no internal_url


def test_app_config_internal_url_accepted():
    cfg = AppConfig(**_minimal_app(internal_url="http://app:3000"))
    assert cfg.internal_url == "http://app:3000"


def test_steppy_config_parallel_optional():
    cfg = SteppyConfig(apps={"editor": _minimal_app()})
    assert cfg.parallel is None


def test_parallel_config_defaults():
    cfg = ParallelConfig()
    assert cfg.workers == 1
    assert cfg.compose_file == "docker-compose.yml"
    assert cfg.worker_image == "steppy-worker"
    assert cfg.healthcheck_timeout == 120
    assert cfg.build_image is True


def test_parallel_config_workers_int():
    assert ParallelConfig(workers=4).workers == 4


def test_parallel_config_workers_auto():
    assert ParallelConfig(workers="auto").workers == "auto"


def test_parallel_config_rejects_zero_workers():
    with pytest.raises(ValidationError):
        ParallelConfig(workers=0)


def test_parallel_config_rejects_negative_workers():
    with pytest.raises(ValidationError):
        ParallelConfig(workers=-1)


def test_parallel_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ParallelConfig(workers=2, mystery_field="oops")


def test_steppy_config_with_parallel_block():
    cfg = SteppyConfig(
        apps={"editor": _minimal_app(internal_url="http://editor-app:3000")},
        parallel={
            "workers": 3,
            "compose_file": "docker-compose.steppy.yml",
            "worker_image": "custom-worker",
            "healthcheck_timeout": 60,
            "build_image": False,
        },
    )
    assert cfg.parallel is not None
    assert cfg.parallel.workers == 3
    assert cfg.parallel.compose_file == "docker-compose.steppy.yml"
    assert cfg.parallel.worker_image == "custom-worker"
    assert cfg.parallel.healthcheck_timeout == 60
    assert cfg.parallel.build_image is False


# ── pre_auth ─────────────────────────────────────────────────────────────────


def test_parallel_pre_auth_default_none():
    assert ParallelConfig().pre_auth is None


def test_pre_auth_string_command():
    cfg = ParallelConfig(pre_auth={"command": "python auth_login.py --internal"})
    assert cfg.pre_auth is not None
    assert cfg.pre_auth.command == "python auth_login.py --internal"
    assert cfg.pre_auth.timeout == 600  # default
    assert cfg.pre_auth.image is None


def test_pre_auth_list_command_and_overrides():
    cfg = ParallelConfig(
        pre_auth={
            "command": ["python", "auth_login.py", "--internal"],
            "timeout": 1200,
            "image": "my/preauth:tag",
        }
    )
    assert cfg.pre_auth.command == ["python", "auth_login.py", "--internal"]
    assert cfg.pre_auth.timeout == 1200
    assert cfg.pre_auth.image == "my/preauth:tag"


def test_pre_auth_rejects_empty_string_command():
    with pytest.raises(ValidationError):
        PreAuthConfig(command="   ")


def test_pre_auth_rejects_empty_list_command():
    with pytest.raises(ValidationError):
        PreAuthConfig(command=[])


def test_pre_auth_rejects_zero_timeout():
    with pytest.raises(ValidationError):
        PreAuthConfig(command="x", timeout=0)


def test_pre_auth_rejects_extra_fields():
    with pytest.raises(ValidationError):
        PreAuthConfig(command="x", mystery="oops")
