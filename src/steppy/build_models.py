"""Pydantic models for the steppy build command configuration."""

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from .locales import normalize_locale, validate_locale_tag
from .sitemap import SitemapError, normalize_lastmod


# Reserved URL slug used for the onboarding landing page. Guide and group slugs
# may not collide with this when onboarding is enabled.
ONBOARDING_SLUG = "getting-started"

# URL slug of the whole-site handbook page (`steppy build --handbook`). Checked
# against guide/group slugs only when that flag is used, so a project that never
# exports a handbook is free to name a guide "handbook".
HANDBOOK_SLUG = "handbook"


# Either a plain string (treated as the same value for every locale) or a
# {locale: string} map. Resolved at render time via build.pick_locale.
LocalizedText = Union[str, dict[str, str]]


def _validate_localized(value: Any, *, allow_none: bool = False) -> Any:
    if value is None:
        if allow_none:
            return None
        raise ValueError("Localized text must not be null")
    if isinstance(value, str):
        if not value:
            raise ValueError("Localized text must be a non-empty string")
        return value
    if isinstance(value, dict):
        if not value:
            raise ValueError("Localized text map must not be empty")
        for k, v in value.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"Localized text map keys must be non-empty strings, got {k!r}")
            if not isinstance(v, str) or not v:
                raise ValueError(f"Localized text map values must be non-empty strings (key '{k}' has value {v!r})")
        return value
    raise ValueError(f"Localized text must be a string or {{locale: string}} map, got {type(value).__name__}")


class GuideMode(str, Enum):
    """Supported guide rendering modes.

    - ``stepbystep``  : the scrollable page of captioned screenshots (always emitted)
    - ``interactive`` : the ``<steppy-guide>`` player, driven by ``guide.json``
    - ``video``       : a narrated MP4 synthesized from the same manifest, per
      (locale x layout). Declaring it is inert until ``steppy build --video`` /
      ``steppy guide --video`` arms the render — a render costs minutes of CPU
      and, with a paid TTS provider, real money.

    Every member needs a ``MODE_LABELS`` row in ``build.py`` (the Angular
    component template indexes it directly); guard:
    ``tests/test_guide_mode_sync.py``.
    """

    STEPBYSTEP = "stepbystep"
    INTERACTIVE = "interactive"
    VIDEO = "video"


class BuildTarget(str, Enum):
    """Supported build output targets."""

    ANGULAR = "angular"
    STATIC = "static"


class CursorMode(str, Enum):
    """Cursor presentation modes for the interactive ``<steppy-guide>`` player.

    - ``arrow``   : animated arrow + pulsing hotspot (default — current behaviour)
    - ``hotspot`` : no arrow / ripple / kbd overlay; hotspot still pulses and the
      user clicks it to advance (Guideflow-style)
    - ``off``     : no arrow and no hotspot — auto-advance every step after
      ``waitAfter`` like a passive video walkthrough
    """

    ARROW = "arrow"
    HOTSPOT = "hotspot"
    OFF = "off"


class TemplateMode(str, Enum):
    """Presentation templates for the interactive ``<steppy-guide>`` player.

    - ``overlay``  : full-bleed preview + floating callout pinned to the target
      (the current/default player look)
    - ``sidebar``  : left rail with a vertical step timeline + description, preview
      on the right
    - ``inline``   : full-bleed preview + a slim bottom caption bar

    Runtime-switchable in the player; this is only the per-guide default.
    """

    SIDEBAR = "sidebar"
    OVERLAY = "overlay"
    INLINE = "inline"


class SeoMode(str, Enum):
    """SEO / indexing mode for the static build target.

    - ``private`` (default): every page gets a ``noindex`` robots meta and
      ``robots.txt`` emits ``Disallow: /`` — nothing is indexable. Safe default
      so an accidentally-exposed client guide site is not crawlable.
    - ``public``: emits ``sitemap.xml``, canonical links, ``hreflang`` alternates
      and optional Open Graph tags. Requires ``baseUrl``.
    """

    PUBLIC = "public"
    PRIVATE = "private"


def _validate_slug(value: str, kind: str) -> str:
    if not value or not value.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            f"Invalid slug '{value}' ({kind}): must be non-empty and contain only "
            "alphanumeric characters, hyphens, or underscores"
        )
    return value


