"""Build assembler for Steppy — generates Angular or static guide artifacts from scenarios."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import jinja2

from ._player_bundle import (
    get_cursor_bundle_path,
    get_legacy_cursor_iife,
    get_player_bundle_path,
)
from ._templating import bundled_templates_dir, make_template_env
from .captures import DEFAULT_SCREENSHOTS_DIR, resolve_output_dir
from .build_models import (
    HANDBOOK_SLUG,
    ONBOARDING_SLUG,
    BuildConfig,
    BuildTarget,
    CursorMode,
    GuideEntry,
    GuideMode,
    LocalizedText,
    SeoMode,
)
from .guide import GuideAssembler, register_jinja_filters, step_stem
from .locales import base_language, normalize_locale, og_locale
from .models import SteppyConfig
from .naming import GUIDE_SITE_TOURS_DIR
from .sitemap import Alternate, SitemapUrl, limit_warnings, render_sitemap
from .parser import parse_scenario_file


class BuildError(Exception):
    """Raised when the build process encounters an error."""


def _warn(message: str) -> None:
    """Emit a build warning to stderr, like ``config._emit_warnings``.

    build.py has no rich console of its own — the CLI owns that — so anything
    the build wants to say outside its results dict goes here.
    """
    print(f"WARNING: {message}", file=sys.stderr)


# ── Locale display labels ────────────────────────────────────────────────────

# Built-in display names, endonyms where practical. A project can add or
# override any label via ``locale_labels`` in its build config (FLAWS §4.4);
# an unmapped locale falls back to its uppercased code (e.g. "PT-BR").
LOCALE_LABELS: dict[str, str] = {
    "fr": "Français",
    "nl": "Nederlands",
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "ca": "Català",
    "da": "Dansk",
    "sv": "Svenska",
    "no": "Norsk",
    "fi": "Suomi",
    "pl": "Polski",
    "cs": "Čeština",
    "ro": "Română",
    "hu": "Magyar",
    "el": "Ελληνικά",
    "tr": "Türkçe",
    "ru": "Русский",
    "uk": "Українська",
    "ar": "العربية",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
}

# One row per GuideMode member. `templates/angular/component.ts.j2` indexes this
# directly (`mode_labels[mode]`) while looping over a guide's declared modes, so
# a missing row is a Jinja UndefinedError, not a cosmetic gap.
# Guard: tests/test_guide_mode_sync.py.
MODE_LABELS: dict[str, str] = {
    "stepbystep": "Step by Step",
    "interactive": "Interactive",
    "video": "Video",
}


# Default UI strings emitted into every locale's i18n JSON. The host app's
# translation team edits the generated files post-build; we ship English
# defaults so an unedited build is at least usable in English. ngx-translate
# placeholder syntax (``{{var}}``) is preserved untouched.
UI_LABEL_DEFAULTS: dict[str, dict[str, str]] = {
    "onboarding": {
        "start": "Start onboarding →",
        "stepLabel": "step {{position}} of {{total}}",
        "previousLabel": "Start",
        "doneLabel": "Finish",
    },
    "ui": {
        "groupEmpty": "No guides in this group yet.",
    },
}


# ── Localized text resolution ────────────────────────────────────────────────


def pick_locale(
    value: Optional[LocalizedText],
    locale: str,
    default_locale: str,
) -> Optional[str]:
    """Resolve a LocalizedText value for a target locale.

    - Plain string → returned as-is (treated as locale-agnostic).
    - Dict → returns ``value[locale]`` if present, else the entry for the bare
      language subtag, else ``value[default_locale]``, else ``None``.
    - ``None`` → ``None`` (used for optional description fields).

    The language-subtag rung is what keeps a regional locale from silently
    serving another language's text. A site on ``['fr-BE', 'nl-BE']`` whose maps
    are written ``{fr: …, nl: …}`` — the overwhelmingly likely authoring —
    would otherwise fall straight through to the default locale, so every
    ``nl-BE`` page would carry the **French** meta description and og:title
    while declaring ``lang="nl-BE"``. Two URLs with identical text claiming
    different languages is the textbook hreflang-cluster rejection.

    Only the *query* is truncated, never widened: ``fr`` must not match a map
    that has only ``fr-BE``, because with both ``fr-BE`` and ``fr-CA`` present
    the choice would be arbitrary and dict-order dependent.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if locale in value:
        return value[locale]
    bare = base_language(locale)
    if bare != locale and bare in value:
        return value[bare]
    return value.get(default_locale)


# ── Config loading ───────────────────────────────────────────────────────────


