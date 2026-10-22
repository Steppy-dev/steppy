"""CoverageConfig: yaml loading, defaults, typo rejection, settings bridge."""

import pytest
from pydantic import ValidationError

from steppy.coverage.merge import CoverageSettings
from steppy.models import CoverageApiConfig, CoverageConfig, SteppyConfig

_MINIMAL_APPS = {"editor": {"name": "N", "url": "http://localhost:4200", "auth": {"mode": "bypass"}}}


def test_config_without_coverage_block_loads():
    config = SteppyConfig(apps=_MINIMAL_APPS)
    assert config.coverage is None


def test_coverage_block_loads_with_defaults():
    config = SteppyConfig(
        apps=_MINIMAL_APPS,
        coverage={
            "apis": [
                {
                    "name": "editor-api",
                    "openapi": "http://localhost:8080/openapi.json",
                    "base_urls": ["http://localhost:8080"],
                }
            ]
        },
    )
    cov = config.coverage
    assert cov.store_path == "./.steppy/coverage.db"
    assert cov.records_dir == "./output/coverage"
    assert cov.api_resource_types == ["xhr", "fetch"]
    assert cov.module_ignore == ["node_modules/", "webpack/"]
    assert cov.apis[0].name == "editor-api"


def test_coverage_block_rejects_typos():
    with pytest.raises(ValidationError):
        CoverageConfig(openapi_url="http://x")  # not a field — extra=forbid
    with pytest.raises(ValidationError):
        CoverageApiConfig(name="a", openapi="x", base_url="http://x")  # base_urls


def test_coverage_api_requires_non_empty_fields():
    with pytest.raises(ValidationError, match="non-empty"):
        CoverageApiConfig(name="  ", openapi="http://x")


def test_coverage_api_names_must_be_unique():
    with pytest.raises(ValidationError, match="unique"):
        CoverageConfig(
            apis=[
                {"name": "a", "openapi": "x"},
                {"name": "a", "openapi": "y"},
            ]
        )


def test_route_param_patterns_must_compile():
    with pytest.raises(ValidationError, match="invalid coverage.route_param_patterns"):
        CoverageConfig(route_param_patterns=["[unclosed"])


def test_settings_from_config_with_block(tmp_path):
    config = SteppyConfig(
        apps=_MINIMAL_APPS,
        coverage={
            "apis": [{"name": "a", "openapi": "spec.json", "base_urls": ["http://h"]}],
            "sourcemap_dir": "dist",
            "store_path": "./custom/cov.db",
        },
    )
    settings = CoverageSettings.from_config(config, tmp_path)
    assert settings.store_path == tmp_path / "custom" / "cov.db"
    assert settings.records_dir == tmp_path / "output" / "coverage"
    assert settings.sourcemap_dir == tmp_path / "dist"
    assert settings.apis == [{"name": "a", "openapi": "spec.json", "base_urls": ["http://h"]}]


def test_settings_from_config_without_block(tmp_path):
    config = SteppyConfig(apps=_MINIMAL_APPS)
    settings = CoverageSettings.from_config(config, tmp_path)
    assert settings.store_path == tmp_path / ".steppy" / "coverage.db"
    assert settings.records_dir == tmp_path / "output" / "coverage"
    assert settings.apis == []
    assert settings.sourcemap_dir is None
