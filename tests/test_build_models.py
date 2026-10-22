"""Unit tests for build_models module."""

import pytest
from pydantic import ValidationError

from steppy.build_models import (
    ONBOARDING_SLUG,
    BuildConfig,
    BuildTarget,
    CursorMode,
    GuideEntry,
    GuideMode,
    NavbarConfig,
    SeoMode,
    SiteConfig,
)


def _valid_config(**overrides) -> dict:
    """Return a minimal valid config dict, with optional overrides."""
    base = {
        "scenariosRoot": "./scenarios",
        "outputDir": "./src/app/features/guides/generated",
        "assetsDir": "./public/guides",
        "role": "editor",
        "locales": ["fr"],
        "site": {"title": "Test Guides"},
        "guides": [
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
        ],
        "navbar": {"order": ["onboarding"]},
    }
    base.update(overrides)
    return base


class TestSeoConfig:
    def test_defaults_to_private(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.seo.mode == SeoMode.PRIVATE
        assert cfg.seo.base_url is None
        assert cfg.seo.open_graph is False
        assert cfg.seo.description is None

    def test_public_requires_base_url(self):
        with pytest.raises(ValidationError, match="baseUrl is required"):
            BuildConfig(**_valid_config(seo={"mode": "public"}))

    def test_public_with_base_url_ok(self):
        cfg = BuildConfig(**_valid_config(seo={"mode": "public", "baseUrl": "https://x.example.com"}))
        assert cfg.seo.mode == SeoMode.PUBLIC
        assert cfg.seo.base_url == "https://x.example.com"

    def test_base_url_trailing_slash_stripped(self):
        cfg = BuildConfig(**_valid_config(seo={"mode": "public", "baseUrl": "https://x.example.com/"}))
        assert cfg.seo.base_url == "https://x.example.com"

    def test_base_url_must_be_http(self):
        with pytest.raises(ValidationError, match="absolute http"):
            BuildConfig(**_valid_config(seo={"mode": "public", "baseUrl": "guides.example.com"}))

    def test_open_graph_and_description_camel_case(self):
        cfg = BuildConfig(
            **_valid_config(
                seo={
                    "mode": "public",
                    "baseUrl": "https://x.example.com",
                    "openGraph": True,
                    "description": {"fr": "Bonjour"},
                }
            )
        )
        assert cfg.seo.open_graph is True
        assert cfg.seo.description == {"fr": "Bonjour"}

    def test_empty_description_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(
                **_valid_config(
                    seo={
                        "mode": "public",
                        "baseUrl": "https://x.example.com",
                        "description": "",
                    }
                )
            )

    def test_private_with_base_url_is_fine(self):
        # baseUrl on a private config is harmless (no absolute URLs are emitted).
        cfg = BuildConfig(**_valid_config(seo={"mode": "private", "baseUrl": "https://x.example.com"}))
        assert cfg.seo.mode == SeoMode.PRIVATE


class TestGuideEntryDescription:
    def test_guide_description_optional(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.guides[0].description is None

    def test_guide_description_localized(self):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "description": {"fr": "Prise en main", "nl": "Aan de slag"},
                    },
                ]
            )
        )
        assert cfg.guides[0].description == {"fr": "Prise en main", "nl": "Aan de slag"}


class TestBuildConfigParsing:
    def test_minimal_valid_config(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.scenarios_root == "./scenarios"
        assert cfg.output_dir == "./src/app/features/guides/generated"
        assert cfg.assets_dir == "./public/guides"
        assert cfg.role == "editor"
        assert cfg.locales == ["fr"]
        assert cfg.default_locale == "fr"
        assert cfg.site.title == "Test Guides"
        assert len(cfg.guides) == 1
        assert cfg.guides[0].slug == "onboarding"

    def test_camel_case_aliases(self):
        cfg = BuildConfig(
            **_valid_config(
                screenshotsDir="./custom/screenshots",
                defaultLocale="fr",
            )
        )
        assert cfg.screenshots_dir == "./custom/screenshots"
        assert cfg.default_locale == "fr"

    def test_defaults_applied(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.screenshots_dir == "./output/screenshots"
        assert cfg.default_locale == "fr"

    def test_full_config_with_all_fields(self):
        cfg = BuildConfig(
            **_valid_config(
                screenshotsDir="../steppy/output/screenshots",
                locales=["fr", "nl"],
                defaultLocale="fr",
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "modes": ["stepbystep"],
                    },
                    {
                        "slug": "invoices",
                        "title": "Invoices",
                        "scenario": "editor/invoices.md",
                        "modes": ["stepbystep", "interactive"],
                    },
                ],
                navbar={"order": ["onboarding", "invoices"]},
            )
        )
        assert len(cfg.guides) == 2
        assert cfg.guides[1].modes == [GuideMode.STEPBYSTEP, GuideMode.INTERACTIVE]
        assert cfg.locales == ["fr", "nl"]

    def test_guides_by_slug_helper(self):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {"slug": "a", "title": "A", "scenario": "a.md"},
                    {"slug": "b", "title": "B", "scenario": "b.md"},
                ],
                navbar={"order": ["a", "b"]},
            )
        )
        by_slug = cfg.guides_by_slug()
        assert set(by_slug.keys()) == {"a", "b"}
        assert by_slug["a"].title == "A"