class GuideEntry(BaseModel):
    """A single guide entry in the build config."""

    slug: str
    title: LocalizedText
    scenario: str
    modes: list[GuideMode] = [GuideMode.STEPBYSTEP]
    group: Optional[str] = None
    # Optional per-guide cursor mode override. Falls back to ``BuildConfig.cursor``.
    cursor: Optional[CursorMode] = None
    # Optional per-guide default presentation template. Falls back to
    # ``BuildConfig.default_template``.
    template: Optional[TemplateMode] = None
    # Optional per-guide meta description for public SEO / shared-link
    # snippets. Falls back to ``title`` when unset.
    description: Optional[LocalizedText] = None

    class Config:
        extra = "forbid"

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return _validate_slug(v, "guide")

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v: Any) -> Any:
        return _validate_localized(v)

    @field_validator("modes")
    @classmethod
    def validate_modes(cls, v: list[GuideMode]) -> list[GuideMode]:
        if not v:
            raise ValueError("At least one mode is required")
        if len(v) != len(set(v)):
            raise ValueError("Duplicate modes are not allowed")
        return v

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: Any) -> Any:
        return _validate_localized(v, allow_none=True)


class GroupConfig(BaseModel):
    """A group of guides surfaced as a navbar section + landing page."""

    slug: str
    title: LocalizedText
    description: Optional[LocalizedText] = None
    parent: Optional[str] = None

    class Config:
        extra = "forbid"

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return _validate_slug(v, "group")

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v: Any) -> Any:
        return _validate_localized(v)

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: Any) -> Any:
        return _validate_localized(v, allow_none=True)


class OnboardingConfig(BaseModel):
    """An ordered onboarding path through a subset of guides."""

    enabled: bool = True
    title: LocalizedText = "Getting Started"
    description: Optional[LocalizedText] = None
    guides: list[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v: Any) -> Any:
        return _validate_localized(v)

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: Any) -> Any:
        return _validate_localized(v, allow_none=True)


class SiteConfig(BaseModel):
    """Site-level metadata."""

    title: LocalizedText

    class Config:
        extra = "forbid"

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v: Any) -> Any:
        return _validate_localized(v)


class NavbarConfig(BaseModel):
    """Navbar ordering configuration."""

    order: list[str]

    class Config:
        extra = "forbid"


class SeoConfig(BaseModel):
    """Static-site SEO / indexing controls.

    ``mode`` defaults to ``private`` (safe default): every static page gets a
    ``noindex`` robots meta and ``robots.txt`` emits ``Disallow: /`` so an
    accidentally-exposed client guide site is not indexable. Set
    ``mode: 'public'`` (which requires ``baseUrl``) to emit ``sitemap.xml``,
    canonical links, ``hreflang`` alternates and optional Open Graph tags for
    the public showcase / marketing sites. Static target only — the Angular
    host owns its own meta. Indexing controls are defense-in-depth, not access
    control (private sites still sit behind auth).
    """

    mode: SeoMode = SeoMode.PRIVATE
    base_url: Optional[str] = Field(default=None, alias="baseUrl")
    # Site-wide default meta description (index page + ultimate fallback + OG).
    description: Optional[LocalizedText] = None
    # Opt-in Open Graph / Twitter card tags (public mode only).
    open_graph: bool = Field(default=False, alias="openGraph")
    # Optional site-wide <lastmod> for every sitemap entry. Unset (the default)
    # emits none, which is what keeps a build byte-reproducible; there is
    # deliberately no 'auto' mode, because every source of a timestamp available
    # here is wrong (a CI checkout stamps every file at clone time, and
    # screenshots are not in git at all). Set it from the commit date instead:
    # `git log -1 --format=%cI`.
    lastmod: Optional[str] = None

    class Config:
        extra = "forbid"
        populate_by_name = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"seo.baseUrl must be an absolute http(s) URL, got {v!r}")
        return v.rstrip("/")

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: Any) -> Any:
        return _validate_localized(v, allow_none=True)

    @field_validator("lastmod")
    @classmethod
    def validate_lastmod(cls, v: Optional[str]) -> Optional[str]:
        """Grammar lives in ``sitemap.normalize_lastmod``; this only re-labels
        the failure with the config key, like ``validate_base_url`` does."""
        if v is None:
            return None
        if not v.strip():
            return None
        try:
            return normalize_lastmod(v)
        except SitemapError as exc:
            raise ValueError(f"seo.{exc}") from exc

    @model_validator(mode="after")
    def validate_public_requires_base_url(self) -> "SeoConfig":
        if self.mode == SeoMode.PUBLIC and not self.base_url:
            raise ValueError(
                "seo.baseUrl is required when seo.mode is 'public' — absolute "
                "URLs are needed for sitemap.xml, canonical links and hreflang "
                "alternates"
            )
        return self


