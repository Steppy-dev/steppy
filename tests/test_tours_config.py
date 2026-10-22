"""``config.yaml``'s ``tours:`` block (``ToursConfig``) and ``output.tours_dir``."""

import pytest
from pydantic import ValidationError

from steppy.models import SteppyConfig

APP = {"editor": {"name": "Invoices", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}}
TARGETS = [{"type": "app-assets", "path": "../invoice-app/public/steppy/tours"}, {"type": "guide"}]


def _config(tours=None, **extra):
    data = {"apps": APP, "locales": ["en", "fr"], "default_locale": "en", **extra}
    if tours is not None:
        data["tours"] = tours
    return SteppyConfig(**data)


def test_an_absent_block_is_none_and_the_tours_dir_has_a_default():
    config = _config()
    assert config.tours is None
    assert config.output.tours_dir == "./output/tours"


def test_defaults_when_enabled():
    tours = _config({"enabled": True, "targets": TARGETS}).tours
    assert tours.default is False and tours.replay is True
    assert tours.build_id == "git-sha" and tours.test_id_attribute == "data-testid"
    assert [t.type for t in tours.targets] == ["app-assets", "guide"]


def test_a_disabled_block_needs_no_targets():
    assert _config({"enabled": False}).tours.targets == []


@pytest.mark.parametrize(
    "tours, match",
    [
        ({"enabled": True}, "at least one target"),
        ({"enabled": True, "targets": [{"type": "guide"}, {"type": "guide"}]}, "more than one 'guide'"),
        (
            {
                "enabled": True,
                "targets": [{"type": "app-assets", "path": "a/b"}, {"type": "app-assets", "path": "a/./b"}],
            },
            "same app-assets path",
        ),
        ({"enabled": True, "targets": [{"type": "cdn"}]}, "type"),
        ({"enabled": True, "targets": [{"type": "app-assets"}]}, "path"),
        ({"enabled": True, "targets": [{"type": "app-assets", "path": " "}]}, "non-empty"),
        ({"enbled": True}, "Extra inputs"),
        ({"test_id_attribute": "id"}, "test-id attribute"),
        ({"build_id": "../x"}, "build id"),
        ({"locales": []}, "non-empty"),
        ({"locales": ["en", "EN"]}, "twice"),
        ({"locales": ["en", "x-private"]}, ""),
        ({"guide_base_url": "ftp://guide.example.com"}, "http"),
        ({"guide_base_url": "https://guide.example.com/?x=1"}, "query"),
        ({"guide_base_url": "https://guiïde.example.com"}, "punycode"),
        ({"layouts": {}}, "at least one layout"),
        ({"layouts": {"desktop": "(min-width: 768px); color: red{"}}, "media query"),
    ],
)
def test_invalid_blocks_are_rejected(tours, match):
    with pytest.raises(ValidationError, match=match):
        _config(tours)


def test_an_unresolved_env_build_id_loads_and_fails_only_when_used():
    assert _config({"build_id": "${APP_BUILD_ID}"}).tours.build_id == "${APP_BUILD_ID}"


def test_guide_base_url_is_normalised_to_a_trailing_slash():
    assert _config({"guide_base_url": "https://guide.example.com/docs"}).tours.guide_base_url == (
        "https://guide.example.com/docs/"
    )


def test_the_tour_default_locale_must_be_a_tour_locale():
    with pytest.raises(ValidationError, match="default locale"):
        _config({"locales": ["fr"]})  # project default 'en' is not among the tour locales
    assert _config({"locales": ["fr"], "default_locale": "fr"}).tours.locales == ["fr"]


def test_tour_layouts_must_name_screenshot_layouts():
    layouts = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
    ok = _config(
        {"layouts": {"desktop": "(min-width: 768px)", "mobile": "(max-width: 767.98px)"}},
        screenshot={"layouts": layouts},
    )
    assert set(ok.tours.layouts) == {"desktop", "mobile"}
    with pytest.raises(ValidationError, match="screenshot.layouts"):
        _config({"layouts": {"tablet": "(min-width: 600px)"}}, screenshot={"layouts": layouts})


def test_output_tours_dir_rejects_typos():
    with pytest.raises(ValidationError):
        _config(output={"tour_dir": "x"})


def test_an_inherited_locale_list_is_checked_when_tours_are_on():
    # Each tour locale names a file: `en` and `EN` would be one file on NTFS/APFS.
    on = {"enabled": True, "targets": TARGETS}
    with pytest.raises(ValidationError, match=r"locales \(inherited by tours\) lists the same locale twice"):
        _config(on, locales=["en", "EN"])
    many = [f"en-{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(33)]
    with pytest.raises(ValidationError, match="at most 32"):
        _config(on, locales=["en", *many])
    # Off, the block is inert: the project's own locale list stays its business.
    assert _config({"enabled": False}, locales=["en", "EN"]).tours.enabled is False