class TestBuildConfigValidation:
    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra"):
            BuildConfig(**_valid_config(unknownField="value"))

    def test_rejects_empty_guides(self):
        with pytest.raises(ValidationError, match="At least one guide"):
            BuildConfig(**_valid_config(guides=[]))

    def test_rejects_empty_locales(self):
        with pytest.raises(ValidationError, match="At least one locale"):
            BuildConfig(**_valid_config(locales=[]))

    def test_rejects_duplicate_slugs(self):
        with pytest.raises(ValidationError, match="Duplicate guide slugs"):
            BuildConfig(
                **_valid_config(
                    guides=[
                        {"slug": "same", "title": "A", "scenario": "a.md"},
                        {"slug": "same", "title": "B", "scenario": "b.md"},
                    ],
                    navbar={"order": ["same"]},
                )
            )

    def test_rejects_navbar_unknown_slug(self):
        with pytest.raises(ValidationError, match="Navbar references unknown slug"):
            BuildConfig(
                **_valid_config(
                    navbar={"order": ["onboarding", "nonexistent"]},
                )
            )

    def test_rejects_default_locale_not_in_locales(self):
        with pytest.raises(ValidationError, match="defaultLocale.*not in"):
            BuildConfig(
                **_valid_config(
                    locales=["fr", "nl"],
                    defaultLocale="en",
                )
            )


class TestGuideEntry:
    def test_default_modes(self):
        entry = GuideEntry(slug="test", title="Test", scenario="test.md")
        assert entry.modes == [GuideMode.STEPBYSTEP]

    def test_explicit_modes(self):
        entry = GuideEntry(slug="test", title="Test", scenario="test.md", modes=["stepbystep", "interactive"])
        assert entry.modes == [GuideMode.STEPBYSTEP, GuideMode.INTERACTIVE]

    def test_rejects_empty_slug(self):
        with pytest.raises(ValidationError, match="Invalid slug"):
            GuideEntry(slug="", title="Test", scenario="test.md")

    def test_rejects_invalid_slug_chars(self):
        with pytest.raises(ValidationError, match="Invalid slug"):
            GuideEntry(slug="has spaces", title="Test", scenario="test.md")

    def test_rejects_empty_modes(self):
        with pytest.raises(ValidationError, match="At least one mode"):
            GuideEntry(slug="test", title="Test", scenario="test.md", modes=[])

    def test_rejects_duplicate_modes(self):
        with pytest.raises(ValidationError, match="Duplicate modes"):
            GuideEntry(slug="test", title="Test", scenario="test.md", modes=["stepbystep", "stepbystep"])

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra"):
            GuideEntry(slug="test", title="Test", scenario="test.md", unknown="x")

    def test_slug_with_hyphens_and_underscores(self):
        entry = GuideEntry(slug="my-guide_v2", title="Test", scenario="test.md")
        assert entry.slug == "my-guide_v2"


class TestSiteConfig:
    def test_valid(self):
        site = SiteConfig(title="My Guides")
        assert site.title == "My Guides"

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra"):
            SiteConfig(title="My Guides", unknown="x")


class TestNavbarConfig:
    def test_valid(self):
        nav = NavbarConfig(order=["a", "b"])
        assert nav.order == ["a", "b"]

    def test_empty_order_allowed(self):
        nav = NavbarConfig(order=[])
        assert nav.order == []