class BuildConfig(BaseModel):
    """Top-level build configuration, mirrors the TypeScript steppy.guide.config.ts."""

    scenarios_root: str = Field(alias="scenariosRoot")
    screenshots_dir: str = Field(default="./output/screenshots", alias="screenshotsDir")
    output_dir: str = Field(alias="outputDir")
    assets_dir: str = Field(alias="assetsDir")
    role: str
    locales: list[str] = ["fr"]
    default_locale: str = Field(default="fr", alias="defaultLocale")
    # Optional per-locale display-name overrides for the language switcher.
    # Merged over the built-in LOCALE_LABELS in build.py; unmapped locales fall
    # back to their uppercased code. Lets a project add/rename a language
    # without a code change (FLAWS §4.4).
    locale_labels: dict[str, str] = Field(default_factory=dict, alias="localeLabels")
    # Optional layouts dimension. When non-empty, the build assembles each
    # guide once per (locale, layout) combination and renders an in-page
    # toggle. Empty = single-layout build (backwards-compat).
    layouts: list[str] = Field(default_factory=list)
    default_layout: Optional[str] = Field(default=None, alias="defaultLayout")
    # Default is the framework-agnostic ``static`` site (works with any host,
    # including the React/Vue wrapper packages). Angular projects must set
    # ``target: 'angular'`` explicitly.
    target: BuildTarget = Field(default=BuildTarget.STATIC)
    # Project-wide default cursor mode for the interactive player. Per-guide
    # ``GuideEntry.cursor`` overrides this. Default is ``arrow`` (current
    # behaviour — animated cursor + hotspot).
    cursor: CursorMode = Field(default=CursorMode.ARROW)
    # Project-wide default presentation template for the interactive player.
    # Per-guide ``GuideEntry.template`` overrides this. Default is ``overlay``
    # (the current floating-callout player look).
    default_template: TemplateMode = Field(default=TemplateMode.OVERLAY, alias="defaultTemplate")
    site: SiteConfig
    guides: list[GuideEntry]
    navbar: NavbarConfig
    groups: list[GroupConfig] = Field(default_factory=list)
    onboarding: Optional[OnboardingConfig] = None
    # Static-site SEO / indexing controls. Defaults to a private config
    # (noindex + robots.txt Disallow) so nothing is accidentally indexable.
    seo: SeoConfig = Field(default_factory=SeoConfig)

    class Config:
        extra = "forbid"
        populate_by_name = True

    @field_validator("layouts")
    @classmethod
    def validate_layouts(cls, v: list[str]) -> list[str]:
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"layout names must be non-empty strings, got {item!r}")
        if len(v) != len(set(v)):
            raise ValueError("Duplicate layout names are not allowed")
        return v

    @field_validator("guides")
    @classmethod
    def validate_guides_non_empty(cls, v: list[GuideEntry]) -> list[GuideEntry]:
        if not v:
            raise ValueError("At least one guide entry is required")
        return v

    @field_validator("locales")
    @classmethod
    def validate_locales_non_empty(cls, v: list[str]) -> list[str]:
        # Non-empty first: the message is pinned by tests and by anyone who has
        # seen it, and a format complaint about an absent list reads as noise.
        if not v:
            raise ValueError("At least one locale is required")
        for item in v:
            # Validated, NOT rewritten. The authored spelling is a join key with
            # the capture directories on disk, the host app's i18n language code
            # and every LocalizedText map; only `hreflang` and `<html lang>` get
            # the normalised form. See src/steppy/locales.py. The return value is
            # discarded on purpose.
            validate_locale_tag(item)
        return v

    @field_validator("default_locale")
    @classmethod
    def validate_default_locale_tag(cls, v: str) -> str:
        validate_locale_tag(v)
        return v

    @model_validator(mode="after")
    def validate_cross_references(self) -> "BuildConfig":
        guide_slugs = {g.slug for g in self.guides}
        group_slugs = {g.slug for g in self.groups}

        # Duplicate guide slugs
        if len(guide_slugs) != len(self.guides):
            seen = set()
            dupes = []
            for g in self.guides:
                if g.slug in seen:
                    dupes.append(g.slug)
                seen.add(g.slug)
            raise ValueError(f"Duplicate guide slugs: {dupes}")

        # Duplicate group slugs
        if len(group_slugs) != len(self.groups):
            seen = set()
            dupes = []
            for g in self.groups:
                if g.slug in seen:
                    dupes.append(g.slug)
                seen.add(g.slug)
            raise ValueError(f"Duplicate group slugs: {dupes}")

        # Group/guide slug collisions
        collisions = guide_slugs & group_slugs
        if collisions:
            raise ValueError(
                f"Slug collision between guides and groups: {sorted(collisions)}. "
                "Guide pages and group landing pages share the same URL space."
            )

        # Each guide.group must reference an existing group
        for guide in self.guides:
            if guide.group is not None and guide.group not in group_slugs:
                raise ValueError(
                    f"Guide '{guide.slug}' references unknown group "
                    f"'{guide.group}'. Available groups: {sorted(group_slugs) or '[]'}"
                )

        # Each group.parent must reference an existing group (no self-ref, no cycles)
        groups_by_slug_map = {g.slug: g for g in self.groups}
        for group in self.groups:
            if group.parent is None:
                continue
            if group.parent not in group_slugs:
                raise ValueError(
                    f"Group '{group.slug}' references unknown parent "
                    f"'{group.parent}'. Available groups: {sorted(group_slugs)}"
                )
            if group.parent == group.slug:
                raise ValueError(f"Group '{group.slug}' cannot be its own parent")
        for group in self.groups:
            visited: set[str] = set()
            cur = group
            while cur.parent is not None:
                if cur.parent in visited:
                    raise ValueError(f"Circular parent reference detected involving group '{group.slug}'")
                visited.add(cur.slug)
                cur = groups_by_slug_map[cur.parent]

        # Navbar references — accept guide slugs (must be ungrouped) and group slugs
        guide_by_slug = {g.slug: g for g in self.guides}
        for slug in self.navbar.order:
            if slug in group_slugs:
                grp = groups_by_slug_map[slug]
                if grp.parent is not None:
                    raise ValueError(
                        f"Navbar references '{slug}' which is a sub-group (parent: "
                        f"'{grp.parent}'). List the parent group in navbar.order instead."
                    )
                continue
            if slug in guide_by_slug:
                if guide_by_slug[slug].group is not None:
                    raise ValueError(
                        f"Navbar references guide '{slug}' but it belongs to "
                        f"group '{guide_by_slug[slug].group}'. Reference the "
                        "group slug instead, or remove the guide's `group` field."
                    )
                continue
            raise ValueError(
                f"Navbar references unknown slug '{slug}'. "
                f"Available guide slugs: {sorted(guide_slugs)}; "
                f"group slugs: {sorted(group_slugs) or '[]'}"
            )

        # default_locale must be in locales
        if self.default_locale not in self.locales:
            raise ValueError(f"defaultLocale '{self.default_locale}' is not in locales {self.locales}")

        # Two locales that normalise to the same tag would emit two hreflang
        # alternates claiming the same language for two different URLs, which
        # makes search engines discard the whole cluster -- and on NTFS/APFS they
        # may resolve to the same output file (the exclusive.py argument).
        by_normalized: dict[str, str] = {}
        for locale in self.locales:
            normalized = normalize_locale(locale)
            first = by_normalized.setdefault(normalized, locale)
            if first != locale:
                raise ValueError(
                    f"locales '{first}' and '{locale}' both mean '{normalized}'. They would emit "
                    "two hreflang alternates claiming the same language for two different URLs, "
                    "which makes search engines discard the whole alternates cluster — and on "
                    "Windows and macOS they may resolve to the same output file."
                )

        # A capture path carries locale and layout as bare sibling segments and
        # `captures.classify_capture_path` tells them apart by set membership, so
        # a name in both sets is genuinely ambiguous.
        shared = sorted(set(self.locales) & set(self.layouts))
        if shared:
            raise ValueError(
                f"{shared} appear in both locales and layouts. Capture paths carry both as bare "
                "directory segments, so a name in both is ambiguous — rename one side."
            )

        # default_layout must be in layouts (when both are set)
        if self.layouts:
            if self.default_layout is None:
                # Default to the first declared layout
                object.__setattr__(self, "default_layout", self.layouts[0])
            elif self.default_layout not in self.layouts:
                raise ValueError(f"defaultLayout '{self.default_layout}' is not in layouts {self.layouts}")

        # Both targets support interactive mode as of stage 4 of the
        # interactive-player feature: the static target loads the same Lit
        # ``<steppy-guide>`` web component via a plain ``<script type="module">``.

        # Onboarding validation
        if self.onboarding is not None and self.onboarding.enabled:
            if not self.onboarding.guides:
                raise ValueError("onboarding.guides is empty but onboarding.enabled is true")
            seen: set[str] = set()
            for slug in self.onboarding.guides:
                if slug in seen:
                    raise ValueError(f"Duplicate slug '{slug}' in onboarding.guides")
                seen.add(slug)
                if slug not in guide_slugs:
                    raise ValueError(
                        f"onboarding.guides references unknown guide slug '{slug}'. Available: {sorted(guide_slugs)}"
                    )
            if ONBOARDING_SLUG in guide_slugs or ONBOARDING_SLUG in group_slugs:
                raise ValueError(
                    f"Slug '{ONBOARDING_SLUG}' is reserved for the onboarding "
                    "landing page when onboarding is enabled. Rename the "
                    "conflicting guide or group."
                )

        return self

    def guides_by_slug(self) -> dict[str, GuideEntry]:
        """Return guides indexed by slug."""
        return {g.slug: g for g in self.guides}

    def groups_by_slug(self) -> dict[str, GroupConfig]:
        """Return groups indexed by slug."""
        return {g.slug: g for g in self.groups}

    def onboarding_index(self) -> dict[str, int]:
        """Return guide slug → 0-based onboarding step index, or empty dict."""
        if self.onboarding is None or not self.onboarding.enabled:
            return {}
        return {slug: i for i, slug in enumerate(self.onboarding.guides)}

    def navigation_tree(self) -> list[dict[str, Any]]:
        """Compute the effective top-level navigation.

        Returns a list of dicts shaped as one of:
          - {"kind": "onboarding", "title": str, "path": str, "guides": [slug, ...]}
          - {"kind": "group", "slug": str, "title": str, "path": str,
             "description": str|None, "children": [guide_node, ...]}
          - {"kind": "guide", "slug": str, "title": str, "path": str}

        Order:
          1. Onboarding entry (if enabled) — always first.
          2. Top-level entries (groups + ungrouped guides) by `navbar.order`,
             then by declaration order.
        """
        nodes: list[dict[str, Any]] = []

        if self.onboarding is not None and self.onboarding.enabled:
            nodes.append(
                {
                    "kind": "onboarding",
                    "title": self.onboarding.title,
                    "path": f"/{ONBOARDING_SLUG}",
                    "guides": list(self.onboarding.guides),
                }
            )

        guides_by_slug = self.guides_by_slug()
        groups_by_slug = self.groups_by_slug()

        # Build per-group child lists in declaration order.
        # Sub-groups are appended first to their parent's list, each carrying
        # the SAME list reference as its own children — so guides appended in
        # the next pass automatically land inside the right nested node.
        group_children: dict[str, list[dict[str, Any]]] = {g.slug: [] for g in self.groups}
        for grp in self.groups:
            if grp.parent is not None:
                group_children[grp.parent].append(_group_node(grp, group_children[grp.slug]))
        for guide in self.guides:
            if guide.group is not None:
                group_children[guide.group].append(_guide_node(guide))

        # Walk navbar.order to seed top-level placement
        placed_top: set[str] = set()  # group/ungrouped-guide slugs at top
        ordered_top: list[dict[str, Any]] = []
        for slug in self.navbar.order:
            if slug in placed_top:
                continue
            if slug in groups_by_slug:
                grp = groups_by_slug[slug]
                ordered_top.append(_group_node(grp, group_children[grp.slug]))
                placed_top.add(slug)
            elif slug in guides_by_slug:
                ordered_top.append(_guide_node(guides_by_slug[slug]))
                placed_top.add(slug)

        # Append any groups not in navbar.order, in declaration order.
        # Sub-groups (parent is not None) only appear nested under their
        # parent — never at the top level.
        for grp in self.groups:
            if grp.slug in placed_top or grp.parent is not None:
                continue
            ordered_top.append(_group_node(grp, group_children[grp.slug]))
            placed_top.add(grp.slug)

        # Append any ungrouped guides not in navbar.order, in declaration order
        for guide in self.guides:
            if guide.group is not None:
                continue
            if guide.slug in placed_top:
                continue
            ordered_top.append(_guide_node(guide))
            placed_top.add(guide.slug)

        nodes.extend(ordered_top)
        return nodes


def _guide_node(guide: GuideEntry) -> dict[str, Any]:
    return {
        "kind": "guide",
        "slug": guide.slug,
        "title": guide.title,
        "path": f"/{guide.slug}",
    }


def _group_node(grp: "GroupConfig", children: list[dict[str, Any]]) -> dict[str, Any]:
    """Nav node for a group — the twin of :func:`_guide_node`.

    ``children`` is passed by reference: sub-groups are seeded before their
    guides, so the caller relies on later appends to that same list showing up
    inside this node.
    """
    return {
        "kind": "group",
        "slug": grp.slug,
        "title": grp.title,
        "description": grp.description,
        "path": f"/{grp.slug}",
        "children": children,
    }