def load_ts_config(config_path: Path) -> dict:
    """Evaluate a TypeScript config file via Node subprocess and return the parsed dict.

    Tries ``npx tsx`` to evaluate the config. The target Angular app is expected
    to have Node.js available.

    Raises:
        BuildError: If the config cannot be loaded (Node missing, syntax error, etc.)
    """
    resolved = config_path.resolve().as_posix()
    # Single-line script — embedded newlines break stdout capture when npx.CMD
    # is invoked via subprocess on Windows.
    # ``json.dumps`` emits a properly-escaped JS string literal so a path with a
    # quote/backslash can't break out of (or inject into) the import (FLAWS §5.2).
    script = (
        f"import config from {json.dumps(resolved)}; process.stdout.write(JSON.stringify(config.default ?? config));"
    )
    # NOTE: shell=(os.name == "nt") is required to invoke npx.CMD on Windows.
    # It stays safe because ``script`` always contains spaces, so Python's
    # list2cmdline double-quotes it — inside double quotes cmd.exe does not treat
    # & | ^ as metacharacters, so a path with those characters can't inject.
    npx = shutil.which("npx") or "npx"
    try:
        result = subprocess.run(
            [npx, "tsx", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(config_path.parent),
            timeout=30,
            shell=(os.name == "nt"),
        )
    except FileNotFoundError:
        raise BuildError(
            "Node.js / npx not found. The build command requires Node.js "
            "to evaluate the TypeScript config file. Install Node.js and "
            "ensure npx is on your PATH."
        )
    except subprocess.TimeoutExpired:
        raise BuildError(f"Timed out evaluating config {config_path}. Check that the config file is valid TypeScript.")

    if result.returncode != 0:
        raise BuildError(f"Failed to evaluate config {config_path}:\n{result.stderr.strip()}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BuildError(f"Config {config_path} did not produce valid JSON: {exc}")


def load_build_config(config_path: Path) -> BuildConfig:
    """Load and validate a build config from a TypeScript file.

    Returns a validated ``BuildConfig`` instance.

    Raises:
        BuildError: On load or validation failure.
    """
    if not config_path.exists():
        raise BuildError(f"Config file not found: {config_path}")

    raw = load_ts_config(config_path)
    try:
        return BuildConfig(**raw)
    except Exception as exc:
        raise BuildError(f"Invalid config: {exc}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _slug_to_class_name(slug: str) -> str:
    """Convert a slug like ``my-guide`` to a PascalCase class name ``MyGuide``."""
    return "".join(part.capitalize() for part in slug.replace("_", "-").split("-"))


def _role_namespace(role: str) -> str:
    """Normalize a role to the uppercase identifier used as the i18n key
    prefix and the routes-export const stem (``editor`` → ``EDITOR``,
    ``field-agent`` → ``FIELD_AGENT``)."""
    return role.upper().replace("-", "_")


# ── Build assembler ──────────────────────────────────────────────────────────


class BuildAssembler:
    """Generates guide artifacts from a build config and scenario files.

    Supports two targets: ``angular`` (default) emits standalone components,
    routes, and a navbar consumable by an Angular host app. ``static`` emits
    a self-contained static site (full HTML pages, index, manifest.json, copied
    screenshots) suitable for any plain static host.
    """

    def __init__(
        self,
        build_config: BuildConfig,
        project_root: Path,
        steppy_config: Optional[SteppyConfig] = None,
        templates_dir: Optional[Path] = None,
    ):
        self.build_config = build_config
        self.project_root = project_root

        # Deduped (guide, locale, layout) keys already warned about (see
        # _warn_layout_capture_missing).
        self._warned_missing_layouts: set[tuple[str, str, str]] = set()

        # Effective layouts per guide slug, filled by _build_guide (see
        # _layouts_for_guide). Read by the static manifest writer.
        self._guide_layouts: dict[str, list[str]] = {}

        # PDF export state, armed by build_all(pdf=…, handbook=…). Static
        # target only; every other build leaves these untouched and inert.
        self._pdf_enabled = False
        self._handbook_enabled = False
        # Emitted (page html, title) pairs to print — filled by _emit_static_guide.
        self._pdf_pages: list[tuple[Path, str]] = []
        # locale -> {guide slug: rendered fragment} for the handbook, reusing
        # the fragments _build_guide already rendered.
        self._handbook_fragments: dict[str, dict[str, str]] = {}

        # Video export state, armed by build_all(video=True). Jobs are collected
        # during the normal pass and rendered once at the end, sharing a single
        # browser — the same shape as the PDF pass above.
        self._video_enabled = False
        self._video_jobs: list = []
        # slug -> {locale: [relative media paths]}, for the page templates and
        # the site manifest.
        self._video_media: dict[str, dict[str, dict[str, str]]] = {}
        self.steppy_config = steppy_config

        # In-app tours to republish (the tours ``guide`` target), armed by
        # build_all before the wipe; None when nothing is published.
        self._tours_publish = None

        # Effective locale display names: built-in map, overridden per-project
        # by the build config's ``locale_labels`` (FLAWS §4.4).
        self.locale_labels: dict[str, str] = {
            **LOCALE_LABELS,
            **build_config.locale_labels,
        }

        # The *only* place the normalised spelling of a locale lives. Everything
        # else -- filenames, capture directories, LocalizedText keys, the host
        # app's i18n code -- keeps the authored tag verbatim, because those are
        # identities in systems Steppy does not own (see src/steppy/locales.py).
        # Holding the second form in one dict is what stops a third sprouting.
        self.html_lang: dict[str, str] = {loc: normalize_locale(loc) for loc in build_config.locales}

        self.env = make_template_env(templates_dir, project_root)
        register_jinja_filters(self.env)
        self.env.globals["pick_locale"] = pick_locale
        self.env.globals["default_locale"] = build_config.default_locale

        # Pre-build the GuideAssembler for step preparation
        self._guide_assembler = self._create_guide_assembler()

    def _create_guide_assembler(self) -> GuideAssembler:
        """Create a GuideAssembler with a minimal config if no steppy_config provided.

        ``project_root`` is threaded through deliberately: without it the
        assembler would resolve local templates from the current working
        directory while this build resolves them from the project root, so a
        single build could consult two different ``templates/`` trees.
        """
        if self.steppy_config:
            return GuideAssembler(self.steppy_config, project_root=self.project_root)

        # Build a minimal SteppyConfig sufficient for prepare_guide_steps
        minimal_config = SteppyConfig(
            apps={},
            auth={},
        )
        return GuideAssembler(minimal_config, project_root=self.project_root)

    def _resolve_path(self, relative: str) -> Path:
        """Resolve a path relative to the project root."""
        return (self.project_root / relative).resolve()

    def _screenshots_root(self) -> Path:
        """Where this build reads captures from (FLAWS §4.6).

        Precedence: an explicit ``screenshotsDir`` in the TypeScript build
        config → ``config.output.screenshots_dir`` from ``config.yaml`` (what
        the generated conftest actually wrote to) → the ``BuildConfig``
        default. ``model_fields_set`` is what separates "explicitly set" from
        "defaulted" — without it a build could never fall back, since the field
        carries a default of its own.

        ``steppy_config`` is optional (``cli.build`` tolerates an unloadable
        ``config.yaml``), in which case only the first and last rungs exist.
        """
        if "screenshots_dir" in self.build_config.model_fields_set or self.steppy_config is None:
            return self._resolve_path(self.build_config.screenshots_dir)
        return resolve_output_dir(self.project_root, self.steppy_config.output.screenshots_dir, DEFAULT_SCREENSHOTS_DIR)

    def _is_static(self) -> bool:
        return self.build_config.target == BuildTarget.STATIC

    def _lang_switcher_links(
        self,
        href_for: "callable[[str], str]",
        current_locale: str,
    ) -> list[dict]:
        """Build language-switcher link descriptors for a static page.

        ``href_for(locale)`` returns the target URL (relative to the rendered
        page) for the given locale. The active locale renders as a span; others
        render as links.
        """
        return [
            {
                "locale": loc,
                "label": self.locale_labels.get(loc, loc.upper()),
                "href": href_for(loc),
                "active": loc == current_locale,
            }
            for loc in self.build_config.locales
        ]

    def _redirect_html(
        self,
        target_url: str,
        *,
        lang: str,
        canonical: Optional[str] = None,
        noindex: bool = False,
    ) -> str:
        """Tiny meta-refresh HTML used to point bare directory URLs at a
        default-locale page. Kept inline (no template) since it's trivial.

        ``noindex`` adds a robots noindex meta (private seo mode) so redirect
        stubs are not indexable either. Public builds deliberately do **not**
        get one: a ``noindex`` page canonicalling into an indexable cluster is a
        known footgun, and a zero-delay meta refresh already reads as a
        redirect, so the stub's own URL is not indexed either way.

        ``canonical`` defaults to ``target_url``; public builds pass the
        absolute form so every canonical on the site has the same shape."""
        robots = '<meta name="robots" content="noindex,nofollow,noarchive">\n' if noindex else ""
        return (
            f'<!doctype html>\n<html lang="{lang}">\n<meta charset="utf-8">\n'
            f"{robots}"
            f'<meta http-equiv="refresh" content="0; url={target_url}">\n'
            f'<link rel="canonical" href="{canonical or target_url}">\n'
            f"<title>Redirecting…</title>\n"
            f'<p><a href="{target_url}">Redirecting…</a></p>\n'
        )

    def _dir_redirect_stub(self, dir_path: str) -> str:
        """The ``<dir>/index.html`` meta-refresh stub a section gets so its bare
        directory URL resolves instead of 404ing.

        One helper rather than four call sites each deciding independently what a
        stub's lang, canonical and robots directive should be."""
        default_locale = self.build_config.default_locale
        private = self._is_private_seo()
        return self._redirect_html(
            f"./{default_locale}.html",
            lang=self._html_lang(default_locale),
            canonical=(None if private else self._abs_url(self._page_site_rel(dir_path, default_locale))),
            noindex=private,
        )

    # ── SEO / indexing (static target) ───────────────────────────────────

    def _is_private_seo(self) -> bool:
        """True when the static site should be non-indexable (the default)."""
        return self.build_config.seo.mode != SeoMode.PUBLIC

    def _abs_url(self, site_rel: str) -> str:
        """Absolute URL for a site-root-relative path (public seo mode)."""
        base = self.build_config.seo.base_url or ""
        return f"{base}/{site_rel.lstrip('/')}"

    def _html_lang(self, locale: str) -> str:
        """The BCP-47 spelling of ``locale``, for ``<html lang>`` / ``hreflang``.

        Falls back to the authored tag for anything outside the configured set
        (nothing is, today) rather than raising in the middle of a render."""
        return self.html_lang.get(locale, locale)

    def _page_site_rel(self, dir_path: str, locale: str, is_index: bool = False) -> str:
        """Site-root-relative path for a page in a given locale.

        The default-locale index resolves to ``""`` — the bare site root —
        rather than ``index.html``. Both serve the same bytes, so a canonical
        (and a sitemap ``<loc>``, and an hreflang href) naming the filename has
        the home page indexed as two URLs. The file is still *written* as
        ``index.html`` by ``_index_filename``; only the URL differs.

        Directory stubs elsewhere are deliberately not treated this way:
        ``<group>/index.html`` is a meta-refresh stub rather than the content,
        so ``<group>/`` must never become a canonical."""
        if is_index:
            if locale == self.build_config.default_locale:
                return ""
            return self._index_filename(locale)
        fname = f"{locale}.html"
        return f"{dir_path}/{fname}" if dir_path else fname

    def _has_video(self, entry: GuideEntry) -> bool:
        """Did this build actually produce video for ``entry``?

        ``bool(...get(slug))`` rather than ``slug in ...``: ``_emit_video_guide``
        assigns the key unconditionally, so a guide whose media dict came out
        empty would answer ``True`` to a membership test.
        """
        return bool(self._video_media.get(entry.slug))

    def _emitted_modes(self, entry: GuideEntry) -> list[str]:
        """The guide's modes, narrowed to the ones that have a page behind them.

        A guide can declare ``video`` without ``--video`` having armed the
        render, and offering a "Video" tab with no file behind it is worse than
        not offering it. The component toggle and the guide page's mode row
        already applied that rule; ``manifest.json`` and ``<role>.nav.json`` did
        not, so both advertised a mode whose page was never written and any
        consumer building a switcher from them constructed a 404. One helper, so
        there is no fifth spelling to get wrong.
        """
        has_video = self._has_video(entry)
        return [m.value for m in entry.modes if m is not GuideMode.VIDEO or has_video]

    def _mode_links(self, entry: GuideEntry, current: str, locale: str) -> list[dict]:
        """The "other renderings of this guide" row, for every page that is one.

        Returns ``[]`` when there is only one rendering — a single-mode guide
        gets no row rather than a row of one.

        The row exists because these pages canonical to each other: the
        interactive and video pages point ``rel=canonical`` at the step-by-step
        page (see ``_guide_primary_site_rel``), so a crawler is told where the
        real version is while a reader was given no way to get there. The
        interactive page had no outbound link to it at all, and the video page
        had a hand-rolled one whose label did not even match ``MODE_LABELS``.
        """
        candidates = [("stepbystep", f"./{locale}.html")]
        if GuideMode.INTERACTIVE in entry.modes:
            candidates.append(("interactive", "./interactive.html"))
        if self._has_video(entry):
            candidates.append(("video", "./video.html"))
        if len(candidates) < 2:
            return []
        return [{"label": MODE_LABELS[mode], "href": href, "active": mode == current} for mode, href in candidates]

    def _guide_primary_site_rel(self, entry: GuideEntry) -> str:
        """The one URL a guide's other renderings canonical to.

        ``interactive.html`` and ``video.html`` present the *same* steps as the
        per-locale step-by-step pages, so leaving them self-canonical gave every
        guide three or four near-identical indexable URLs. They point here
        instead, and drop out of the sitemap.

        There is deliberately no "unless the guide has no step-by-step page"
        branch, because there is no such guide: ``_build_guide`` calls
        ``_emit_static_guide`` unconditionally and ``GuideMode``'s docstring says
        ``stepbystep`` is *always emitted*. A branch that cannot fire would read
        like a safety net while protecting nothing."""
        return self._page_site_rel(entry.slug, self.build_config.default_locale)

    def _hreflang_alternates(self, dir_path: str, is_index: bool = False) -> list[dict]:
        """Absolute per-locale sibling URLs for ``<link rel="alternate">`` and
        sitemap ``<xhtml:link>``. Empty for single-locale sites."""
        locales = self.build_config.locales
        if len(locales) <= 1:
            return []
        alts = [
            {
                "hreflang": self._html_lang(loc),
                "href": self._abs_url(self._page_site_rel(dir_path, loc, is_index)),
            }
            for loc in locales
        ]
        alts.append(
            {
                "hreflang": "x-default",
                "href": self._abs_url(self._page_site_rel(dir_path, self.build_config.default_locale, is_index)),
            }
        )
        return alts

    def _first_screenshot_site_rel(self, steps_by_layout: dict, default_layout: Optional[str]) -> Optional[str]:
        """Site-root-relative URL of a guide's first screenshot, for
        ``og:image``. Strips the page-relative ``../`` prefix baked into the
        step data (``../assets/...`` → ``assets/...``)."""
        if not steps_by_layout:
            return None
        if default_layout and default_layout in steps_by_layout:
            key = default_layout
        elif "default" in steps_by_layout:
            key = "default"
        else:
            key = next(iter(steps_by_layout), None)
        if key is None:
            return None
        steps = steps_by_layout.get(key) or []
        if not steps:
            return None
        url = steps[0].get("screenshot")
        if not url:
            return None
        while url.startswith("../"):
            url = url[3:]
        return url.lstrip("/")

    def _seo_for_page(
        self,
        *,
        locale: str,
        title,
        description,
        dir_path: Optional[str] = None,
        is_index: bool = False,
        site_rel: Optional[str] = None,
        alternates: Optional[list] = None,
        og_image_site_rel: Optional[str] = None,
        og_type: str = "article",
    ) -> dict:
        """Per-page SEO context for the ``static/_seo_head.html.j2`` partial.

        Private mode (default) → only a ``noindex`` robots directive. Public
        mode → canonical + hreflang alternates + meta description + optional
        Open Graph. ``title``/``description`` are ``LocalizedText`` — callers
        pre-apply the title fallback for descriptions."""
        if self._is_private_seo():
            return {"robots": "noindex,nofollow,noarchive"}
        default_locale = self.build_config.default_locale
        if site_rel is None:
            site_rel = self._page_site_rel(dir_path or "", locale, is_index)
        canonical = self._abs_url(site_rel)
        desc = pick_locale(description, locale, default_locale)
        if alternates is None:
            alternates = self._hreflang_alternates(dir_path or "", is_index)
        ctx: dict = {
            "robots": None,
            "canonical": canonical,
            "description": desc,
            "alternates": alternates,
        }
        if self.build_config.seo.open_graph:
            ctx["og"] = {
                "type": og_type,
                "title": pick_locale(title, locale, default_locale),
                "description": desc,
                "url": canonical,
                "site_name": pick_locale(self.build_config.site.title, locale, default_locale),
                # None (and therefore omitted) unless the tag has a valid Open
                # Graph spelling -- see locales.og_locale.
                "locale": og_locale(self._html_lang(locale)),
                "image": (self._abs_url(og_image_site_rel) if og_image_site_rel else None),
            }
        return ctx

    # ── Per-guide layout dimension ───────────────────────────────────────

    def _layouts_for_guide(self, entry: GuideEntry, scenario) -> list[str]:
        """The layouts this guide is actually built for.

        ``BuildConfig.layouts`` is the site-wide dimension; a scenario may
        *declare* the viewports it covers via frontmatter ``layouts:`` (the
        same declaration that makes ``steppy run`` parametrize over them).
        Intersect the two, keeping build order, so a desktop-only flow stops
        shipping a "Mobile" toggle that swaps between identical screenshots.

        A scenario that declares nothing keeps the full build dimension —
        output is byte-identical to the pre-feature build. Only the
        *declaration* narrows: a missing capture still falls back with a
        warning (``_warn_layout_capture_missing``), because a failed capture
        must not silently shrink the published site.
        """
        build_layouts = list(self.build_config.layouts)
        declared = getattr(scenario, "layouts", None)
        if not build_layouts or not declared:
            return build_layouts
        narrowed = [layout for layout in build_layouts if layout in declared]
        if not narrowed:
            raise BuildError(
                f"Guide '{entry.slug}': scenario '{entry.scenario}' declares layouts "
                f"{sorted(declared)} but the build declares {build_layouts} — no overlap, "
                f"so there is nothing to render. Align the scenario's frontmatter `layouts:` "
                f"with the build config's `layouts`."
            )
        return narrowed

    def _default_layout_for(self, layouts: list[str]) -> Optional[str]:
        """The build's default layout when it survived the per-guide
        intersection, else this guide's first layout (``None`` with no layouts)."""
        if not layouts:
            return None
        configured = self.build_config.default_layout
        if configured and configured in layouts:
            return configured
        return layouts[0]

    def _warn_layout_capture_missing(self, guide_slug: str, scenario_slug: str, locale: str, layout: str) -> None:
        """Warn once per (guide, locale, layout) that a configured layout has no capture.

        Emitted to stderr like ``config._emit_warnings`` — build.py has no
        console. Deduped because the message would otherwise repeat per guide
        per locale on every build of a project that simply has not recaptured.
        """
        key = (guide_slug, locale, layout)
        if key in self._warned_missing_layouts:
            return
        self._warned_missing_layouts.add(key)
        print(
            f"WARNING: guide '{guide_slug}' declares layout '{layout}' but no capture exists at "
            f"screenshots/{scenario_slug}/{locale}/{layout}/ — falling back to the coarser capture. "
            f"The '{layout}' view will show the same screenshots as the others. "
            f"Recapture with: steppy run --guide --layouts {layout}",
            file=sys.stderr,
        )

    def _rel(self, path: Path) -> str:
        """Path key for the results dict — project-relative when possible."""
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    def _copy_binary(self, src: Path, dest: Path, results: dict[str, str], dry_run: bool) -> None:
        """Copy a vendored binary asset, recording the same
        ``planned|unchanged|updated|generated`` status ``_write_file`` uses.

        NOTE: the ``unchanged``/``updated`` branches are currently unreachable on
        a real build. ``build_all`` rmtrees both ``output_dir`` and
        ``assets_dir`` before any writer runs, and every writer targets a path
        under one of them, so ``dest.exists()`` is always False and every build
        reports ``N generated, 0 updated, 0 unchanged``. The byte-compare is
        kept because it costs one read of a file we were about to read anyway
        and it is what makes this helper correct if incremental builds are ever
        introduced — but do not read it as evidence that they exist today.
        """
        rel = self._rel(dest)
        if dry_run:
            results[rel] = "planned"
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        existed = dest.exists()
        if existed and dest.read_bytes() == src.read_bytes():
            results[rel] = "unchanged"
        else:
            shutil.copy2(src, dest)
            results[rel] = "updated" if existed else "generated"

    def _resolve_assets_dir(self, output_dir: Path) -> Path:
        """Pick the assets directory based on target.

        Static target writes screenshots into ``outputDir/assets/`` so the
        build is self-contained and pages can use relative URLs that work on
        any deploy root. Angular target uses the configured ``assetsDir``
        (typically the host app's ``public/`` folder).
        """
        if self._is_static():
            return output_dir / "assets"
        return self._resolve_path(self.build_config.assets_dir)

    def validate(self) -> None:
        """Validate that all referenced files exist and config is consistent.

        Raises:
            BuildError: On any validation failure.
        """
        scenarios_root = self._resolve_path(self.build_config.scenarios_root)
        screenshots_dir = self._screenshots_root()

        if not scenarios_root.exists():
            raise BuildError(f"Scenarios root not found: {scenarios_root}")

        if not screenshots_dir.exists():
            raise BuildError(f"Screenshots directory not found: {screenshots_dir}")

        for entry in self.build_config.guides:
            scenario_path = scenarios_root / entry.scenario
            if not scenario_path.exists():
                raise BuildError(f"Scenario file not found for guide '{entry.slug}': {scenario_path}")

    def build_all(
        self,
        dry_run: bool = False,
        *,
        pdf: bool = False,
        handbook: bool = False,
        video: bool = False,
    ) -> dict[str, str]:
        """Build all artifacts for the configured target.

        Args:
            dry_run: If True, compute what would be written but don't write.
            pdf: Also print every emitted guide page to PDF and link it from
                the page (static target only).
            handbook: Also assemble ``handbook/<locale>.html`` + ``.pdf`` — the
                whole site as one document — and link it from the index.
            video: Also render a narrated MP4 per (locale, layout) for each
                guide declaring ``modes: ['video']``. Both gates are required:
                the flag is the cost decision, the mode is the content one.

        Returns:
            Dict mapping file paths (relative to project root) to status
            ("generated", "updated", "unchanged", "planned").
        """
        self.validate()
        self._arm_pdf_export(pdf=pdf, handbook=handbook)
        self._arm_video_export(video=video)
        results: dict[str, str] = {}

        output_dir = self._resolve_path(self.build_config.output_dir)
        assets_dir = self._resolve_assets_dir(output_dir)
        # Before the wipe: a missing or stale tour tree fails the build while
        # the previous site is still intact.
        self._arm_tours(output_dir, assets_dir)

        if not dry_run:
            # Clean output directory
            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Clean assets directory
            if assets_dir.exists():
                shutil.rmtree(assets_dir)
            assets_dir.mkdir(parents=True, exist_ok=True)

        # Build each guide (target-aware: writes every per-guide artifact inline).
        for entry in self.build_config.guides:
            self._build_guide(entry, output_dir, assets_dir, results, dry_run)

        # Videos are rendered before the shared files so the index/manifest can
        # report what actually exists, and after every guide so one browser
        # covers the whole batch.
        if self._video_enabled:
            self._render_build_videos(results, dry_run)

        # Write shared site files (target-specific)
        if self._is_static():
            self._emit_static_tours(output_dir, results, dry_run)
            self._emit_static_shared(output_dir, results, dry_run)
            if self._handbook_enabled:
                self._write_static_handbooks(output_dir, results, dry_run)
            if self._pdf_enabled or self._handbook_enabled:
                self._render_build_pdfs(output_dir, results, dry_run)
        else:
            self._emit_angular_shared(output_dir, results, dry_run)

        return results

    def _onboarding_strip_for(self, entry: GuideEntry) -> Optional[dict]:
        """Return per-guide onboarding navigation context, or None.

        Shape: {prev, next, position, total, slug, title (entry's), title (onboarding's)…}
        Only emitted when the guide participates in the onboarding path.
        """
        onb = self.build_config.onboarding
        if onb is None or not onb.enabled or entry.slug not in onb.guides:
            return None
        index = onb.guides.index(entry.slug)
        total = len(onb.guides)
        guides_by_slug = self.build_config.guides_by_slug()
        prev_slug = onb.guides[index - 1] if index > 0 else None
        next_slug = onb.guides[index + 1] if index < total - 1 else None
        return {
            "title": onb.title,
            "path_slug": ONBOARDING_SLUG,
            # Integer position/total feed the Angular template's
            # ngx-translate placeholder substitution. The pre-formatted
            # *_label fields are retained for the static target template.
            "position": index + 1,
            "total": total,
            "position_label": f"step {index + 1} of {total}",
            "prev_label": "Start",
            "done_label": "Finish",
            "prev": ({"slug": prev_slug, "title": guides_by_slug[prev_slug].title} if prev_slug else None),
            "next": ({"slug": next_slug, "title": guides_by_slug[next_slug].title} if next_slug else None),
        }

    def _build_guide(
        self,
        entry: GuideEntry,
        output_dir: Path,
        assets_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        """Build artifacts for a single guide entry.

        Renders the per-locale HTML fragment(s) (target-agnostic), copies
        screenshots, then emits target-specific per-guide files (Angular
        component TS files, or static full-page HTML files).

        Writes every per-guide artifact inline and returns nothing — the shared
        emitters re-derive whatever they need from ``build_config``.
        """
        guide_dir = output_dir / entry.slug
        if not dry_run:
            guide_dir.mkdir(parents=True, exist_ok=True)

        scenarios_root = self._resolve_path(self.build_config.scenarios_root)
        screenshots_dir = self._screenshots_root()
        scenario_path = scenarios_root / entry.scenario

        # Parse the scenario
        scenario = parse_scenario_file(scenario_path)
        role = self.build_config.role

        # Build target-aware screenshot URL resolver.
        # - Angular: absolute URL ``/assets/<role>/...`` so [innerHTML]-injected
        #   images resolve from any route depth. The host app exports the
        #   generated assets folder into its ``public/assets/<role>/``.
        # - Static: relative URL from <slug>/<locale>.html → ../assets/<slug>/<locale>/[<layout>/]step-NN.png,
        #   so the build is portable across any deploy root (root domain or subpath).
        # When per-locale or per-layout subdirs aren't on disk we degrade to
        # the next coarser layout so single-language / single-layout projects
        # keep building without recapture.
        is_static = self._is_static()

        def make_url_fn(locale_segment: Optional[str], layout_segment: Optional[str]):
            segs = []
            if locale_segment:
                segs.append(locale_segment)
            if layout_segment:
                segs.append(layout_segment)
            seg = "/".join(segs) + "/" if segs else ""
            if is_static:

                def fn(slug: str, step_index: int, frame_key: Optional[str]) -> str:
                    stem = step_stem(step_index, frame_key)
                    return f"../assets/{entry.slug}/{seg}{stem}.png"
            else:

                def fn(slug: str, step_index: int, frame_key: Optional[str]) -> str:
                    stem = step_stem(step_index, frame_key)
                    return f"/assets/{role}/{entry.slug}/{seg}{stem}.png"

            return fn

        # Layouts dimension, narrowed to what this guide's scenario declares.
        # Empty list = single iteration with layout=None (back-compat);
        # non-empty = render once per (locale, layout) pair and let the
        # page/component template stitch with an in-page toggle.
        guide_layouts = self._layouts_for_guide(entry, scenario)
        guide_default_layout = self._default_layout_for(guide_layouts)
        layouts_or_default: list[Optional[str]] = list(guide_layouts) if guide_layouts else [None]

        # Render the scoped HTML fragment per (locale, layout) (shared between
        # targets). For static target we suppress the fragment's inline <style>
        # block since the static page links the shared assets/guides.css.
        fragment_tpl = self.env.get_template("guide.fragment.html.j2")
        # Nested dict[locale][layout_key] -> fragment / steps. The inner key is
        # the layout name, or the literal "default" when no layouts are
        # configured. This keeps a uniform shape downstream.
        fragments_by_locale: dict[str, dict[str, str]] = {}
        steps_by_locale: dict[str, dict[str, list[dict]]] = {}
        include_styles = not self._is_static()
        for locale in self.build_config.locales:
            fragments_by_locale[locale] = {}
            steps_by_locale[locale] = {}
            for layout in layouts_or_default:
                layout_key = layout or "default"
                # Same resolver the copier and the read side use, so the URL can
                # never name a directory nothing wrote (see
                # GuideAssembler.resolve_capture_segments).
                locale_segment, layout_segment = self._guide_assembler.resolve_capture_segments(
                    scenario.slug, screenshots_dir, locale, layout
                )
                # The resolver falling back past a configured layout is not an
                # error (single-layout projects rely on it), but it IS silent
                # data loss: every layout renders the same screenshots, so the
                # published page ships a layout toggle that swaps between
                # identical images, and a step restricted to one layout is shown
                # with another layout's capture. Say so once per (guide, layout).
                if layout is not None and layout_segment is None:
                    self._warn_layout_capture_missing(entry.slug, scenario.slug, locale, layout)
                guide_steps = self._guide_assembler.prepare_guide_steps(
                    scenario,
                    role,
                    locale,
                    screenshots_dir,
                    screenshot_url_fn=make_url_fn(locale_segment, layout_segment),
                    layout=layout,
                )
                html_content = fragment_tpl.render(
                    title=pick_locale(
                        entry.title,
                        locale,
                        self.build_config.default_locale,
                    ),
                    role=role.title(),
                    steps=guide_steps,
                    include_styles=include_styles,
                )
                fragments_by_locale[locale][layout_key] = html_content
                steps_by_locale[locale][layout_key] = guide_steps
                # The handbook is a one-document read: it takes each guide at
                # its default layout rather than stacking every layout's copy.
                if self._handbook_enabled and layout_key == (guide_default_layout or "default"):
                    self._handbook_fragments.setdefault(locale, {})[entry.slug] = html_content

        cursor_timings = scenario.guide.cursor.model_dump() if scenario.guide else {}

        # Resolve the effective cursor mode for this guide. Per-guide override
        # wins; otherwise fall through to the project-wide default. We keep
        # ``None`` when the resolved value is ``arrow`` so the generated host
        # templates stay byte-identical with the pre-feature default.
        effective_cursor = entry.cursor or self.build_config.cursor
        cursor_mode = effective_cursor.value if effective_cursor != CursorMode.ARROW else None

        # Resolve the effective presentation template (per-guide override wins).
        # Always a concrete string — unlike cursor there is no legacy host
        # output to keep byte-identical, and an always-present field is simpler
        # for the player and clearer in the emitted artifacts.
        template = (entry.template or self.build_config.default_template).value

        # Copy screenshots (shared between targets — different destination only)
        self._copy_screenshots(
            entry,
            scenario,
            screenshots_dir,
            assets_dir,
            results,
            dry_run,
            layouts=guide_layouts,
        )

        # Video job collection runs before the page emitters so they can link
        # what is about to be rendered (same shape as pdf_url). Nothing is
        # rendered here — `_render_build_videos` does the batch at the end.
        if self._video_enabled and GuideMode.VIDEO in entry.modes:
            self._emit_video_guide(
                entry,
                scenario,
                role,
                guide_dir,
                assets_dir,
                cursor_timings,
                results,
                dry_run,
                cursor_mode=cursor_mode,
                template=template,
                layouts=guide_layouts,
                default_layout=guide_default_layout,
            )

        # Per-target per-guide writes
        if self._is_static():
            self._emit_static_guide(
                entry,
                guide_dir,
                fragments_by_locale,
                steps_by_locale,
                cursor_timings,
                results,
                dry_run,
                layouts=guide_layouts,
                default_layout=guide_default_layout,
            )
        else:
            self._emit_angular_guide(
                entry,
                guide_dir,
                fragments_by_locale,
                steps_by_locale,
                cursor_timings,
                results,
                dry_run,
                cursor_mode=cursor_mode,
                template=template,
                layouts=guide_layouts,
                default_layout=guide_default_layout,
            )

        # Interactive manifest + per-guide page (when the guide opts in via
        # modes: ['interactive']). Stage 4: both targets are wired.
        # Video mode does NOT need this: it builds its own manifest with
        # disk-relative screenshot URLs (see _build_video_manifest).
        if GuideMode.INTERACTIVE in entry.modes:
            self._emit_interactive_guide(
                entry,
                scenario,
                role,
                guide_dir,
                assets_dir,
                cursor_timings,
                results,
                dry_run,
                cursor_mode=cursor_mode,
                template=template,
                layouts=guide_layouts,
                default_layout=guide_default_layout,
            )

        # Remember the effective layout set so the site manifest can report it
        # per guide (the top-level `layouts` stays the site-wide dimension).
        self._guide_layouts[entry.slug] = list(guide_layouts)

    # ── Per-guide emitters ───────────────────────────────────────────────

    def _emit_angular_guide(
        self,
        entry: GuideEntry,
        guide_dir: Path,
        fragments_by_locale: dict[str, dict[str, str]],
        steps_by_locale: dict[str, dict[str, list[dict]]],
        cursor_timings: dict,
        results: dict[str, str],
        dry_run: bool,
        *,
        cursor_mode: Optional[str] = None,
        template: str = "overlay",
        layouts: Optional[list[str]] = None,
        default_layout: Optional[str] = None,
    ) -> None:
        """Write per-guide Angular files. ``fragments_by_locale`` is a nested
        dict keyed first by locale, then by layout (or ``"default"`` when no
        layouts are configured). ``steps_by_locale`` is the matching nested
        per-step frame data — the stepbystep cursor driver consumes the slim
        ``{index, frames: [{viewport, cursor}]}`` shape baked into the content
        module."""
        # Write standalone fragment HTML files (handy for browser preview / debugging)
        for locale, fragments_by_layout in fragments_by_locale.items():
            for layout_key, html_content in fragments_by_layout.items():
                # Suffix only when multiple layouts so single-layout output keeps
                # the existing filename shape.
                layout_suffix = f"-{layout_key}" if len(fragments_by_layout) > 1 else ""
                html_filename = f"{entry.slug}-stepbystep-{locale}{layout_suffix}.html"
                self._write_file(guide_dir / html_filename, html_content, results, dry_run)

        class_name = _slug_to_class_name(entry.slug)
        has_video = self._has_video(entry)
        modes = self._emitted_modes(entry)
        has_interactive = GuideMode.INTERACTIVE in entry.modes
        has_multiple_modes = len(modes) > 1
        guide_layouts = list(layouts if layouts is not None else self.build_config.layouts)
        has_multiple_layouts = len(guide_layouts) > 1
        onboarding_strip = self._onboarding_strip_for(entry)
        role_namespace = _role_namespace(self.build_config.role)
        # Initial value for the component's local layout signal. With layouts
        # configured, use this guide's default; otherwise the literal
        # "default" key which the content map collapses to.
        layout_signal_default = default_layout or (guide_layouts[0] if guide_layouts else "default")

        # Content module — shape is content[locale][layout_key] = fragment,
        # plus steps[locale][layout_key] = slim per-step frame data
        # ({index, frames: [{viewport, cursor}]}) that the stepbystep cursor
        # driver animates over the [innerHTML]-injected DOM. The Angular
        # component picks the active locale via ngx-translate's TranslateService
        # and the active layout via a local signal; single-layout configs
        # collapse to a single "default" key.
        steps_by_locale_slim = {
            locale: {
                layout_key: [
                    {
                        "index": step["index"],
                        "frames": [
                            {
                                "viewport": frame.get("viewport"),
                                "cursor": frame.get("cursor"),
                            }
                            for frame in step.get("frames", [])
                        ],
                    }
                    for step in steps
                ]
                for layout_key, steps in by_layout.items()
            }
            for locale, by_layout in steps_by_locale.items()
        }
        content_tpl = self.env.get_template("angular/content.ts.j2")
        content_ts = content_tpl.render(
            content_by_locale=fragments_by_locale,
            steps_by_locale=steps_by_locale_slim,
            cursor_timings=cursor_timings,
        )
        self._write_file(guide_dir / f"{entry.slug}.content.ts", content_ts, results, dry_run)

        # Component
        component_tpl = self.env.get_template("angular/component.ts.j2")
        component_ts = component_tpl.render(
            slug=entry.slug,
            class_name=class_name,
            role=self.build_config.role,
            role_namespace=role_namespace,
            default_locale=self.build_config.default_locale,
            layouts=guide_layouts,
            default_layout=layout_signal_default,
            modes=modes,
            mode_labels=MODE_LABELS,
            default_mode=modes[0],
            has_interactive=has_interactive,
            has_video=has_video,
            has_multiple_modes=has_multiple_modes,
            has_multiple_layouts=has_multiple_layouts,
            onboarding_strip=onboarding_strip,
        )
        self._write_file(guide_dir / f"{entry.slug}.component.ts", component_ts, results, dry_run)

        # Interactive placeholder component (if needed)
        if has_interactive:
            interactive_tpl = self.env.get_template("angular/component-interactive.ts.j2")
            interactive_ts = interactive_tpl.render(
                slug=entry.slug,
                class_name=class_name,
                role=self.build_config.role,
                role_namespace=role_namespace,
                default_locale=self.build_config.default_locale,
                default_layout=default_layout,
                has_multiple_layouts=has_multiple_layouts,
                cursor_mode=cursor_mode,
                default_template=template,
            )
            self._write_file(
                guide_dir / f"{entry.slug}-interactive.component.ts",
                interactive_ts,
                results,
                dry_run,
            )

        # Video host component (only when a video was actually rendered).
        if has_video:
            media = self._video_media[entry.slug]
            videos = {
                key: {
                    "file": Path(item["src"]).name,
                    "poster_file": Path(item["poster"]).name,
                    "captions_file": Path(item["captions"]).name,
                }
                for key, item in media.items()
            }
            video_ts = self.env.get_template("angular/component-video.ts.j2").render(
                slug=entry.slug,
                class_name=class_name,
                role=self.build_config.role,
                default_locale=self.build_config.default_locale,
                default_layout=default_layout,
                has_multiple_layouts=has_multiple_layouts,
                videos=videos,
                default_key=next(iter(videos)),
            )
            self._write_file(
                guide_dir / f"{entry.slug}-video.component.ts",
                video_ts,
                results,
                dry_run,
            )

    def _emit_static_guide(
        self,
        entry: GuideEntry,
        guide_dir: Path,
        fragments_by_locale: dict[str, dict[str, str]],
        steps_by_locale: dict[str, dict[str, list[dict]]],
        cursor_timings: dict,
        results: dict[str, str],
        dry_run: bool,
        *,
        layouts: Optional[list[str]] = None,
        default_layout: Optional[str] = None,
    ) -> None:
        """Write per-guide static HTML pages — one full page per locale.

        When multiple layouts are configured, each page bundles all layouts'
        fragments under ``data-layout="..."`` wrappers so the in-page toggle
        can swap between them client-side.
        """
        page_tpl = self.env.get_template("static/page.html.j2")
        groups_by_slug = self.build_config.groups_by_slug()
        group = groups_by_slug.get(entry.group) if entry.group else None
        onboarding_strip = self._onboarding_strip_for(entry)
        layout_keys = list(layouts if layouts is not None else self.build_config.layouts)
        if default_layout is None:
            default_layout = self._default_layout_for(layout_keys)
        for locale, fragments_by_layout in fragments_by_locale.items():
            lang_links = self._lang_switcher_links(
                href_for=lambda loc: f"./{loc}.html",
                current_locale=locale,
            )
            steps_by_layout = steps_by_locale.get(locale, {})
            seo = self._seo_for_page(
                locale=locale,
                dir_path=entry.slug,
                title=entry.title,
                description=entry.description or entry.title,
                og_image_site_rel=self._first_screenshot_site_rel(steps_by_layout, default_layout),
            )
            # `fragments_by_layout` uses key "default" when no layouts are
            # configured (back-compat). The page template detects multi-layout
            # mode by checking len(layout_keys) > 1.
            page_html = page_tpl.render(
                entry=entry,
                site_title=self.build_config.site.title,
                locale=locale,
                html_lang=self._html_lang(locale),
                index_href=self._index_href(locale),
                current_locale=locale,
                locales=self.build_config.locales,
                default_locale=self.build_config.default_locale,
                locale_labels=self.locale_labels,
                lang_links=lang_links,
                fragments_by_layout=fragments_by_layout,
                steps_by_layout=steps_by_layout,
                layouts=layout_keys,
                default_layout=default_layout,
                cursor_timings=cursor_timings,
                group=group,
                onboarding_strip=onboarding_strip,
                seo=seo,
                # Sibling of the page itself; None unless --pdf armed the export.
                pdf_url=f"./{locale}.pdf" if self._pdf_enabled else None,
                handbook_url=(f"../{self._handbook_href(locale)}" if self._handbook_enabled else None),
                mode_links=self._mode_links(entry, "stepbystep", locale),
            )
            page_path = guide_dir / f"{locale}.html"
            self._write_file(page_path, page_html, results, dry_run)
            if self._pdf_enabled:
                self._pdf_pages.append(
                    (page_path, pick_locale(entry.title, locale, self.build_config.default_locale) or entry.slug)
                )

        # Bare `<slug>/` used to 404: groups, onboarding and the handbook all got
        # a stub and guides did not. Excluded from the sitemap like the others.
        self._write_file(
            guide_dir / "index.html",
            self._dir_redirect_stub(entry.slug),
            results,
            dry_run,
        )

        if entry.slug in self._video_media:
            self._emit_static_video_page(entry, guide_dir, results, dry_run)

    def _emit_static_video_page(
        self,
        entry: GuideEntry,
        guide_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        """One ``<slug>/video.html`` playing every locale/layout variant.

        A sibling page per mode, matching how ``interactive.html`` works — the
        static target has no in-page mode toggle.
        """
        media = self._video_media.get(entry.slug) or {}
        if not media:
            return
        variants = [
            {
                "key": key,
                "label": self._variant_label(item),
                "locale": item["locale"],
                "layout": item["layout"],
                "src": item["src"],
                "poster": item["poster"],
                "captions": item["captions"],
            }
            for key, item in media.items()
        ]
        page_html = self.env.get_template("static/video.html.j2").render(
            entry=entry,
            site_title=self.build_config.site.title,
            default_locale=self.build_config.default_locale,
            html_lang=self._html_lang(self.build_config.default_locale),
            index_href=self._index_href(self.build_config.default_locale),
            mode_links=self._mode_links(entry, "video", self.build_config.default_locale),
            variants=variants,
            # Canonical to the step-by-step page for the same reason the
            # interactive page does -- see _guide_primary_site_rel.
            seo=self._seo_for_page(
                locale=self.build_config.default_locale,
                site_rel=self._guide_primary_site_rel(entry),
                title=entry.title,
                description=entry.description or entry.title,
                alternates=[],
            ),
        )
        self._write_file(guide_dir / "video.html", page_html, results, dry_run)

    def _variant_label(self, item: dict) -> str:
        locale = self.locale_labels.get(item["locale"], item["locale"])
        layout = item.get("layout")
        return f"{locale} · {layout.capitalize()}" if layout else locale

    def _emit_interactive_guide(
        self,
        entry: GuideEntry,
        scenario,
        role: str,
        guide_dir: Path,
        assets_dir: Path,
        cursor_timings: dict,
        results: dict[str, str],
        dry_run: bool,
        *,
        cursor_mode: Optional[str] = None,
        template: str = "overlay",
        layouts: Optional[list[str]] = None,
        default_layout: Optional[str] = None,
    ) -> None:
        """Emit per-guide interactive artifacts.

        Both targets:
          * Render the guide manifest (``guide.json``).

        Static target additionally emits an ``interactive.html`` page that
        loads the player bundle and mounts ``<steppy-guide>``. The bundle
        itself is copied once-per-build by ``_copy_player_bundle_if_needed``.

        Angular target emits only the manifest — the per-guide
        ``<slug>-interactive.component.ts`` (rendered by ``_emit_angular_guide``)
        mounts ``<steppy-guide>`` against the manifest URL.
        """
        is_static = self._is_static()
        screenshots_dir = self._screenshots_root()
        guide_layouts = list(layouts if layouts is not None else self.build_config.layouts)
        if default_layout is None:
            default_layout = self._default_layout_for(guide_layouts)

        def screenshot_url(
            slug: str,
            step_index: int,
            frame_key: Optional[str],
            locale: str,
            layout: Optional[str],
        ) -> str:
            stem = step_stem(step_index, frame_key)
            # Same resolver as the copier and the stepbystep URL builder. This
            # closure used to hardcode `segs = [locale]`, so a project with flat
            # (locale-less) captures got a manifest pointing at
            # assets/<slug>/<locale>/… that the copier never wrote: the
            # stepbystep page rendered fine while the interactive page showed a
            # blank stage on every step, and the build reported success.
            segs = [
                seg
                for seg in self._guide_assembler.resolve_capture_segments(
                    scenario.slug, screenshots_dir, locale, layout
                )
                if seg
            ]
            seg = "/".join(segs) + "/" if segs else ""
            if is_static:
                # Manifest at output_dir/<slug>/guide.json → screenshots at
                # output_dir/assets/<slug>/<locale>/<layout>/step-NN.png.
                return f"../assets/{entry.slug}/{seg}{stem}.png"
            # Angular: absolute root-relative URL served from host's public/.
            return f"/assets/{role}/{entry.slug}/{seg}{stem}.png"

        # Title is a LocalizedText. Normalize to {locale: str} for every
        # declared locale, falling back to default_locale (then to the raw
        # string for the plain-string LocalizedText form).
        resolved_title: dict[str, str] = {}
        for locale in self.build_config.locales:
            value = pick_locale(
                entry.title,
                locale,
                self.build_config.default_locale,
            )
            if value is None:
                raise BuildError(f"Interactive manifest for '{entry.slug}': title is empty for locale '{locale}'.")
            resolved_title[locale] = value

        try:
            manifest = self._guide_assembler.prepare_interactive_manifest(
                scenario=scenario,
                role=role,
                screenshots_dir=self._screenshots_root(),
                locales=list(self.build_config.locales),
                layouts=guide_layouts,
                default_locale=self.build_config.default_locale,
                default_layout=default_layout,
                title=resolved_title,
                cursor_timings=cursor_timings,
                screenshot_url_fn=screenshot_url,
                cursor_mode=cursor_mode,
                default_template=template,
            )
        except ValueError as exc:
            raise BuildError(str(exc)) from exc

        # Manifest destination:
        # - Static: output_dir/<slug>/guide.json (sibling to the per-locale HTML pages).
        # - Angular: assets_dir/<slug>/guide.json (alongside the screenshots, so
        #   the host's public/assets/<role>/<slug>/ contains both).
        if is_static:
            manifest_path = guide_dir / "guide.json"
        else:
            manifest_path = assets_dir / entry.slug / "guide.json"

        manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        self._write_file(manifest_path, manifest_json, results, dry_run)

        if is_static:
            # Static target: render an interactive.html alongside the per-locale
            # stepbystep pages. The bundle is copied once-per-build via
            # ``_copy_player_bundle_if_needed``.
            page_tpl = self.env.get_template("static/interactive.html.j2")
            # Canonical to the step-by-step page, not to itself: this renders
            # the same steps in a different presentation, and a self-canonical
            # here is what had every guide occupying three or four indexable
            # URLs. No hreflang -- a page that canonicals elsewhere must not
            # carry alternates, and this one serves every locale at one URL.
            interactive_seo = self._seo_for_page(
                locale=self.build_config.default_locale,
                site_rel=self._guide_primary_site_rel(entry),
                title=entry.title,
                description=entry.description or entry.title,
                alternates=[],
            )
            page_html = page_tpl.render(
                entry=entry,
                site_title=self.build_config.site.title,
                default_locale=self.build_config.default_locale,
                html_lang=self._html_lang(self.build_config.default_locale),
                index_href=self._index_href(self.build_config.default_locale),
                mode_links=self._mode_links(entry, "interactive", self.build_config.default_locale),
                default_layout=default_layout,
                layouts=guide_layouts,
                pick_locale=pick_locale,
                cursor_mode=cursor_mode,
                default_template=template,
                seo=interactive_seo,
            )
            self._write_file(
                guide_dir / "interactive.html",
                page_html,
                results,
                dry_run,
            )

    def _copy_player_bundle_if_needed(
        self,
        output_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        """Copy the ESM player bundle into the build output once, when at
        least one guide opts into interactive mode.

        Destination depends on the target:
          * Angular: ``assets_dir/steppy-player.js`` so the host serves it at
            ``/assets/<role>/steppy-player.js`` (matching the URL the
            generated component fetches).
          * Static: ``output_dir/assets/steppy-player.js``, referenced from
            ``<slug>/interactive.html`` via the relative path
            ``../assets/steppy-player.js``.
        """
        has_interactive = any(GuideMode.INTERACTIVE in g.modes for g in self.build_config.guides)
        if not has_interactive:
            return
        # _resolve_assets_dir already returns output_dir/"assets" for the static
        # target, so both targets resolve through it.
        self._copy_binary(
            get_player_bundle_path(),
            self._resolve_assets_dir(output_dir) / "steppy-player.js",
            results,
            dry_run,
        )

    def _copy_cursor_bundle(
        self,
        output_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        """Copy the legacy cursor IIFE bundle into the Angular assets dir as
        ``steppy-cursor.js`` so the host serves it at
        ``/assets/<role>/steppy-cursor.js``.

        Every Angular guide has a stepbystep view, and ``<script>`` tags
        injected via Angular's ``[innerHTML]`` never execute — so the cursor
        driver can't ship inside the rendered fragment. The generated component
        loads this bundle once and calls ``window.__STEPPY_CURSOR__.mount(...)``
        after each render. Static target has no equivalent: it inlines the same
        source via ``_write_static_cursor_js`` (``get_legacy_cursor_iife``).
        """
        self._copy_binary(
            get_cursor_bundle_path(),
            self._resolve_assets_dir(output_dir) / "steppy-cursor.js",
            results,
            dry_run,
        )

    # ── Video (both targets) ─────────────────────────────────────────────

    def _arm_video_export(self, *, video: bool) -> None:
        """Validate and record the video-export request for this build.

        Fails early on purpose. A render is minutes of CPU per (guide, locale,
        layout) and, with a paid TTS provider, real money — discovering that
        ffmpeg is missing at the end of that is the worst possible time.
        """
        self._video_jobs = []
        self._video_media = {}
        self._video_enabled = video
        if not video:
            return

        declared = [g.slug for g in self.build_config.guides if GuideMode.VIDEO in g.modes]
        if not declared:
            _warn(
                "--video was passed but no guide declares modes: ['video'] — nothing to render. "
                "Add the mode to the guides you want videos for in the build config."
            )
            self._video_enabled = False
            return

        if self.steppy_config is None:
            # build tolerates a missing config.yaml, but silently defaulting here
            # would ship a paid-TTS project a set of silent videos with no clue why.
            _warn(
                "--video was passed but no config.yaml was found; using default video settings. "
                "Narration will be captions-only (video.tts.provider defaults to 'none')."
            )

        from .video import find_ffmpeg

        find_ffmpeg(self._video_config())

    def _video_config(self):
        """Video settings from ``config.yaml``, or the defaults.

        ``steppy build`` treats a missing config as non-fatal (it only needs it
        for role display names), so mirror ``_pdf_config`` rather than failing —
        ``_arm_video_export`` has already warned about the consequence.
        """
        from .models import VideoConfig

        return self.steppy_config.video if self.steppy_config else VideoConfig()

    def _emit_video_guide(
        self,
        entry: GuideEntry,
        scenario,
        role: str,
        guide_dir: Path,
        assets_dir: Path,
        cursor_timings: dict,
        results: dict[str, str],
        dry_run: bool,
        *,
        cursor_mode: Optional[str] = None,
        template: str = "overlay",
        layouts: Optional[list[str]] = None,
        default_layout: Optional[str] = None,
    ) -> None:
        """Collect the render jobs for one guide; render nothing yet.

        Rendering is deferred to ``_render_build_videos`` so a whole build shares
        one browser, exactly as the PDF pass does.

        The manifest built here is **not** the published one: screenshot URLs are
        resolved relative to the harness on disk rather than to the deployed site.
        The Angular target's published URLs are root-absolute (``/assets/<role>/…``)
        and would resolve to the filesystem root over ``file://``; making the
        renderer own its own URL shape means it does not care which target it is
        serving, and Angular gets video for free.
        """
        options = self._video_config()
        screenshots_dir = self._screenshots_root()
        guide_layouts = list(layouts if layouts is not None else self.build_config.layouts)
        if default_layout is None:
            default_layout = self._default_layout_for(guide_layouts)
        media: dict[str, dict[str, str]] = {}

        # One video per (locale, layout) — but two layouts that resolve to the
        # same captures would render the same footage twice, at full price.
        seen_segments: dict[tuple[str, tuple], str] = {}

        for locale in self.build_config.locales:
            for layout in guide_layouts or [None]:
                segments = self._guide_assembler.resolve_capture_segments(
                    scenario.slug, screenshots_dir, locale, layout
                )
                stem = locale if not layout or len(guide_layouts) <= 1 else f"{locale}-{layout}"
                out = guide_dir / f"{stem}.mp4" if self._is_static() else assets_dir / entry.slug / f"{stem}.mp4"

                duplicate_of = seen_segments.get((locale, tuple(segments)))
                if duplicate_of is not None:
                    _warn(
                        f"guide '{entry.slug}' ({locale}): layout '{layout}' resolves to the same "
                        f"captures as '{duplicate_of}', so its video would be identical. Rendering "
                        f"once. Capture the layout (steppy run --guide --layouts …) or drop it from "
                        f"the guide's layouts."
                    )
                    media[stem] = dict(media[duplicate_of])
                    continue
                seen_segments[(locale, tuple(segments))] = stem

                manifest = self._build_video_manifest(
                    entry,
                    scenario,
                    role,
                    locale=locale,
                    layout=layout,
                    page_dir=guide_dir,
                    assets_dir=assets_dir,
                    screenshots_dir=screenshots_dir,
                    cursor_timings=cursor_timings,
                    cursor_mode=cursor_mode,
                    template=template,
                    default_layout=default_layout,
                    guide_layouts=guide_layouts,
                )

                job = self._video_job(entry, manifest, guide_dir, out, locale, layout, options)
                self._video_jobs.append(job)
                for path in (job.out, job.vtt, job.poster):
                    self._record_artifact(path, results, dry_run)
                media[stem] = {
                    "locale": locale,
                    "layout": layout or "",
                    "src": self._media_href(out, guide_dir),
                    "poster": self._media_href(job.poster, guide_dir),
                    "captions": self._media_href(job.vtt, guide_dir),
                }

        self._video_media[entry.slug] = media

    def _media_href(self, path: Path, page_dir: Path) -> str:
        """URL of a media file relative to the page that embeds it."""
        return Path(os.path.relpath(path, page_dir)).as_posix()

    def _build_video_manifest(
        self,
        entry: GuideEntry,
        scenario,
        role: str,
        *,
        locale: str,
        layout: Optional[str],
        page_dir: Path,
        assets_dir: Path,
        screenshots_dir: Path,
        cursor_timings: dict,
        cursor_mode: Optional[str],
        template: str,
        default_layout: Optional[str],
        guide_layouts: list[str],
    ) -> dict:
        """Manifest for one (locale, layout), with disk-relative screenshot URLs."""

        def screenshot_url(slug, step_index, frame_key, url_locale, url_layout):
            stem = step_stem(step_index, frame_key)
            segs = [
                seg
                for seg in self._guide_assembler.resolve_capture_segments(
                    scenario.slug, screenshots_dir, url_locale, url_layout
                )
                if seg
            ]
            target = (assets_dir / entry.slug).joinpath(*segs) / f"{stem}.png"
            return self._media_href(target, page_dir)

        title = pick_locale(entry.title, locale, self.build_config.default_locale)
        if title is None:
            raise BuildError(f"Video for '{entry.slug}': title is empty for locale '{locale}'.")

        try:
            return self._guide_assembler.prepare_interactive_manifest(
                scenario=scenario,
                role=role,
                screenshots_dir=screenshots_dir,
                locales=[locale],
                layouts=[layout] if layout else [],
                default_locale=locale,
                default_layout=layout,
                title={locale: title},
                cursor_timings=cursor_timings,
                screenshot_url_fn=screenshot_url,
                cursor_mode=cursor_mode,
                default_template=template,
            )
        except ValueError as exc:
            raise BuildError(f"Video for '{entry.slug}' ({locale}): {exc}") from exc

    def _video_job(self, entry, manifest, page_dir, out, locale, layout, options):
        from .video import VideoJob

        default_locale = self.build_config.default_locale
        return VideoJob(
            manifest=manifest,
            page_dir=page_dir,
            out=out,
            locale=locale,
            layout=layout,
            title=pick_locale(entry.title, locale, default_locale) or entry.slug,
            subtitle=pick_locale(self.build_config.site.title, locale, default_locale) or "",
            outro=pick_locale(self.build_config.site.title, locale, default_locale) or "",
            stamp=self._video_stamp(entry, options, locale, layout),
            logo_data_uri=self._logo_data_uri(options),
        )

    def _video_stamp(self, entry: GuideEntry, options, locale: str, layout: Optional[str]) -> str:
        """Render the burned-in provenance line, or "" when disabled."""
        if not options.stamp.enabled:
            return ""
        from datetime import date

        return options.stamp.text.format(
            version=options.stamp.version,
            date=date.today().isoformat(),
            locale=locale,
            layout=layout or "",
            slug=entry.slug,
        )

    def _logo_data_uri(self, options) -> str:
        """Embed the branding logo as a data URI.

        The render page has no network and loads over ``file://``, so a path
        would only sometimes resolve; inlining always does.
        """
        if not options.branding.logo:
            return ""
        path = self._resolve_path(options.branding.logo)
        if not path.is_file():
            _warn(f"video.branding.logo not found: {path}. The watermark will be omitted.")
            return ""
        import base64
        import mimetypes

        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    def _render_build_videos(self, results: dict[str, str], dry_run: bool) -> None:
        """Render every collected job, sharing one browser for the batch.

        A dry run has already recorded what *would* be written and must never
        launch a browser or an encoder.
        """
        if dry_run or not self._video_jobs:
            return

        from .tts import assemble_track
        from .video import render_videos

        options = self._video_config()
        jobs = self._synthesize_narration(options)

        failures = [
            r
            for r in render_videos(
                jobs,
                options,
                assemble_audio=assemble_track,
                cache_root=self._resolve_path(options.cache_dir),
                on_warning=_warn,
            )
            if not r.ok
        ]
        if failures:
            detail = "\n".join(f"  {r.job.out.name}: {r.error}" for r in failures)
            raise BuildError(f"{len(failures)} video(s) failed to render:\n{detail}")

    def _synthesize_narration(self, options) -> list:
        """Voice every job's steps before any browser opens.

        Thin wrapper over the shared ``tts.voice_jobs`` — the only thing a build
        adds is resolving the cache against the project root, so a build
        launched from a subdirectory hits the same warm cache instead of
        re-billing against an empty one.
        """
        from .tts import voice_jobs
        from .video import find_ffmpeg

        if options.tts.provider == "none":
            return list(self._video_jobs)

        cache_root = self._resolve_path(options.tts.cache_dir)
        return voice_jobs(
            self._video_jobs,
            options.tts,
            ffmpeg=find_ffmpeg(options),
            cache_root=cache_root,
            work_dir=cache_root / "work",
            on_warning=_warn,
        )

    # ── Screenshots (shared) ─────────────────────────────────────────────

    def _copy_screenshots(
        self,
        entry: GuideEntry,
        scenario,
        screenshots_dir: Path,
        assets_dir: Path,
        results: dict[str, str],
        dry_run: bool,
        *,
        layouts: Optional[list[str]] = None,
    ) -> None:
        """Copy screenshots for this guide to the assets directory.

        For each configured locale, prefers ``screenshots_dir/<slug>/<locale>/``
        and copies into ``assets_dir/<entry.slug>/<locale>/``. Falls back to
        the flat ``screenshots_dir/<slug>/`` → ``assets_dir/<entry.slug>/``
        layout when the per-locale subdir doesn't exist (single-language
        projects, or projects that haven't recaptured after enabling locales).
        """
        scenario_root = screenshots_dir / scenario.slug
        if not scenario_root.exists():
            return

        copied_dirs: set[Path] = set()

        def _copy_from(src_dir: Path, dest_dir: Path) -> None:
            if dest_dir in copied_dirs:
                return
            copied_dirs.add(dest_dir)
            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
            # JSON sidecars carry bbox + viewport metadata that the runtime
            # cursor animation reads — they must travel with the PNGs.
            for src_file in sorted([*src_dir.glob("step-*.png"), *src_dir.glob("step-*.json")]):
                dest = dest_dir / src_file.name
                rel_dest = self._rel(dest)
                if dry_run:
                    results[rel_dest] = "planned"
                else:
                    shutil.copy2(src_file, dest)
                    results[rel_dest] = "generated"

        guide_layouts = list(layouts if layouts is not None else self.build_config.layouts)
        layouts_or_default: list[Optional[str]] = guide_layouts or [None]
        for locale in self.build_config.locales:
            for layout in layouts_or_default:
                # Same resolver the URL builder and the read side use. Mirroring
                # the resolved segments into the assets tree is what guarantees
                # the emitted <img src> resolves (see
                # GuideAssembler.resolve_capture_segments).
                segments = [
                    seg
                    for seg in self._guide_assembler.resolve_capture_segments(
                        scenario.slug, screenshots_dir, locale, layout
                    )
                    if seg
                ]
                _copy_from(
                    scenario_root.joinpath(*segments),
                    (assets_dir / entry.slug).joinpath(*segments),
                )

    # ── Shared site files ────────────────────────────────────────────────

    def _emit_angular_shared(
        self,
        output_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        self._write_routes(output_dir, results, dry_run)
        self._write_nav_json(output_dir, results, dry_run)
        self._write_i18n_json(output_dir, results, dry_run)
        self._write_angular_group_components(output_dir, results, dry_run)
        self._write_angular_onboarding_component(output_dir, results, dry_run)
        self._copy_cursor_bundle(output_dir, results, dry_run)
        self._copy_player_bundle_if_needed(output_dir, results, dry_run)
        self._write_gitignore(output_dir, results, dry_run)

    # ── In-app tours: the ``guide`` target ───────────────────────────────

    def _arm_tours(self, output_dir: Path, assets_dir: Path) -> None:
        """Verify what the tours ``guide`` target will publish, before the wipe.

        Inert unless ``config.yaml`` enables tours. The tree is the one
        ``steppy generate`` / ``steppy tours`` wrote; the build only
        copies it (see ``steppy.tours.publish``). Lazily imported, like the
        rest of the tours package.
        """
        self._tours_publish = None
        tours = getattr(self.steppy_config, "tours", None)
        if tours is None or not tours.enabled:
            return
        from .tours.publish import TourPublishError, check_build_overlaps, plan_guide_publish
        from .tours.settings import ToursSettings

        settings = ToursSettings.from_config(self.steppy_config, self.project_root)
        try:
            check_build_overlaps(settings, output_dir, assets_dir)
            if not settings.publishes_guide:
                return
            if not self._is_static():
                _warn("tours: the guide target publishes only with target: 'static'; skipped")
                return
            taken = {g.slug for g in self.build_config.guides} | {g.slug for g in self.build_config.groups}
            if GUIDE_SITE_TOURS_DIR in taken:
                raise BuildError(
                    f"slug {GUIDE_SITE_TOURS_DIR!r} is reserved for the in-app tours this site publishes; "
                    "rename that guide or group"
                )
            plan = plan_guide_publish(
                settings,
                guide_slugs=[g.slug for g in self.build_config.guides],
                build_locales=self.build_config.locales,
            )
        except TourPublishError as exc:
            raise BuildError(str(exc)) from exc
        for warning in plan.warnings:
            _warn(warning)
        self._tours_publish = plan

    def _emit_static_tours(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Copy the verified tour tree to ``<site>/tours/<build-id>/``, byte for byte."""
        plan = self._tours_publish
        if plan is None:
            return
        target = output_dir / plan.site_dir
        for name, data in plan.files:  # tour files first, the manifest last
            self._write_bytes(target / name, data, results, dry_run)

    def _emit_static_shared(
        self,
        output_dir: Path,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        self._write_static_index(output_dir, results, dry_run)
        self._write_static_group_pages(output_dir, results, dry_run)
        self._write_static_onboarding_page(output_dir, results, dry_run)
        self._write_static_manifest(output_dir, results, dry_run)
        self._write_static_css(output_dir, results, dry_run)
        self._write_static_cursor_js(output_dir, results, dry_run)
        self._copy_player_bundle_if_needed(output_dir, results, dry_run)
        self._warn_root_only_files()
        self._write_static_robots(output_dir, results, dry_run)
        self._write_static_sitemap(output_dir, results, dry_run)
        self._write_nojekyll(output_dir, results, dry_run)
        self._write_static_headers(output_dir, results, dry_run)

    # ── Angular shared writers (unchanged behavior) ──────────────────────

    def _default_route_slug(self) -> str:
        """Pick the default route. Onboarding wins when present; otherwise the
        first navbar entry that resolves to a real route (group or guide); else
        the first declared guide."""
        if self.build_config.onboarding is not None and self.build_config.onboarding.enabled:
            return ONBOARDING_SLUG
        groups_by_slug = self.build_config.groups_by_slug()
        guides_by_slug = self.build_config.guides_by_slug()
        for slug in self.build_config.navbar.order:
            if slug in groups_by_slug or slug in guides_by_slug:
                return slug
        return self.build_config.guides[0].slug

    def _write_routes(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        role = self.build_config.role
        role_ns = _role_namespace(role)
        guides_data = [{"slug": g.slug, "class_name": _slug_to_class_name(g.slug)} for g in self.build_config.guides]
        groups_data = [
            {"slug": grp.slug, "class_name": _slug_to_class_name(grp.slug)} for grp in self.build_config.groups
        ]
        has_onboarding = self.build_config.onboarding is not None and self.build_config.onboarding.enabled
        tpl = self.env.get_template("angular/routes.ts.j2")
        content = tpl.render(
            role=role,
            routes_const=f"{role_ns}_ROUTES",
            guides=guides_data,
            groups=groups_data,
            has_onboarding=has_onboarding,
            onboarding_slug=ONBOARDING_SLUG,
            default_slug=self._default_route_slug(),
        )
        self._write_file(output_dir / f"{role}.routes.ts", content, results, dry_run)

    def _write_nav_json(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Emit the navigation tree as JSON with translation-key references
        instead of inlined ``LocalizedText`` maps. The host app feeds the keys
        through ngx-translate to render the navbar.
        """
        role = self.build_config.role
        role_ns = _role_namespace(role)
        guides_by_slug = self.build_config.guides_by_slug()
        onb_index = self.build_config.onboarding_index()

        def transform(node: dict) -> dict:
            kind = node["kind"]
            if kind == "onboarding":
                return {
                    "kind": "onboarding",
                    "titleKey": f"{role_ns}.onboarding.title",
                    "path": node["path"],
                    "guides": list(node["guides"]),
                }
            if kind == "group":
                entry: dict = {
                    "kind": "group",
                    "slug": node["slug"],
                    "titleKey": f"{role_ns}.groups.{node['slug']}.title",
                    "path": node["path"],
                    "children": [transform(child) for child in node["children"]],
                }
                if node.get("description") is not None:
                    entry["descriptionKey"] = f"{role_ns}.groups.{node['slug']}.description"
                return entry
            if kind == "guide":
                slug = node["slug"]
                guide_entry = guides_by_slug[slug]
                return {
                    "kind": "guide",
                    "slug": slug,
                    "titleKey": f"{role_ns}.guides.{slug}.title",
                    "path": node["path"],
                    "group": guide_entry.group,
                    "modes": self._emitted_modes(guide_entry),
                    "onboardingIndex": onb_index.get(slug),
                }
            raise BuildError(f"Unknown navigation node kind: {kind!r}")

        transformed = [transform(n) for n in self.build_config.navigation_tree()]
        content = json.dumps(transformed, indent=2, ensure_ascii=False) + "\n"
        self._write_file(output_dir / f"{role}.nav.json", content, results, dry_run)

    def _write_i18n_json(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Emit one ``i18n/<role>/<locale>.json`` per configured locale.

        Each file holds a single top-level ``<ROLE>`` namespace whose value is
        a tree of translation strings (site title, per-guide titles, per-group
        titles/descriptions, onboarding text, hardcoded UI labels). Hardcoded
        UI labels (``ui.groupEmpty``, ``onboarding.start``…) ship as English
        defaults in every locale; translators edit the generated files.
        """
        role = self.build_config.role
        role_ns = _role_namespace(role)
        cfg = self.build_config
        default_locale = cfg.default_locale
        onb = cfg.onboarding

        for locale in cfg.locales:
            guides_section = {g.slug: {"title": pick_locale(g.title, locale, default_locale)} for g in cfg.guides}
            groups_section: dict[str, dict[str, str]] = {}
            for grp in cfg.groups:
                bucket: dict[str, str] = {
                    "title": pick_locale(grp.title, locale, default_locale),
                }
                if grp.description is not None:
                    bucket["description"] = pick_locale(grp.description, locale, default_locale)
                groups_section[grp.slug] = bucket

            onboarding_section: dict[str, str] = dict(UI_LABEL_DEFAULTS["onboarding"])
            if onb is not None and onb.enabled:
                onboarding_section["title"] = pick_locale(onb.title, locale, default_locale)
                if onb.description is not None:
                    onboarding_section["description"] = pick_locale(onb.description, locale, default_locale)

            bundle = {
                role_ns: {
                    "site": {
                        "title": pick_locale(cfg.site.title, locale, default_locale),
                    },
                    "guides": guides_section,
                    "groups": groups_section,
                    "onboarding": onboarding_section,
                    "ui": dict(UI_LABEL_DEFAULTS["ui"]),
                }
            }
            content = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
            self._write_file(
                output_dir / "i18n" / role / f"{locale}.json",
                content,
                results,
                dry_run,
            )

    def _write_angular_group_components(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        if not self.build_config.groups:
            return
        role_ns = _role_namespace(self.build_config.role)
        tpl = self.env.get_template("angular/group.component.ts.j2")
        for group in self.build_config.groups:
            children = [{"slug": guide.slug} for guide in self.build_config.guides if guide.group == group.slug]
            content = tpl.render(
                role_namespace=role_ns,
                group=group,
                class_name=_slug_to_class_name(group.slug),
                children=children,
                has_description=group.description is not None,
            )
            self._write_file(
                output_dir / "_groups" / f"{group.slug}.group.component.ts",
                content,
                results,
                dry_run,
            )

    def _write_angular_onboarding_component(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        onb = self.build_config.onboarding
        if onb is None or not onb.enabled:
            return
        role_ns = _role_namespace(self.build_config.role)
        tpl = self.env.get_template("angular/onboarding.component.ts.j2")
        steps = [{"slug": slug} for slug in onb.guides]
        content = tpl.render(
            role_namespace=role_ns,
            steps=steps,
            has_description=onb.description is not None,
        )
        self._write_file(
            output_dir / "_onboarding" / "onboarding.component.ts",
            content,
            results,
            dry_run,
        )

    def _write_gitignore(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        tpl = self.env.get_template("angular/gitignore.j2")
        content = tpl.render()
        self._write_file(output_dir / ".gitignore", content, results, dry_run)

    # ── Static shared writers ────────────────────────────────────────────

    def _guides_in_order(self) -> list[GuideEntry]:
        """Return guides ordered by navbar.order, with declaration-order fallback
        for any slug not explicitly listed (mirrors the Angular navbar logic)."""
        by_slug = self.build_config.guides_by_slug()
        ordered: list[GuideEntry] = []
        seen: set[str] = set()
        for slug in self.build_config.navbar.order:
            if slug in by_slug and slug not in seen:
                ordered.append(by_slug[slug])
                seen.add(slug)
        for guide in self.build_config.guides:
            if guide.slug not in seen:
                ordered.append(guide)
                seen.add(guide.slug)
        return ordered

    def _index_filename(self, locale: str) -> str:
        """Filename for a locale-specific top-level index page."""
        if locale == self.build_config.default_locale:
            return "index.html"
        return f"index.{locale}.html"

    def _index_href(self, locale: str) -> str:
        """Link back to the site index from a page one directory deep.

        Every static page except the index itself sits at ``<dir>/<name>.html``,
        so they all share this one href. It exists because the templates used to
        re-implement ``_index_filename`` inline — three different ways, keyed on
        ``locale``, on ``current_locale``, and (on the interactive and video
        pages) on ``default_locale`` compared with itself, which was a ternary
        whose else-branch could never be taken. Four spellings of one rule.

        Note this is the *file* href, not the canonical URL: ``_page_site_rel``
        deliberately answers ``""`` for the default-locale index so the home page
        is canonical at the bare site root rather than at ``/index.html``. The
        two must stay separate.
        """
        return f"../{self._index_filename(locale)}"

    def _write_static_index(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Write one index page per locale at the project root.

        - Default locale → ``index.html``
        - Other locales → ``index.<locale>.html``

        Lang switcher links between them. Default-locale lives at
        ``index.html`` directly so the canonical URL stays clean.
        """
        tpl = self.env.get_template("static/index.html.j2")
        for locale in self.build_config.locales:
            lang_links = self._lang_switcher_links(
                href_for=lambda loc: f"./{self._index_filename(loc)}",
                current_locale=locale,
            )
            seo = self._seo_for_page(
                locale=locale,
                is_index=True,
                title=self.build_config.site.title,
                description=self.build_config.seo.description or self.build_config.site.title,
                og_type="website",
            )
            content = tpl.render(
                site_title=self.build_config.site.title,
                current_locale=locale,
                locale=locale,
                html_lang=self._html_lang(locale),
                default_locale=self.build_config.default_locale,
                locales=self.build_config.locales,
                locale_labels=self.locale_labels,
                lang_links=lang_links,
                navigation=self.build_config.navigation_tree(),
                onboarding_slug=ONBOARDING_SLUG,
                onboarding_eyebrow="Getting Started",
                group_eyebrow="Module",
                steps_label="steps",
                guides_label="guides",
                seo=seo,
                # None unless --handbook armed the export.
                handbook_url=(f"./{self._handbook_href(locale)}" if self._handbook_enabled else None),
                handbook_pdf_url=(f"./{HANDBOOK_SLUG}/{locale}.pdf" if self._handbook_enabled else None),
                handbook_label="Full handbook",
            )
            self._write_file(
                output_dir / self._index_filename(locale),
                content,
                results,
                dry_run,
            )

    def _write_static_group_pages(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Per-locale group landing pages at ``<group-slug>/<locale>.html``,
        plus a ``<group-slug>/index.html`` redirect to the default locale."""
        if not self.build_config.groups:
            return
        tpl = self.env.get_template("static/group.html.j2")
        guide_children: dict[str, list[dict]] = {g.slug: [] for g in self.build_config.groups}
        for guide in self.build_config.guides:
            if guide.group is not None:
                guide_children[guide.group].append({"kind": "guide", "slug": guide.slug, "title": guide.title})
        subgroup_children: dict[str, list[dict]] = {g.slug: [] for g in self.build_config.groups}
        for grp in self.build_config.groups:
            if grp.parent is not None:
                subgroup_children[grp.parent].append(
                    {
                        "kind": "group",
                        "slug": grp.slug,
                        "title": grp.title,
                        "description": grp.description,
                        "child_count": len(guide_children[grp.slug]),
                    }
                )
        children_by_group: dict[str, list[dict]] = {
            g.slug: subgroup_children[g.slug] + guide_children[g.slug] for g in self.build_config.groups
        }
        default_locale = self.build_config.default_locale
        for group in self.build_config.groups:
            for locale in self.build_config.locales:
                lang_links = self._lang_switcher_links(
                    href_for=lambda loc: f"./{loc}.html",
                    current_locale=locale,
                )
                seo = self._seo_for_page(
                    locale=locale,
                    dir_path=group.slug,
                    title=group.title,
                    description=group.description or group.title,
                    og_type="website",
                )
                content = tpl.render(
                    site_title=self.build_config.site.title,
                    current_locale=locale,
                    locale=locale,
                    html_lang=self._html_lang(locale),
                    index_href=self._index_href(locale),
                    default_locale=default_locale,
                    locales=self.build_config.locales,
                    locale_labels=self.locale_labels,
                    lang_links=lang_links,
                    group=group,
                    children=children_by_group[group.slug],
                    empty_label="No guides in this group yet.",
                    seo=seo,
                )
                self._write_file(
                    output_dir / group.slug / f"{locale}.html",
                    content,
                    results,
                    dry_run,
                )
            # index.html redirect to default locale
            self._write_file(
                output_dir / group.slug / "index.html",
                self._dir_redirect_stub(group.slug),
                results,
                dry_run,
            )

    def _write_static_onboarding_page(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Per-locale onboarding pages at ``<onboarding-slug>/<locale>.html``,
        plus a ``<onboarding-slug>/index.html`` redirect to the default locale."""
        onb = self.build_config.onboarding
        if onb is None or not onb.enabled:
            return
        tpl = self.env.get_template("static/onboarding.html.j2")
        guides_by_slug = self.build_config.guides_by_slug()
        steps = [guides_by_slug[slug] for slug in onb.guides]
        default_locale = self.build_config.default_locale
        for locale in self.build_config.locales:
            lang_links = self._lang_switcher_links(
                href_for=lambda loc: f"./{loc}.html",
                current_locale=locale,
            )
            seo = self._seo_for_page(
                locale=locale,
                dir_path=ONBOARDING_SLUG,
                title=onb.title,
                description=onb.description or onb.title,
                og_type="website",
            )
            content = tpl.render(
                site_title=self.build_config.site.title,
                current_locale=locale,
                locale=locale,
                html_lang=self._html_lang(locale),
                index_href=self._index_href(locale),
                default_locale=default_locale,
                locales=self.build_config.locales,
                locale_labels=self.locale_labels,
                lang_links=lang_links,
                onboarding=onb,
                steps=steps,
                start_label="Start onboarding →",
                seo=seo,
            )
            self._write_file(
                output_dir / ONBOARDING_SLUG / f"{locale}.html",
                content,
                results,
                dry_run,
            )
        self._write_file(
            output_dir / ONBOARDING_SLUG / "index.html",
            self._dir_redirect_stub(ONBOARDING_SLUG),
            results,
            dry_run,
        )

    def _write_static_manifest(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        onb = self.build_config.onboarding
        onb_index = self.build_config.onboarding_index()
        manifest = {
            "site": {"title": self.build_config.site.title},
            "locales": list(self.build_config.locales),
            "defaultLocale": self.build_config.default_locale,
            "layouts": list(self.build_config.layouts),
            "defaultLayout": self.build_config.default_layout,
            "guides": [
                {
                    "slug": g.slug,
                    "title": g.title,
                    "modes": self._emitted_modes(g),
                    "scenario": g.scenario,
                    "group": g.group,
                    "onboardingIndex": onb_index.get(g.slug),
                    # Effective layouts for THIS guide — the site-wide
                    # ``layouts`` above narrowed by the scenario's frontmatter
                    # declaration (see _layouts_for_guide).
                    "layouts": self._guide_layouts.get(g.slug, list(self.build_config.layouts)),
                    "pages": {loc: f"{g.slug}/{loc}.html" for loc in self.build_config.locales},
                }
                for g in self._guides_in_order()
            ],
            "groups": [
                {
                    "slug": grp.slug,
                    "title": grp.title,
                    "description": grp.description,
                    "guides": [guide.slug for guide in self.build_config.guides if guide.group == grp.slug],
                    "pages": {loc: f"{grp.slug}/{loc}.html" for loc in self.build_config.locales},
                }
                for grp in self.build_config.groups
            ],
            "onboarding": (
                {
                    "enabled": True,
                    "title": onb.title,
                    "description": onb.description,
                    "guides": list(onb.guides),
                    "pages": {loc: f"{ONBOARDING_SLUG}/{loc}.html" for loc in self.build_config.locales},
                }
                if onb is not None and onb.enabled
                else None
            ),
            "navigation": self.build_config.navigation_tree(),
        }
        plan = self._tours_publish
        if plan is not None:
            manifest["tours"] = {
                "buildId": plan.build_id,
                "manifest": f"{plan.site_dir}/tours.manifest.json",
                "ids": list(plan.tour_ids),
            }
        content = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        self._write_file(output_dir / "manifest.json", content, results, dry_run)

    # ── PDF export (static target) ───────────────────────────────────────

    def _arm_pdf_export(self, *, pdf: bool, handbook: bool) -> None:
        """Validate and record the PDF-export request for this build.

        Angular is rejected outright: that target emits components for a host
        app to route and render, so there is no page here to print — the host
        owns its own document.
        """
        self._pdf_pages = []
        self._handbook_fragments = {}
        self._pdf_enabled = pdf
        self._handbook_enabled = handbook
        if not (pdf or handbook):
            return
        if not self._is_static():
            raise BuildError(
                "PDF export requires target: 'static'. The angular target emits components "
                "for a host app to render, so there is no page for Steppy to print."
            )
        if handbook:
            taken = {g.slug for g in self.build_config.guides} | {g.slug for g in self.build_config.groups}
            if HANDBOOK_SLUG in taken:
                raise BuildError(
                    f"--handbook writes to '{HANDBOOK_SLUG}/', but a guide or group already uses that "
                    f"slug. Rename it, or drop --handbook."
                )

    def _record_artifact(self, path: Path, results: dict[str, str], dry_run: bool) -> None:
        """Record a **binary** artifact in the results dict.

        ``_write_file`` owns text output (and its unchanged/updated diffing);
        PDFs are written by Chromium, so they only need to be counted.
        """
        results[self._rel(path)] = "planned" if dry_run else "generated"

    def _handbook_href(self, locale: str) -> str:
        """Site-root-relative path of the handbook page for ``locale``."""
        return f"{HANDBOOK_SLUG}/{locale}.html"

    def _write_static_handbooks(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """One ``handbook/<locale>.html`` per locale: the whole site as a single
        document (cover, contents, every guide) in navigation order.

        Deliberately written one directory deep, exactly like ``<slug>/<locale>.html``:
        the guide fragments already carry ``../assets/...`` screenshot URLs, so
        they can be reused verbatim instead of re-rendered against a new depth.
        """
        tpl = self.env.get_template("guide.book.html.j2")
        css = self.env.get_template("static/guides.css").render()
        groups_by_slug = self.build_config.groups_by_slug()
        default_locale = self.build_config.default_locale
        for locale in self.build_config.locales:
            fragments = self._handbook_fragments.get(locale, {})
            entries: list[dict] = []
            current_group: Optional[str] = None
            for entry in self._guides_in_order():
                html_content = fragments.get(entry.slug)
                if html_content is None:
                    continue
                section = None
                if entry.group and entry.group != current_group:
                    group = groups_by_slug.get(entry.group)
                    if group is not None:
                        section = pick_locale(group.title, locale, default_locale)
                current_group = entry.group
                entries.append(
                    {
                        "slug": entry.slug,
                        "title": pick_locale(entry.title, locale, default_locale),
                        "section": section,
                        "html": html_content,
                    }
                )
            if not entries:
                continue
            content = tpl.render(
                title=pick_locale(self.build_config.site.title, locale, default_locale),
                subtitle=None,
                role=None,
                locale=locale,
                html_lang=self._html_lang(locale),
                guides=entries,
                guides_css=css,
            )
            self._write_file(output_dir / HANDBOOK_SLUG / f"{locale}.html", content, results, dry_run)
        # Bare-directory URL → default locale, like every other section.
        if self._handbook_fragments:
            self._write_file(
                output_dir / HANDBOOK_SLUG / "index.html",
                self._redirect_html(
                    f"./{default_locale}.html",
                    lang=self._html_lang(default_locale),
                    noindex=True,
                ),
                results,
                dry_run,
            )

    def _render_build_pdfs(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Print the emitted pages (and handbooks) to PDF.

        Runs last, over files already on disk, sharing one Chromium for the
        whole batch. A dry run only records what *would* be printed — it must
        never launch a browser.
        """
        from .pdf import PdfJob, render_pdfs

        jobs: list[PdfJob] = []
        if self._pdf_enabled:
            for html_path, title in self._pdf_pages:
                jobs.append(PdfJob(html=html_path, pdf=html_path.with_suffix(".pdf"), title=title))
        if self._handbook_enabled:
            default_locale = self.build_config.default_locale
            site_title = pick_locale(self.build_config.site.title, default_locale, default_locale) or ""
            for locale in self.build_config.locales:
                page = output_dir / HANDBOOK_SLUG / f"{locale}.html"
                if self._rel(page) in results:
                    jobs.append(PdfJob(html=page, pdf=page.with_suffix(".pdf"), title=site_title))

        for job in jobs:
            self._record_artifact(job.pdf, results, dry_run)
        if dry_run or not jobs:
            return

        failures = [r for r in render_pdfs(jobs, self._pdf_config()) if not r.ok]
        if failures:
            detail = "\n".join(f"  {r.job.pdf.name}: {r.error}" for r in failures)
            raise BuildError(f"{len(failures)} PDF(s) failed to render:\n{detail}")

    def _pdf_config(self):
        """Paper settings from ``config.yaml``. ``steppy build`` treats a
        missing config as non-fatal (it only needs it for role display names),
        so fall back to the defaults rather than failing the export."""
        from .models import PdfConfig

        return self.steppy_config.pdf if self.steppy_config else PdfConfig()

    def _write_static_css(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Render the shared guides.css through Jinja's loader so user template
        overrides in templates/static/guides.css take precedence over the bundled
        copy (same precedence rule as every other template)."""
        tpl = self.env.get_template("static/guides.css")
        content = tpl.render()
        self._write_file(output_dir / "assets" / "guides.css", content, results, dry_run)

    def _write_static_cursor_js(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Render the shared cursor animation driver to assets/cursor.js. Static
        pages link this once instead of inlining the IIFE in every page.

        The template body is just ``{{ cursor_iife }}`` — the actual animator
        source lives in ``packages/player/src/cursor/`` and is bundled into
        ``src/steppy/_player_bundle/steppy-cursor-legacy.js``."""
        tpl = self.env.get_template("static/cursor.js.j2")
        content = tpl.render(cursor_iife=get_legacy_cursor_iife())
        self._write_file(output_dir / "assets" / "cursor.js", content, results, dry_run)

    def _write_nojekyll(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Empty file disabling GitHub Pages' Jekyll preprocessing.
        Harmless on other static hosts."""
        self._write_file(output_dir / ".nojekyll", "", results, dry_run)

    def _write_static_headers(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Emit a ``_headers`` sidecar carrying ``X-Robots-Tag`` for the
        artifacts that cannot carry a robots meta of their own.

        PDFs are the reason. ``handbook/<locale>.pdf`` is Chromium printing a
        page that is deliberately ``noindex``, and ``<slug>/<locale>.pdf``
        duplicates its HTML the same way — but ``page.pdf()`` writes no robots
        metadata and a PDF has no ``<head>`` to put one in. An HTTP header is the
        only mechanism, and ``robots.txt`` is the wrong one: per this repo's own
        note in ``report/render.py``, a disallowed URL is still *listable*, it
        simply never gets fetched and therefore never learns it is noindex.

        Read by Netlify and Cloudflare Pages; **ignored by GitHub Pages and
        GitLab Pages**, which are the two the shipped CI scaffolds document — so
        for the default deployment this genuinely does nothing. Emitted anyway,
        for the reason ``.nojekyll`` is: harmless where unread, and the only
        thing available where it is read.
        """
        tpl = self.env.get_template("static/_headers.j2")
        tours = {"dir": GUIDE_SITE_TOURS_DIR} if self._tours_publish is not None else None
        content = tpl.render(public=not self._is_private_seo(), tours=tours)
        self._write_file(output_dir / "_headers", content, results, dry_run)

    def _warn_root_only_files(self) -> None:
        """Warn when ``seo.baseUrl`` carries a path.

        ``robots.txt`` and ``_headers`` are honoured only at the **origin root**,
        but a GitHub Pages *project* site is served under ``/<repo>/`` — so a
        correct ``baseUrl`` there has a path, and both files land somewhere no
        crawler and no host looks. Everything else survives: canonical links,
        hreflang and ``sitemap.xml`` are absolute, and each page still carries
        its own robots meta. It is specifically the two sidecars that go silent,
        and silently.
        """
        base = self.build_config.seo.base_url
        if not base:
            return
        prefix = urlsplit(base).path.strip("/")
        if not prefix:
            return
        _warn(
            f"seo.baseUrl '{base}' has a path, so robots.txt and _headers are written under "
            f"/{prefix}/ — neither is honoured anywhere but the origin root, so both are "
            "silently inert. Canonical links, hreflang and sitemap.xml are absolute and "
            "unaffected, and every page still carries its own robots meta."
        )

    def _write_static_robots(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Emit robots.txt. Private (default) → ``Disallow: /``; public →
        ``Allow: /`` plus a ``Sitemap:`` pointer."""
        public = not self._is_private_seo()
        tpl = self.env.get_template("static/robots.txt.j2")
        content = tpl.render(
            public=public,
            sitemap_url=self._abs_url("sitemap.xml") if public else None,
        )
        self._write_file(output_dir / "robots.txt", content, results, dry_run)

    def _sitemap_urls(self) -> list[SitemapUrl]:
        """The pages the sitemap lists: absolute URLs, deterministic order.

        The set is exactly **the pages that are their own canonical** — index,
        guides in navbar order, groups, onboarding. Everything excluded is
        excluded for a reason a reader can check in the page itself rather than
        against a list here:

        * redirect stubs (``<dir>/index.html``) canonical to their default-locale
          sibling;
        * ``interactive.html`` and ``video.html`` canonical to the guide's
          step-by-step page (see ``_guide_primary_site_rel``) — they present the
          same steps in a different shell, and listing them had every guide
          occupying three or four near-identical indexable URLs;
        * handbook pages carry an unconditional ``noindex``: one document
          duplicating every guide on the site.

        ``tests/test_build_seo.py`` asserts that correspondence directly, by
        reading the emitted HTML rather than by re-deriving this list.
        """
        cfg = self.build_config
        locales = cfg.locales
        lastmod = cfg.seo.lastmod
        urls: list[SitemapUrl] = []

        def add(dir_path: str, *, is_index: bool = False) -> None:
            alternates = tuple(
                Alternate(alt["hreflang"], alt["href"]) for alt in self._hreflang_alternates(dir_path, is_index)
            )
            for loc in locales:
                urls.append(
                    SitemapUrl(
                        loc=self._abs_url(self._page_site_rel(dir_path, loc, is_index)),
                        alternates=alternates,
                        lastmod=lastmod,
                    )
                )

        add("", is_index=True)
        for g in self._guides_in_order():
            add(g.slug)
        for grp in cfg.groups:
            add(grp.slug)
        onb = cfg.onboarding
        if onb is not None and onb.enabled:
            add(ONBOARDING_SLUG)
        return urls

    def _sitemap_override_template(self) -> Optional[str]:
        """A project's own ``static/sitemap.xml.j2``, if it has one.

        Steppy bundles no sitemap template — the default is
        ``src/steppy/sitemap.py``. But a project overriding the name still wins,
        because the override is the only way to reach the parts of the protocol
        the generator deliberately does not cover (``<image:image>`` over a
        guide's screenshots, ``<video:video>``, a ``<sitemapindex>`` split), and
        because silently ignoring a well-formed override is the exact failure
        ``_templating.py`` exists to prevent.

        The bundled-directory comparison is load-bearing rather than defensive:
        ``src/steppy/templates/`` is generated and gitignored, so a developer on
        an editable install with a stale tree would otherwise trip this branch
        against Steppy's own vendored copy.
        """
        try:
            _source, filename, _uptodate = self.env.loader.get_source(self.env, "static/sitemap.xml.j2")
        except jinja2.TemplateNotFound:
            return None
        if filename is None:
            return None
        bundled = (bundled_templates_dir() / "static" / "sitemap.xml.j2").resolve()
        if Path(filename).resolve() == bundled:
            return None
        return filename

    def _write_static_sitemap(self, output_dir: Path, results: dict[str, str], dry_run: bool) -> None:
        """Emit sitemap.xml over every indexable page (public seo mode only)."""
        if self._is_private_seo():
            return
        urls = self._sitemap_urls()
        override = self._sitemap_override_template()
        if override is not None:
            if self.build_config.seo.lastmod:
                _warn(
                    f"sitemap.xml is rendered from your template at {override}; seo.lastmod is "
                    "passed to it as `lastmod` and as each url's `.lastmod`, but the template "
                    "must emit the <lastmod> element itself."
                )
            content = self.env.get_template("static/sitemap.xml.j2").render(
                urls=urls,
                lastmod=self.build_config.seo.lastmod,
            )
        else:
            content = render_sitemap(urls)
        for message in limit_warnings(len(urls), len(content.encode("utf-8"))):
            _warn(message)
        self._write_file(output_dir / "sitemap.xml", content, results, dry_run)

    # ── File writer (shared) ─────────────────────────────────────────────

    def _write_bytes(self, path: Path, data: bytes, results: dict[str, str], dry_run: bool) -> None:
        """Write ``data`` as is and record the result.

        For files whose exact bytes matter — tour files are pinned by SRI
        hashes — where ``_write_file``'s text mode would translate newlines on
        Windows.
        """
        rel = self._rel(path)
        if dry_run:
            results[rel] = "planned"
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() == data:
                results[rel] = "unchanged"
                return
            results[rel] = "updated"
        else:
            results[rel] = "generated"
        path.write_bytes(data)

    def _write_file(
        self,
        path: Path,
        content: str,
        results: dict[str, str],
        dry_run: bool,
    ) -> None:
        """Write a file and record the result."""
        rel = self._rel(path)

        if dry_run:
            results[rel] = "planned"
            return

        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                results[rel] = "unchanged"
                return
            results[rel] = "updated"
        else:
            results[rel] = "generated"

        path.write_text(content, encoding="utf-8")