class TestBuildTarget:
    def test_target_defaults_to_static(self):
        # The default build target is the framework-agnostic static site;
        # Angular projects opt in with an explicit ``target: 'angular'``.
        cfg = BuildConfig(**_valid_config())
        assert cfg.target == BuildTarget.STATIC

    def test_target_static_accepts_stepbystep(self):
        cfg = BuildConfig(
            **_valid_config(
                target="static",
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "modes": ["stepbystep"],
                    },
                ],
            )
        )
        assert cfg.target == BuildTarget.STATIC

    def test_target_static_accepts_interactive(self):
        """Stage 4: both targets render interactive guides via the same Lit
        ``<steppy-guide>`` web component, so the historical rejection is gone."""
        cfg = BuildConfig(
            **_valid_config(
                target="static",
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "modes": ["stepbystep", "interactive"],
                    },
                ],
            )
        )
        assert cfg.target == BuildTarget.STATIC
        assert GuideMode.INTERACTIVE in cfg.guides[0].modes

    def test_target_unknown_value_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(target="hugo"))

    def test_target_angular_accepts_interactive(self):
        cfg = BuildConfig(
            **_valid_config(
                target="angular",
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "modes": ["stepbystep", "interactive"],
                    },
                ],
            )
        )
        assert cfg.target == BuildTarget.ANGULAR


class TestGroups:
    def _grouped(self, **overrides):
        base = _valid_config(
            guides=[
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
                {"slug": "b", "title": "B", "scenario": "b.md", "group": "module"},
                {"slug": "free", "title": "Free", "scenario": "free.md"},
            ],
            navbar={"order": ["module", "free"]},
            groups=[{"slug": "module", "title": "Module"}],
        )
        base.update(overrides)
        return base

    def test_grouped_config_parses(self):
        cfg = BuildConfig(**self._grouped())
        assert len(cfg.groups) == 1
        assert cfg.groups[0].slug == "module"
        assert cfg.guides[0].group == "module"

    def test_guide_group_must_reference_existing_group(self):
        with pytest.raises(ValidationError, match="references unknown group"):
            BuildConfig(
                **self._grouped(
                    guides=[
                        {"slug": "a", "title": "A", "scenario": "a.md", "group": "ghost"},
                    ],
                    navbar={"order": ["ghost"]},
                    groups=[{"slug": "module", "title": "Module"}],
                )
            )

    def test_group_guide_slug_collision_rejected(self):
        with pytest.raises(ValidationError, match="Slug collision between guides and groups"):
            BuildConfig(
                **_valid_config(
                    guides=[
                        {"slug": "shared", "title": "S", "scenario": "s.md"},
                    ],
                    navbar={"order": ["shared"]},
                    groups=[{"slug": "shared", "title": "Shared"}],
                )
            )

    def test_navbar_referencing_grouped_guide_rejected(self):
        with pytest.raises(ValidationError, match="belongs to group"):
            BuildConfig(
                **self._grouped(
                    navbar={"order": ["a", "free"]},
                )
            )

    def test_duplicate_group_slugs_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate group slugs"):
            BuildConfig(
                **_valid_config(
                    groups=[
                        {"slug": "g", "title": "G1"},
                        {"slug": "g", "title": "G2"},
                    ],
                )
            )

    def test_group_landing_page_path(self):
        cfg = BuildConfig(**self._grouped())
        tree = cfg.navigation_tree()
        node = next(n for n in tree if n["kind"] == "group")
        assert node["path"] == "/module"
        assert [c["slug"] for c in node["children"]] == ["a", "b"]

    def test_navigation_tree_orders_top_level_by_navbar(self):
        cfg = BuildConfig(**self._grouped(navbar={"order": ["free", "module"]}))
        tree = cfg.navigation_tree()
        kinds = [(n["kind"], n.get("slug") or n.get("title")) for n in tree]
        assert kinds == [("guide", "free"), ("group", "module")]

    def test_navigation_tree_falls_back_to_declaration_order(self):
        cfg = BuildConfig(**self._grouped(navbar={"order": []}))
        tree = cfg.navigation_tree()
        # Groups in declaration order, then ungrouped guides in declaration order
        assert [(n["kind"], n.get("slug") or n.get("title")) for n in tree] == [
            ("group", "module"),
            ("guide", "free"),
        ]


class TestSubGroups:
    def _nested(self, **overrides):
        base = _valid_config(
            guides=[
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "sub"},
                {"slug": "b", "title": "B", "scenario": "b.md", "group": "parent"},
            ],
            navbar={"order": ["parent"]},
            groups=[
                {"slug": "parent", "title": "Parent"},
                {"slug": "sub", "title": "Sub", "parent": "parent"},
            ],
        )
        base.update(overrides)
        return base

    def test_subgroup_parses(self):
        cfg = BuildConfig(**self._nested())
        sub = next(g for g in cfg.groups if g.slug == "sub")
        assert sub.parent == "parent"

    def test_unknown_parent_rejected(self):
        with pytest.raises(ValidationError, match="references unknown parent"):
            BuildConfig(
                **self._nested(
                    groups=[
                        {"slug": "parent", "title": "Parent"},
                        {"slug": "sub", "title": "Sub", "parent": "ghost"},
                    ],
                )
            )

    def test_self_parent_rejected(self):
        with pytest.raises(ValidationError, match="cannot be its own parent"):
            BuildConfig(
                **self._nested(
                    groups=[
                        {"slug": "parent", "title": "Parent"},
                        {"slug": "sub", "title": "Sub", "parent": "sub"},
                    ],
                )
            )

    def test_cycle_rejected(self):
        with pytest.raises(ValidationError, match="Circular parent reference"):
            BuildConfig(
                **_valid_config(
                    guides=[{"slug": "x", "title": "X", "scenario": "x.md", "group": "a"}],
                    navbar={"order": []},
                    groups=[
                        {"slug": "a", "title": "A", "parent": "b"},
                        {"slug": "b", "title": "B", "parent": "a"},
                    ],
                )
            )

    def test_navbar_referencing_subgroup_rejected(self):
        with pytest.raises(ValidationError, match="is a sub-group"):
            BuildConfig(
                **self._nested(
                    navbar={"order": ["sub"]},
                )
            )

    def test_navigation_tree_nests_subgroup_under_parent(self):
        cfg = BuildConfig(**self._nested())
        tree = cfg.navigation_tree()
        # Only top-level: the parent group (sub is nested inside).
        assert [n["slug"] for n in tree if n["kind"] == "group"] == ["parent"]
        parent_node = next(n for n in tree if n["slug"] == "parent")
        kinds = [(c["kind"], c["slug"]) for c in parent_node["children"]]
        # Sub-groups appear first, then the parent's direct guides.
        assert kinds == [("group", "sub"), ("guide", "b")]
        sub_node = next(c for c in parent_node["children"] if c["kind"] == "group")
        assert [g["slug"] for g in sub_node["children"]] == ["a"]


class TestOnboarding:
    def _onb(self, **overrides):
        base = _valid_config(
            guides=[
                {"slug": "step1", "title": "Step 1", "scenario": "1.md"},
                {"slug": "step2", "title": "Step 2", "scenario": "2.md"},
            ],
            navbar={"order": ["step1", "step2"]},
            onboarding={"enabled": True, "guides": ["step1", "step2"]},
        )
        base.update(overrides)
        return base

    def test_onboarding_parses(self):
        cfg = BuildConfig(**self._onb())
        assert cfg.onboarding is not None
        assert cfg.onboarding.enabled
        assert cfg.onboarding.title == "Getting Started"
        assert cfg.onboarding.guides == ["step1", "step2"]

    def test_onboarding_index(self):
        cfg = BuildConfig(**self._onb())
        assert cfg.onboarding_index() == {"step1": 0, "step2": 1}

    def test_onboarding_unknown_slug_rejected(self):
        with pytest.raises(ValidationError, match="references unknown guide slug"):
            BuildConfig(
                **self._onb(
                    onboarding={"enabled": True, "guides": ["step1", "ghost"]},
                )
            )

    def test_onboarding_empty_when_enabled_rejected(self):
        with pytest.raises(ValidationError, match="onboarding.guides is empty"):
            BuildConfig(
                **self._onb(
                    onboarding={"enabled": True, "guides": []},
                )
            )

    def test_onboarding_duplicate_slug_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate slug 'step1' in onboarding"):
            BuildConfig(
                **self._onb(
                    onboarding={"enabled": True, "guides": ["step1", "step1"]},
                )
            )

    def test_reserved_slug_collision_rejected(self):
        with pytest.raises(ValidationError, match="reserved for the onboarding"):
            BuildConfig(
                **_valid_config(
                    guides=[
                        {"slug": ONBOARDING_SLUG, "title": "X", "scenario": "x.md"},
                    ],
                    navbar={"order": [ONBOARDING_SLUG]},
                    onboarding={"enabled": True, "guides": [ONBOARDING_SLUG]},
                )
            )

    def test_onboarding_disabled_skips_validation(self):
        # disabled + empty guides + ONBOARDING_SLUG-named guide is allowed
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {"slug": ONBOARDING_SLUG, "title": "X", "scenario": "x.md"},
                ],
                navbar={"order": [ONBOARDING_SLUG]},
                onboarding={"enabled": False, "guides": []},
            )
        )
        assert cfg.onboarding_index() == {}

    def test_navigation_tree_includes_onboarding_first(self):
        cfg = BuildConfig(**self._onb())
        tree = cfg.navigation_tree()
        assert tree[0]["kind"] == "onboarding"
        assert tree[0]["path"] == f"/{ONBOARDING_SLUG}"
        assert tree[0]["guides"] == ["step1", "step2"]


class TestNavigationTreeNoGroupsOrOnboarding:
    def test_flat_config_produces_flat_tree(self):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {"slug": "a", "title": "A", "scenario": "a.md"},
                    {"slug": "b", "title": "B", "scenario": "b.md"},
                ],
                navbar={"order": ["a", "b"]},
            )
        )
        tree = cfg.navigation_tree()
        assert all(n["kind"] == "guide" for n in tree)
        assert [n["slug"] for n in tree] == ["a", "b"]


class TestLocalizedText:
    """Cover the LocalizedText type — title/description fields accept either a
    plain string (back-compat) or a {locale: string} map; reject other shapes."""

    def test_plain_string_title_accepted(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.guides[0].title == "Onboarding"

    def test_locale_map_title_accepted_on_guide(self):
        cfg = BuildConfig(
            **_valid_config(
                locales=["fr", "nl"],
                guides=[
                    {"slug": "x", "title": {"fr": "Bonjour", "nl": "Hallo"}, "scenario": "x.md"},
                ],
                navbar={"order": ["x"]},
            )
        )
        assert cfg.guides[0].title == {"fr": "Bonjour", "nl": "Hallo"}

    def test_locale_map_on_group_title_and_description(self):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {"slug": "x", "title": "X", "scenario": "x.md", "group": "g"},
                ],
                groups=[
                    {
                        "slug": "g",
                        "title": {"fr": "Groupe", "nl": "Groep"},
                        "description": {"fr": "Desc FR", "nl": "Desc NL"},
                    },
                ],
                navbar={"order": ["g"]},
            )
        )
        assert cfg.groups[0].title == {"fr": "Groupe", "nl": "Groep"}
        assert cfg.groups[0].description == {"fr": "Desc FR", "nl": "Desc NL"}

    def test_locale_map_on_onboarding_and_site(self):
        cfg = BuildConfig(
            **_valid_config(
                site={"title": {"fr": "Guides", "nl": "Gidsen"}},
                onboarding={
                    "enabled": True,
                    "title": {"fr": "Pour commencer", "nl": "Aan de slag"},
                    "description": {"fr": "FR desc", "nl": "NL desc"},
                    "guides": ["onboarding"],
                },
            )
        )
        assert cfg.site.title == {"fr": "Guides", "nl": "Gidsen"}
        assert cfg.onboarding.title == {"fr": "Pour commencer", "nl": "Aan de slag"}

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(site={"title": ""}))

    def test_empty_dict_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(site={"title": {}}))

    def test_non_string_value_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(site={"title": {"fr": 123}}))

    def test_int_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(site={"title": 42}))

    def test_list_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(site={"title": ["a", "b"]}))

    def test_optional_description_accepts_none(self):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {"slug": "x", "title": "X", "scenario": "x.md", "group": "g"},
                ],
                groups=[{"slug": "g", "title": "G"}],
                navbar={"order": ["g"]},
            )
        )
        assert cfg.groups[0].description is None


class TestCursorMode:
    """Project-wide ``cursor`` default + per-guide ``cursor`` override."""

    def test_default_is_arrow(self):
        cfg = BuildConfig(**_valid_config())
        assert cfg.cursor == CursorMode.ARROW
        assert cfg.guides[0].cursor is None

    @pytest.mark.parametrize("mode", ["arrow", "hotspot", "off"])
    def test_project_default_accepts_each_mode(self, mode):
        cfg = BuildConfig(**_valid_config(cursor=mode))
        assert cfg.cursor.value == mode

    @pytest.mark.parametrize("mode", ["arrow", "hotspot", "off"])
    def test_per_guide_override_accepts_each_mode(self, mode):
        cfg = BuildConfig(
            **_valid_config(
                guides=[
                    {
                        "slug": "onboarding",
                        "title": "Onboarding",
                        "scenario": "editor/onboarding.md",
                        "cursor": mode,
                    },
                ],
            )
        )
        assert cfg.guides[0].cursor.value == mode

    def test_invalid_mode_rejected_on_buildconfig(self):
        with pytest.raises(ValidationError):
            BuildConfig(**_valid_config(cursor="sparkly"))

    def test_invalid_mode_rejected_on_guide(self):
        with pytest.raises(ValidationError):
            BuildConfig(
                **_valid_config(
                    guides=[
                        {
                            "slug": "onboarding",
                            "title": "Onboarding",
                            "scenario": "editor/onboarding.md",
                            "cursor": "sparkly",
                        },
                    ],
                )
            )
