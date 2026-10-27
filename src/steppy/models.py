"""Pydantic models for Steppy configuration and scenarios."""

import os
import re
from typing import Annotated, Any, ClassVar, Literal, Optional, Union
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from .assertions import AssertionSyntaxError, parse_assertion
from .buildid import check_build_id_syntax
from .durations import DurationSyntaxError, parse_ms
from .exclusive import normalize_group_name
from .locales import base_language, normalize_locale, validate_locale_tag
from .presets import PRESETS, IDP_PRESETS
from .testids import check_test_id_attribute
from .tour_spec import MAX_LOCALES, is_layout_name, is_media_query, is_tour_id

# Historical default locale set. Referenced in both the SteppyConfig.locales
# default and its default_locale validator so the literal lives in one place
# (FLAWS §4.4). Projects override `locales`/`default_locale` in config.yaml.
DEFAULT_LOCALES: list[str] = ["fr", "en", "nl", "de"]
DEFAULT_LOCALE: str = "fr"

# Highest scenario-format version this Steppy understands (FLAWS §6.4). A
# scenario opts in via the optional frontmatter key `steppy: <int>`; absent ⇒ 1,
# so every existing file is already a valid v1. Mirrors the version discipline of
# the coverage store (`SCHEMA_VERSION`) and the player manifest
# (`SUPPORTED_MAJOR_VERSION`). Bump only when the scenario language changes in a
# way older Steppy can't parse; a file declaring a higher version fails loudly.
SUPPORTED_SCENARIO_VERSION: int = 1


# ── Guide Text ──────────────────────────────────────────────────────────────


class GuideTextContent(BaseModel):
    """Structured content for a guide text in a single locale.

    Supports both simple string format (backward compatible) and structured format:
    - Simple: "Text here" -> auto-extracts title, uses full text as content
    - Structured: {title: "...", subtitle: "...", text: "..."}
    """

    title: str
    subtitle: Optional[str] = None
    text: Optional[str] = None

    class Config:
        extra = "forbid"


class GuideText(BaseModel):
    """Per-locale text for user guides.

    Each locale can be either:
    - A string (legacy): "Some text." -> title="Some text", text="Some text."
    - A dict (new): {title: "Title", subtitle: "Subtitle", text: "Description"}

    Locales are NOT limited to the four named fields below (FLAWS §4.4): those
    stay for backward-compatible ``.fr``/``.en`` attribute access, but any other
    locale code (es, it, pt, …) is accepted via ``extra="allow"`` and reached
    through ``.get(locale)`` — so adding a language is config-only, no model
    edit required.
    """

    fr: Optional[GuideTextContent] = None
    en: Optional[GuideTextContent] = None
    nl: Optional[GuideTextContent] = None
    de: Optional[GuideTextContent] = None

    class Config:
        extra = "allow"

    @model_validator(mode="before")
    @classmethod
    def normalize_text(cls, data):
        """Normalize every locale's value to a ``GuideTextContent`` (or None).

        Runs over ALL keys, not just the four declared fields, so extra locales
        are normalized identically. Accepts a plain string (legacy: title =
        first sentence, text = the whole string), a dict, or an already-built
        ``GuideTextContent``.
        """
        if not isinstance(data, dict):
            return data
        normalized = {}
        for locale, v in data.items():
            if v is None or isinstance(v, GuideTextContent):
                normalized[locale] = v
            elif isinstance(v, str):
                title = v.split(".")[0].strip() if "." in v else v.strip()
                normalized[locale] = GuideTextContent(title=title, text=v)
            elif isinstance(v, dict):
                normalized[locale] = GuideTextContent(**v)
            else:
                normalized[locale] = v
        return normalized

    def get(self, locale: str) -> Optional[GuideTextContent]:
        """Get structured text content for a specific locale.

        Works for both the declared fields and ``extra="allow"`` locales, since
        Pydantic exposes extras as attributes.

        Falls back to the bare language subtag (``fr-BE`` -> ``fr``), the same
        rung ``build.pick_locale`` uses for titles and descriptions. Without it a
        regional build behaved two different ways for one cause: the guide path
        raised ``Missing guide-text for locale 'fr-BE'`` and stopped the build,
        while the interactive-manifest path silently emitted nulls. An author who
        wants distinct regional wording still writes ``fr-BE:`` and it wins.

        The **query** is truncated, never widened: ``fr`` must not match a block
        that declares only ``fr-BE``, because with ``fr-BE`` and ``fr-CA`` both
        present the choice would be arbitrary and attribute-order dependent.
        """
        found = getattr(self, locale, None)
        if found is not None:
            return found
        bare = base_language(locale)
        if bare != locale:
            return getattr(self, bare, None)
        return None


# ── Guide Cursor ────────────────────────────────────────────────────────────


class GuideCursorTiming(BaseModel):
    """Per-scenario cursor animation timings for the guide rendering layer."""

    pre_action_ms: int = 200
    action_ms: int = 300
    post_action_ms: int = 150
    travel_speed_px_per_ms: float = 1.5

    class Config:
        extra = "forbid"


class GuideConfig(BaseModel):
    """Top-level `guide:` frontmatter block."""

    enabled: bool = False
    cursor: GuideCursorTiming = Field(default_factory=GuideCursorTiming)

    class Config:
        extra = "forbid"


class GuideCursorOverride(BaseModel):
    """Step-level `guide-cursor` override, only meaningful on `custom` steps."""

    behavior: Literal["click", "type", "hover"]
    target: str
    value: Optional[str] = None

    class Config:
        extra = "forbid"


def expect_checks(expect) -> list[str]:
    """Every assertion string in an ``expect:``, whatever form it took.

    One reader for the scalar and the list forms, so validation and codegen
    cannot disagree about how many assertions a step carries.
    """
    if expect is None:
        return []
    if isinstance(expect, str):
        return [expect]
    return [e if isinstance(e, str) else e.check for e in expect]


class ExpectEntry(BaseModel):
    """One assertion in a multi-assertion ``expect:`` list.

    ``expect:`` was a single scalar with no failure message, and the corpus says
    both halves cost: of one project's 1 594 assert-bearing custom blocks, 481
    hold more than one assertion and 1 247 (78%) carry a message — 1 229 of them
    f-strings that print the value actually observed. A scenario author could
    express neither, so the whole verification step stayed in Python.

    ``message`` maps to Playwright's own ``expect(locator, message=...)``.
    """

    check: str
    message: Optional[str] = None

    class Config:
        extra = "forbid"


# ── Step Model ──────────────────────────────────────────────────────────────


#: Every action a scenario step may declare.
#:
#: Module-level (not rebuilt inside the validator on every step parse) and
#: public, because the interactive player carries its own copy as the
#: ``ManifestAction`` Zod enum in ``packages/player/src/manifest/schema.ts``.
#: That enum sits inside a ``.strict()`` schema, so an action added here but
#: not there makes the player reject the whole manifest at runtime — a green
#: pytest suite, a green Vitest suite, a successful ``steppy build``, and a
#: guide that will not render in the viewer's browser.
#: ``tests/test_action_enum_sync.py`` pins the two together.
#:
#: ``auth0-login`` is deliberately absent: it is a deprecated alias normalized
#: to ``login`` before this set is consulted (FLAWS §6.2).
VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "fill-form",
        "select",
        "wait",
        "wait-for",
        "wait-for-text",
        "scroll",
        "hover",
        "keyboard",
        "assert",
        "custom",
        "login",
        "capture",
        "upload",
        "select-option",
        "confirm",
        "fill-date",
        "toggle",
        "pick-date",
        "pick-time",
        "expand",
        "call",
    }
)


#: Load-bearing fields per action: ``action -> ((attr, description), ...)``.
#:
#: Drives ``Step.validate_required_action_fields``, which raises
#: ``"<action> action requires '<attr>' (<description>)"``. A field is missing
#: when it is ``None`` or blank after ``strip()``.
#:
#: This is the whole required-field story for an action — adding one is a row
#: here, not a new validator. The per-action validators that remain below exist
#: only for rules a table cannot express: mutual exclusion (``select-option``'s
#: option/multiple), format regexes (``pick-time``'s HH:MM), cross-field shapes
#: (``capture``'s from.type), and not-yet-implemented rejections (``fill-date``
#: method=calendar).
#:
#: ``capture`` is deliberately absent: its required field is exposed as ``from``
#: but stored as ``from_``, so the attribute name and the name users must see in
#: the error diverge. It keeps its own checks.
_REQUIRED_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    # Core actions
    "click": (("selector", "the element to click"),),
    "hover": (("selector", "the element to hover"),),
    "scroll": (("selector", "the element to scroll to"),),
    "wait-for": (("selector", "the element to wait for"),),
    "fill": (("selector", "the input"), ("value", "the text to type")),
    "select": (("selector", "the <select>"), ("value", "the option value")),
    "keyboard": (("value", "the key(s) to press"),),
    "wait-for-text": (("value", "the text to wait for"),),
    "wait": (("duration", 'the wait time, e.g. "3s"'),),
    # Component-widget actions
    "upload": (("selector", "the file input"), ("value", "the file path")),
    "select-option": (("selector", "the dropdown trigger"),),
    "toggle": (("selector", "the switch/host"),),
    "expand": (("selector", "the fieldset host"),),
    "fill-date": (
        ("selector", "the date input"),
        ("date", "today|this-<weekday>|next-<weekday>|±Nd/w/m/y|ISO"),
    ),
    "pick-date": (
        ("selector", "the datepicker host"),
        ("date", "today|this-<weekday>|next-<weekday>|±Nd/w/m/y|ISO"),
    ),
    "pick-time": (("selector", "the timepicker host"), ("time", '"HH:MM"')),
    "call": (("fn", "the dotted helper path, e.g. ui_helpers.ensure_open"),),
}


#: Fields every action accepts, whatever it is: identity, the trailing
#: ``expect:``, screenshot/guide metadata, the conditional-execution filters and
#: the two internal tracking slots.
_UNIVERSAL_FIELDS: frozenset[str] = frozenset(
    {
        "tour",
        "tour_text",
        "tour_advance",
        "tour_placement",
        "tour_hold",
        "tour_target_click",
        "action",
        "role",
        "expect",
        "screenshot",
        "screenshot_when",
        "guide_text",
        "guide_cursor",
        "only_in",
        "layouts",
        "wait_after",
        "note",
        "optional",
        "when",
        "step_number",
        "step_title",
        "step_label",
    }
)

#: The INVERSE of ``_REQUIRED_FIELDS``: what each action *accepts* beyond the
#: universal set above.
#:
#: ``Step`` is one flat model with ``extra = "forbid"``, which catches a key no
#: action has ever heard of — but said nothing about a key that belongs to a
#: *different* action. Probed before this table existed: ``retry``, ``interval``,
#: ``format``, ``variant``, ``button``, ``danger``, ``search`` and ``method``
#: each validated cleanly on all 23 actions and generated code on one. So
#: ``- action: click`` + ``- date: today`` + ``- variant: reject`` passed
#: ``steppy validate`` and silently did nothing — the same failure shape as the
#: unknown-``expect:`` hole in ``steppy.assertions``.
#:
#: Adding a field to an action means adding it here, or ``steppy validate``
#: starts rejecting the very scenario the new feature is for.
#: Two absences in the ``force`` rows are deliberate: ``upload`` because
#: Playwright's ``set_input_files`` takes no ``force``, and ``toggle`` because
#: its switch branch already emits ``check(force=True)`` unconditionally — the
#: flag would read as a choice the author does not actually have.
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "navigate": frozenset({"target", "wait", "reload", "timeout"}),
    "click": frozenset({"selector", "within", "match", "synthetic", "timeout", "force"}),
    "login": frozenset(),
    "fill": frozenset({"selector", "value", "clear", "sequential", "blur_key", "timeout", "force", "within", "match"}),
    "fill-form": frozenset({"fields", "interactive_progressive", "timeout"}),
    "select": frozenset({"selector", "value", "timeout", "force", "within", "match"}),
    "select-option": frozenset({"selector", "search", "option", "multiple", "timeout", "force", "within", "match"}),
    "confirm": frozenset({"variant", "button", "danger", "timeout", "force"}),
    "fill-date": frozenset({"selector", "date", "format", "method", "timeout", "within", "match"}),
    "toggle": frozenset({"selector", "state", "option", "timeout", "control", "within", "match"}),
    "expand": frozenset({"selector", "timeout", "control", "detect", "state", "within", "match"}),
    "pick-date": frozenset({"selector", "date", "timeout", "within", "match"}),
    "pick-time": frozenset({"selector", "time", "timeout", "within", "match"}),
    "wait": frozenset({"duration"}),
    "wait-for": frozenset({"selector", "state", "within", "match", "timeout", "until", "interval", "wait"}),
    "wait-for-text": frozenset({"value", "timeout"}),
    "scroll": frozenset({"selector", "timeout", "within", "match"}),
    "hover": frozenset({"selector", "within", "match", "timeout", "force"}),
    "upload": frozenset({"selector", "value", "timeout", "within", "match"}),
    "keyboard": frozenset({"value"}),
    "assert": frozenset({"within", "match", "timeout"}),
    "capture": frozenset({"store", "from_", "retry", "interval", "timeout"}),
    "call": frozenset({"fn", "args", "kwargs", "store", "page", "timeout"}),
    "custom": frozenset({"code"}),
}

#: Attribute name -> the spelling a scenario author actually types, for error
#: messages. Only the fields whose alias differs from the attribute.
_FIELD_ALIASES: dict[str, str] = {
    "from_": "from",
    "blur_key": "blur-key",
    "screenshot_when": "screenshot-when",
    "guide_text": "guide-text",
    "guide_cursor": "guide-cursor",
    "only_in": "only-in",
    "interactive_progressive": "interactive-progressive",
    "tour_text": "tour-text",
    "tour_advance": "tour-advance",
    "tour_placement": "tour-placement",
    "tour_hold": "tour-hold",
    "tour_target_click": "tour-target-click",
}

#: Spellings of a boolean a step-level ``tour:`` must not take (step scalars
#: arrive as strings, frontmatter values as real YAML booleans).
_BOOLISH = frozenset({"true", "false", "yes", "no", "on", "off"})


class Step(BaseModel):
    """A single step in a scenario."""

    # Core fields
    action: str
    role: Optional[str] = None  # Required for most actions except 'wait'

    # Action-specific fields
    target: Optional[str] = None  # For navigate
    selector: Optional[str] = None  # For click, fill, select, etc.
    value: Optional[str] = None  # For fill, select, keyboard, wait-for-text
    fields: Optional[dict[str, str]] = None  # For fill-form
    duration: Optional[str] = None  # For wait (e.g., "3s", "5000ms")
    code: Optional[str] = None  # For custom
    # wait-for: visible|hidden|detached|attached. toggle: on|off (the latter two
    # are accepted here because PyYAML coerces `on`/`off` to bool, normalized by
    # the field_validator below). Per-action subsets are enforced in validators.
    state: Optional[Literal["visible", "hidden", "detached", "attached", "on", "off"]] = None
    # navigate: load-state strategy to await after goto (networkidle default).
    wait: Optional[Literal["networkidle", "load", "domcontentloaded", "none"]] = None
    # navigate: reload the current page (page.reload()) instead of goto — the
    # hard-refetch idiom after async/SQS propagation. 'target' is not required.
    reload: Optional[bool] = None

    # select-option action (component dropdown, e.g. PrimeNG p-select)
    search: Optional[str] = None  # Type into the dropdown filter box before picking
    option: Optional[str] = None  # first | last | N (index) | text:"..."
    multiple: Optional[list[str]] = None  # Multiselect: pick several by label, then Escape

    # Locator disambiguation for click/hover/wait-for/assert. Both default to
    # "off" so unfiltered steps regenerate byte-identically.
    within: Optional[str] = None  # Scope selector: .locator(within).locator(selector)
    match: Optional[str] = None  # first | last | N (index) on the resolved locator

    # confirm action (modal / confirm-dialog button). All optional; default = accept.
    variant: Optional[Literal["accept", "reject"]] = None  # accept (default) | reject
    button: Optional[str] = None  # Match by visible label when class lookup is ambiguous
    danger: Optional[bool] = None  # Prefer the destructive/danger button

    # fill-date / pick-date action (relative/dynamic date).
    date: Optional[str] = None  # today | this-<weekday> | next-<weekday> | ±Nd/w/m/y | ISO
    format: Optional[str] = None  # strftime format; default "%d/%m/%Y"
    method: Optional[Literal["type", "calendar"]] = None  # type (default) | calendar

    # pick-time action (drive an hour/minute clock widget).
    time: Optional[str] = None  # "HH:MM"

    # fill action flags (off by default → byte-identical output).
    clear: Optional[bool] = None  # select-all + delete before typing
    sequential: Optional[bool] = None  # press_sequentially instead of fill (fires keydown/input)
    blur_key: Optional[str] = Field(default=None, alias="blur-key")  # key pressed after typing to commit

    # click action flag: dispatch synthetic MouseEvents via evaluate() for
    # elements that swallow Playwright's native .click() (off → byte-identical).
    synthetic: Optional[bool] = None

    # Per-step Playwright timeout, overriding config.timing.step_timeout (and
    # Scenario.timeout) for this step only. Accepts "25s" / "500ms" / a bare
    # integer read as milliseconds, matching the `wait` action's duration
    # grammar. Absent ⇒ the global default and byte-identical output.
    #
    # A single global cannot serve both a 5s dropdown and a 6-minute report
    # generation, which is why 42% of one project's custom blocks carry a
    # hand-written `timeout=`.
    timeout: Optional[Union[str, int]] = None

    # Where the control actually is, for `toggle` and `expand`.
    #
    # Both actions hard-wire a DESCENDANT hop: toggle builds
    # `locator(selector).locator(config.toggle.switch)`, expand builds
    # `locator(selector).locator(config.fieldset.toggle)`. So a selector aimed
    # at the checkbox (or the disclosure button) itself resolves to nothing, and
    # a page with two widget flavours needs a global config change to reach the
    # second. `control: self` skips the hop; any other value overrides the
    # config selector for this step only. Absent ⇒ today's descendant hop.
    control: Optional[str] = None

    # A state predicate for `wait-for`, beyond DOM presence.
    #
    # Playwright's own wait accepts only visible|hidden|detached|attached, none
    # of which says "the form has finished pre-filling" — the single most
    # frequent reason one project kept a step in custom code, 47 times over, all
    # of them the same hand-rolled `deadline = time.time() + 10` loop.
    #
    # Grammar: value-not-empty | value:<text> | text:<text> | count:<cmp><n>
    until: Optional[str] = None

    # How `expand` decides whether the section is already open.
    #
    # `content` (default) asks whether the content element is visible.
    # `aria` reads `aria-expanded` off the toggle — which is what a disclosure
    # button exposes, and what the projects' own helpers read. The two disagree
    # whenever the content is present but clipped, or animated in.
    detect: Optional[Literal["content", "aria"]] = None

    # Pass force=True to the underlying Playwright call, skipping the
    # actionability (visible / stable / receives-events) checks. The idiom for a
    # component button that never reaches "stable" because its overlay is still
    # animating. Distinct from `synthetic:`, which dispatches untrusted
    # MouseEvents and bypasses real input events entirely.
    force: Optional[bool] = None

    # Capture action: store a value fetched from an external source
    store: Optional[str] = None  # Variable name to store the captured value
    from_: Optional[dict[str, Any]] = Field(default=None, alias="from")  # Fetcher spec
    retry: Optional[int] = None  # Number of attempts (default 1 in template)
    interval: Optional[str] = None  # Retry interval, e.g. "500ms" or "1s"

    # Assertions. A bare string is one check, exactly as before; a list holds
    # several, each optionally carrying its own failure message.
    expect: Optional[Union[str, list[Union[str, ExpectEntry]]]] = None

    # Screenshot and guide
    screenshot: Optional[str | bool] = None  # False, "all", True, or a role name defined in config.apps
    screenshot_when: Literal["before", "after"] = Field(default="after", alias="screenshot-when")
    guide_text: Optional[GuideText] = Field(default=None, alias="guide-text")
    guide_cursor: Optional[GuideCursorOverride] = Field(default=None, alias="guide-cursor")

    # Conditional execution. Both default to "step always runs".
    # only-in: restrict to test or guide mode (default = both).
    # layouts: restrict to specific layouts in guide mode (default = all configured layouts;
    # ignored in test mode since there's no layout dimension there).
    only_in: Optional[Literal["test", "guide"]] = Field(default=None, alias="only-in")
    layouts: Optional[list[str]] = None

    # In-app tour annotations, read only when the scenario opts into tours (all
    # inert otherwise). `tour: skip` leaves a step out of the tour — it still
    # runs in the test and in the tour replay; `tour: keep` forces a step that
    # inference would skip into the tour. `tour: start` / `tour: end` mark the
    # first and last step of the tour's range, whatever the navigation does.
    # `tour` holds one value or a list (`[end, keep]`), always stored as a list.
    # `tour-text` overrides `guide-text` for the tour popover; `tour-advance` /
    # `tour-placement` override what inference picks. The value sets are
    # `steppy.tour_spec`'s, pinned by a test. `tour-hold` names, as one test-id
    # selector, an element the popover must never cover (a hover menu the step's
    # target sits in), so the pointer can stay over it. `tour-target-click`
    # says what a click on an information step's target does (`pass` reaches
    # the element, `next` advances instead, `block` swallows it).
    tour: Optional[list[Literal["skip", "keep", "start", "end"]]] = None
    tour_text: Optional[GuideText] = Field(default=None, alias="tour-text")
    tour_advance: Optional[Literal["click", "input", "change", "visible", "manual"]] = Field(
        default=None, alias="tour-advance"
    )
    tour_placement: Optional[Literal["top", "bottom", "left", "right", "auto"]] = Field(
        default=None, alias="tour-placement"
    )
    tour_hold: Optional[str] = Field(default=None, alias="tour-hold")
    tour_target_click: Optional[Literal["pass", "next", "block"]] = Field(default=None, alias="tour-target-click")

    # Progressive capture for fill-form: snapshot once per field with a
    # single-target cursor on the next-to-fill field, then a settled "all
    # filled" frame with no cursor. Interactive players advance frame-by-frame
    # with a click between each, so the user sees the form fill up step by step.
    # Only valid on action='fill-form'.
    interactive_progressive: bool = Field(default=False, alias="interactive-progressive")

    # `call` action: run a project helper function as a first-class step.
    #
    # The single biggest bucket of residual custom code: 2 398 blocks in one
    # project import a `*_helpers` module, and 926 of them do nothing else. The
    # helpers themselves are genuinely domain-specific — one knows the app's
    # French status labels *and* branches on viewport — so Steppy should not
    # absorb them; it should let a scenario name one.
    #
    # A `call` step is also a NAMED step, which a `custom` block is not: it can
    # carry a `guide-cursor:` and appear in the interactive player and the video
    # instead of publishing as `cursor: null, interactive: false`.
    fn: Optional[str] = None  # dotted path, e.g. "ui_helpers.ensure_open"
    args: Optional[list[Any]] = None  # positional arguments, after the page
    kwargs: Optional[dict[str, Any]] = None  # keyword arguments
    # The page is passed first by default, which is how every helper in the
    # measured corpus is written. `page: false` is the escape hatch for a pure
    # value producer (`sentinel_email("editor")`) that takes no page at all.
    page: Optional[bool] = None

    # Conditional execution against LIVE PAGE STATE, as opposed to `only-in:`
    # and `layouts:`, which gate on the run's *mode*.
    #
    # `optional: true`  — run it, and carry on if it raises. For the panel that
    #                     only appears the first time, or the banner that has
    #                     already cleared.
    # `when: visible(#a)` — run it only if the condition holds right now.
    #
    # Between them these cover the "an optional confirm plus a wait on a banner
    # appearing then clearing" and "a click that falls back between two
    # conditionally-rendered buttons" cases the corpus names by hand.
    optional: Optional[bool] = None
    when: Optional[str] = None

    # Metadata
    wait_after: Optional[int] = None  # milliseconds
    note: Optional[str] = None

    # Internal tracking
    step_number: Optional[int] = None
    step_title: Optional[str] = None
    step_label: Optional[str] = None

    class Config:
        populate_by_name = True
        extra = "forbid"

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v: str) -> str:
        """Validate that action is one of the known types.

        ``auth0-login`` is a kept-forever deprecated alias for ``login``,
        normalized here so downstream (codegen) sees a single name (FLAWS §6.2).
        """
        if v == "auth0-login":
            v = "login"
        if v not in VALID_ACTIONS:
            raise ValueError(f"Unknown action type: {v}. Valid actions: {', '.join(sorted(VALID_ACTIONS))}")
        return v

    @field_validator("tour", mode="before")
    @classmethod
    def normalize_tour_mode(cls, v):
        """Step-level ``tour:`` takes ``skip``, ``keep``, ``start`` or ``end``, or a list of them.

        The frontmatter ``tour: true`` opts a whole scenario in. Written on a step
        it would read as plausible and do nothing, so the boolean spellings are
        rejected with a pointer to where they belong. A single value becomes a
        one-element list, so readers only ever test membership.
        """
        if v is None:
            return v
        values = v if isinstance(v, list) else [v]
        for value in values:
            if isinstance(value, bool) or (isinstance(value, str) and value.strip().lower() in _BOOLISH):
                raise ValueError(
                    "step-level 'tour' takes 'skip', 'keep', 'start' or 'end'; to opt a scenario into "
                    "in-app tours, set `tour: true` in its frontmatter"
                )
        return [value.strip() if isinstance(value, str) else value for value in values]

    @field_validator("tour")
    @classmethod
    def validate_tour_modes(cls, v):
        """``skip``/``keep`` say whether the step shows, ``start``/``end`` where the range is.

        The two axes combine (``[end, keep]``); within one, ``skip`` and
        ``keep`` contradict each other, and a repeated value is a typo.
        """
        if v is None:
            return v
        if not v:
            raise ValueError("step-level 'tour' needs at least one of 'skip', 'keep', 'start', 'end'")
        if len(set(v)) != len(v):
            raise ValueError(f"step-level 'tour' repeats a value: {v}")
        if "skip" in v and "keep" in v:
            raise ValueError("step-level 'tour' cannot both 'skip' and 'keep' a step")
        return v

    @field_validator("tour_text")
    @classmethod
    def validate_tour_text_locales(cls, v):
        """Every ``tour-text`` key is a locale tag, and at least one carries text.

        ``GuideText`` accepts any key as a locale (``extra="allow"``), so without
        this a misspelt inner key would silently become a locale nothing ever
        asks for.
        """
        if v is None:
            return v
        values = {name: getattr(v, name) for name in ("fr", "en", "nl", "de")}
        values.update(v.model_extra or {})
        present = [key for key, value in values.items() if value is not None]
        if not present:
            raise ValueError("'tour-text' needs text for at least one locale")
        for key in present:
            validate_locale_tag(key)
        return v

    @field_validator("layouts")
    @classmethod
    def validate_layouts(cls, v):
        """Reject empty lists and empty layout names. Membership against
        ``config.screenshot.layouts`` is checked post-parse in
        ``parser.validate_scenario_metadata``."""
        if v is None:
            return v
        if not v:
            raise ValueError("'layouts' must be a non-empty list (or omitted)")
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"'layouts' entries must be non-empty strings, got: {item!r}")
        return v

    @field_validator("state", mode="before")
    @classmethod
    def normalize_state(cls, v):
        """``toggle`` uses ``state: on|off`` — but PyYAML coerces the bare words
        ``on``/``off`` (and ``yes``/``no``) to Python booleans. Map booleans back
        to the ``on``/``off`` strings so the Literal accepts them. wait-for states
        are always quoted strings and pass through untouched."""
        if isinstance(v, bool):
            return "on" if v else "off"
        return v

    @model_validator(mode="after")
    def validate_interactive_progressive(self) -> "Step":
        """``interactive-progressive`` only makes sense for fill-form."""
        if self.interactive_progressive and self.action != "fill-form":
            raise ValueError(f"'interactive-progressive' is only valid on fill-form steps (got action='{self.action}')")
        return self

    @model_validator(mode="after")
    def validate_required_action_fields(self) -> "Step":
        """Enforce the load-bearing fields for every action (see _REQUIRED_FIELDS).

        Without this, e.g. a ``click`` with no ``selector`` generates
        ``page.click(None)`` and fails at runtime with a baffling Playwright
        timeout instead of at ``steppy validate`` time.

        Per-action validators below cover only rules a required-field table
        cannot express (mutual exclusion, format regexes, cross-field shapes).
        """
        for field, desc in _REQUIRED_FIELDS.get(self.action, ()):
            v = getattr(self, field)
            if v is None or not str(v).strip():
                raise ValueError(f"{self.action} action requires '{field}' ({desc})")

        # navigate needs a target unless it's a reload
        if self.action == "navigate" and not self.reload:
            if not self.target or not self.target.strip():
                raise ValueError("navigate action requires 'target' (the URL/path) unless reload: true")

        # DOM actions generate `<role>_page` — role is mandatory (wait is exempt)
        if self.action in {
            "click",
            "hover",
            "scroll",
            "wait-for",
            "fill",
            "select",
            "keyboard",
            "wait-for-text",
            "navigate",
        }:
            if not self.role or not self.role.strip():
                raise ValueError(f"{self.action} action requires 'role'")

        return self

    @model_validator(mode="after")
    def validate_capture_fields(self) -> "Step":
        """When action == 'capture', enforce store + from.type shape."""
        if self.action != "capture":
            return self
        if not self.store:
            raise ValueError("capture action requires 'store' (variable name)")
        if not self.from_:
            raise ValueError("capture action requires 'from' (fetcher spec)")
        ftype = self.from_.get("type")
        valid_types = {"redis", "http", "shell", "dom", "date"}
        if ftype not in valid_types:
            raise ValueError(f"capture 'from.type' must be one of {valid_types}, got: {ftype!r}")
        if ftype == "redis" and not self.from_.get("key"):
            raise ValueError("capture from.type=redis requires 'key'")
        if ftype == "http" and not self.from_.get("url"):
            raise ValueError("capture from.type=http requires 'url'")
        if ftype == "shell" and not self.from_.get("command"):
            raise ValueError("capture from.type=shell requires 'command'")
        if ftype == "date":
            # Steppy already owns the date grammar — `_resolve_date` understands
            # today | this-<weekday> | next-<weekday> | ±N[d|w|m|y] | ISO — but it
            # was reachable only from inside `fill-date`/`pick-date`, i.e. only
            # to TYPE a date into a field. The corpus needs the resolved value as
            # a SELECTOR fragment
            # (`[data-testid="agenda-calendar-day-{{target_day}}"]`) and as an ISO
            # week number. Capturing it makes the same grammar interpolatable
            # anywhere, with no new date code and no page involved.
            if not self.from_.get("date"):
                raise ValueError("capture from.type=date requires 'date' (today|next-monday|+7d|YYYY-MM-DD)")
        if ftype == "dom":
            # DOM capture reads from the live page, so it needs a role page and
            # a selector. ``extract`` selects the source (text|attribute|regex);
            # attribute/regex require their companion field.
            if not self.from_.get("selector"):
                raise ValueError("capture from.type=dom requires 'selector'")
            if not self.role:
                raise ValueError("capture from.type=dom requires 'role' (it reads the page)")
            extract = self.from_.get("extract", "text")
            if extract not in ("text", "attribute", "regex"):
                raise ValueError(f"capture from.type=dom 'extract' must be text|attribute|regex, got: {extract!r}")
            if extract == "attribute" and not self.from_.get("attribute"):
                raise ValueError("capture from.type=dom extract=attribute requires 'attribute'")
            if extract == "regex" and not self.from_.get("regex"):
                raise ValueError("capture from.type=dom extract=regex requires 'regex'")
        return self

    @model_validator(mode="after")
    def validate_select_option_fields(self) -> "Step":
        """select-option takes exactly one of 'option' (single pick) or
        'multiple' (multiselect) — never both, never neither.

        The required 'selector' is table-driven (_REQUIRED_FIELDS).
        """
        if self.action != "select-option":
            return self
        has_option = self.option is not None and str(self.option).strip() != ""
        has_multiple = self.multiple is not None
        if has_option and has_multiple:
            raise ValueError("select-option accepts either 'option' or 'multiple', not both")
        if not has_option and not has_multiple:
            raise ValueError("select-option requires 'option' (first|last|N|text:\"...\") or 'multiple' (list)")
        return self

    @model_validator(mode="after")
    def validate_match_within(self) -> "Step":
        """'match'/'within' disambiguate a selector, so they apply to every
        action that resolves one. 'match' accepts first|last|<integer index>.

        The original four (click, hover, wait-for, assert) left every
        widget-driving action with no way to name the n-th widget on a page.
        Playwright's own ``>>`` chaining is a workaround — one project uses it
        159 times — but it hides the disambiguation inside an opaque selector
        string, which means guide mode cannot resolve a bbox for it
        (``_safe_bbox_locator`` needs a real locator, not a selector with an
        engine chain in it). Widening the set is what makes the resolved locator
        available to both codegen and the cursor.

        ``confirm`` stays out: it has no ``selector`` at all, resolving its
        button imperatively from the dialog overlay."""
        if self.match is None and self.within is None:
            return self
        supported = {
            "click",
            "hover",
            "wait-for",
            "assert",
            "fill",
            "select",
            "select-option",
            "toggle",
            "expand",
            "upload",
            "scroll",
            "fill-date",
            "pick-date",
            "pick-time",
        }
        if self.action not in supported:
            raise ValueError(f"'match'/'within' are only valid on {sorted(supported)} (got action='{self.action}')")
        if self.match is not None:
            m = str(self.match).strip().lower()
            if m not in ("first", "last") and not m.isdigit():
                raise ValueError(f"'match' must be first|last|<index>, got: {self.match!r}")
        return self

    @model_validator(mode="after")
    def validate_confirm_fields(self) -> "Step":
        """confirm drives a confirmation-dialog button. All fields optional
        (default = accept the active dialog); 'button' and 'variant'/'danger'
        are alternative ways to pick the target button."""
        if self.action != "confirm":
            return self
        if self.button and (self.variant or self.danger):
            raise ValueError("confirm 'button' (label match) cannot combine with 'variant'/'danger'")
        return self

    @model_validator(mode="after")
    def validate_fill_date_fields(self) -> "Step":
        """fill-date's 'calendar' method is reserved for the pick-date action.

        The required 'selector'/'date' are table-driven (_REQUIRED_FIELDS).
        """
        if self.action != "fill-date":
            return self
        if self.method == "calendar":
            raise ValueError(
                "fill-date method='calendar' is not yet implemented; use the "
                "'type' method (default), or the 'pick-date' action"
            )
        return self

    @model_validator(mode="after")
    def validate_wait_for_state(self) -> "Step":
        """``wait-for`` only accepts the DOM-presence states. The ``state``
        Literal is broadened to include ``on``/``off`` for ``toggle``, so guard
        that those don't leak onto wait-for."""
        if self.action == "wait-for" and self.state in ("on", "off"):
            raise ValueError(f"wait-for 'state' must be visible|hidden|detached|attached, got: {self.state!r}")
        return self

    @model_validator(mode="after")
    def validate_navigate_fields(self) -> "Step":
        """``wait:`` (load-state strategy) applies to ``navigate`` and
        ``wait-for``; ``reload:`` (hard refetch) to ``navigate`` alone.

        ``wait-for`` was widened rather than given a ``load-state:`` field of its
        own: ``wait:`` already carries exactly this vocabulary, and one concept
        with two spellings is worse than a slightly broader field. It covers the
        "settle, then check" idiom that 80 custom blocks spell as a bare
        ``wait_for_load_state`` beside their real wait.

        ``reload`` reloads the current page, so it cannot combine with
        ``target``."""
        if self.wait is not None and self.action not in ("navigate", "wait-for"):
            raise ValueError(
                f"'wait' (load-state strategy) is only valid on navigate and wait-for (got action='{self.action}')"
            )
        if self.reload is not None and self.action != "navigate":
            raise ValueError(f"'reload' is only valid on navigate (got action='{self.action}')")
        if self.reload and self.target:
            raise ValueError("navigate 'reload: true' reloads the current page; remove 'target'")
        return self

    @model_validator(mode="after")
    def validate_fill_flags(self) -> "Step":
        """``clear``/``sequential``/``blur-key`` only apply to ``fill``."""
        if self.action == "fill":
            return self
        flags = []
        if self.clear is not None:
            flags.append("clear")
        if self.sequential is not None:
            flags.append("sequential")
        if self.blur_key is not None:
            flags.append("blur-key")
        if flags:
            raise ValueError(
                f"{flags} {'is' if len(flags) == 1 else 'are'} only valid on fill (got action='{self.action}')"
            )
        return self

    @model_validator(mode="after")
    def validate_synthetic_field(self) -> "Step":
        """``synthetic: true`` (dispatch manual MouseEvents for elements that
        swallow Playwright's native .click()) only applies to ``click``."""
        if self.synthetic is not None and self.action != "click":
            raise ValueError(f"'synthetic' is only valid on click (got action='{self.action}')")
        return self

    @model_validator(mode="after")
    def validate_toggle_fields(self) -> "Step":
        """toggle drives a switch (``state: on|off``, omit = flip) or a segmented
        button group (``option: <label>``); the two are mutually exclusive.

        The required 'selector' is table-driven (_REQUIRED_FIELDS).
        """
        if self.action != "toggle":
            return self
        has_state = self.state is not None
        has_option = self.option is not None and str(self.option).strip() != ""
        if has_state and has_option:
            raise ValueError("toggle accepts either 'state' (on|off, switch) or 'option' (label, segmented), not both")
        if has_state and self.state not in ("on", "off"):
            raise ValueError(f"toggle 'state' must be on|off (or omitted to flip), got: {self.state!r}")
        return self

    @model_validator(mode="after")
    def validate_when_condition(self) -> "Step":
        """``when:`` takes a *condition*, a deliberately smaller vocabulary than
        ``expect:``.

        The assertion forms auto-wait, which is wrong for a condition: "click it
        if it is there" has to answer from what is rendered now, or every
        guarded step pays the full timeout waiting for something that was never
        coming. Only the four forms that can be answered immediately are
        allowed.
        """
        if self.when is None:
            return self
        spec = str(self.when).strip()
        head = spec.partition("(")[0]
        allowed = ("visible", "hidden", "count", "contains")
        if head not in allowed or not spec.endswith(")"):
            raise ValueError(
                f"unknown 'when' condition {spec!r}. "
                'Valid: visible(sel) | hidden(sel) | count(sel, >=N) | contains(sel, "text")'
            )
        if not self.role:
            raise ValueError("'when' reads the page, so the step needs a 'role'")
        return self

    @model_validator(mode="after")
    def validate_call_fields(self) -> "Step":
        """``fn`` must be ``module.function`` — importable, and not a bare name.

        A bare name would have to be resolved against the test module's globals,
        which is the ``custom``-block behaviour this action exists to replace.
        Requiring the module makes the dependency visible to a reader and to
        ``steppy validate``.
        """
        if self.action != "call":
            return self
        fn = str(self.fn or "").strip()
        if not re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+", fn):
            raise ValueError(f"call 'fn' must be a dotted path like 'ui_helpers.ensure_open', got: {self.fn!r}")
        return self

    @model_validator(mode="after")
    def validate_until_predicate(self) -> "Step":
        """Reject an ``until:`` the runtime helper would not understand.

        ``_wait_until`` in the generated conftest raises on an unknown
        predicate, but only once the test is running — and only if that step is
        reached. Checking the grammar here fails at ``steppy validate`` instead.
        """
        if self.until is None:
            return self
        spec = str(self.until).strip()
        if spec == "value-not-empty":
            return self
        for prefix in ("value:", "text:"):
            if spec.startswith(prefix) and spec[len(prefix) :]:
                return self
        if spec.startswith("count:"):
            if re.match(r"^(>=|<=|==|!=|>|<)\s*\d+$", spec[6:].strip()):
                return self
            raise ValueError(f"'until' count predicate must be a comparator and an integer, got: {spec[6:]!r}")
        raise ValueError(
            f"unknown 'until' predicate {spec!r}. "
            "Valid: value-not-empty | value:<text> | text:<text> | count:<comparator><n>"
        )

    @model_validator(mode="after")
    def validate_expand_fields(self) -> "Step":
        """``expand``'s ``state:`` reuses ``toggle``'s ``on``/``off`` spelling.

        Not ``open``/``closed``: ``toggle`` already established ``on``/``off``
        for exactly this idempotent-set semantics, and one concept should not
        have two spellings. Omitting ``state`` keeps today's open-only
        behaviour, so existing scenarios regenerate byte-identically.
        """
        if self.action != "expand":
            return self
        if self.state is not None and self.state not in ("on", "off"):
            raise ValueError(f"expand 'state' must be on|off (or omitted to open), got: {self.state!r}")
        return self

    @model_validator(mode="after")
    def validate_pick_time_fields(self) -> "Step":
        """pick-time's 'time' must be 24h ``HH:MM``.

        The required 'selector'/'time' are table-driven (_REQUIRED_FIELDS);
        ``pick-date`` and ``expand`` need nothing beyond the table at all.
        """
        if self.action != "pick-time":
            return self
        if not re.match(r"^\d{1,2}:\d{2}$", str(self.time).strip()):
            raise ValueError(f"pick-time 'time' must be \"HH:MM\", got: {self.time!r}")
        return self

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v):
        """Reject a timeout the generator could not turn into milliseconds.

        Validated here rather than at codegen so a typo fails at
        ``steppy validate``, and parsed by the same helper codegen uses so the
        two cannot disagree.
        """
        if v is None:
            return v
        try:
            parse_ms(v)
        except DurationSyntaxError as exc:
            raise ValueError(f"'timeout': {exc}") from exc
        return v

    @model_validator(mode="after")
    def validate_allowed_fields(self) -> "Step":
        """Reject a field that belongs to a *different* action.

        ``extra = "forbid"`` catches a key no action has, never a key some other
        action owns. See ``_ALLOWED_FIELDS`` for the measurement that motivated
        this. Only explicitly-set keys are checked (``model_fields_set``), so a
        default never trips it and existing scenarios are unaffected.
        """
        allowed = _ALLOWED_FIELDS.get(self.action)
        if allowed is None:  # pragma: no cover - validate_action ran first
            return self
        # Explicitly-passed ``None`` is not "set" for this purpose: it generates
        # nothing either way, and the parser only ever hands us keys the author
        # actually typed. Being strict about it would only punish programmatic
        # construction (tests, codemods) for spelling out the full signature.
        stray = sorted(
            f for f in self.model_fields_set - allowed - _UNIVERSAL_FIELDS if getattr(self, f, None) is not None
        )
        if stray:
            names = ", ".join(repr(_FIELD_ALIASES.get(f, f)) for f in stray)
            plural = "s" if len(stray) > 1 else ""
            raise ValueError(
                f"{self.action} action does not accept field{plural} {names}. "
                f"Accepted here: {', '.join(sorted(_FIELD_ALIASES.get(f, f) for f in allowed)) or '(none)'}"
            )
        return self

    @model_validator(mode="after")
    def validate_expect_grammar(self) -> "Step":
        """Reject an ``expect:`` the assertion grammar does not recognise.

        Without this, an unknown form reached ``assert_body``'s ``{% else %}``
        and generated ``pass`` — a green test asserting nothing, with
        ``steppy validate`` silent. The grammar itself lives in
        ``steppy.assertions`` so codegen and validation cannot disagree.
        """
        if self.expect is None:
            return self
        for check in expect_checks(self.expect):
            try:
                parse_assertion(check)
            except AssertionSyntaxError as exc:
                raise ValueError(str(exc)) from exc
        return self

    @field_validator("screenshot")
    @classmethod
    def validate_screenshot(cls, v: str | bool | None) -> str | bool | None:
        """Normalize screenshot field. Role membership is checked post-parse
        against config.apps (see parser.validate_scenario_roles)."""
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v.lower() == "false":
                return False
            if v.lower() == "true":
                return True
            if not v.strip():
                raise ValueError("screenshot role must be a non-empty string")
        return v


# ── Seed Config ─────────────────────────────────────────────────────────────


class SeedConfig(BaseModel):
    """Seed script configuration."""

    script: str
    run: Literal["before", "after", "both"] = "before"
    timeout: int = Field(default=120, ge=1)  # seconds; kill a hung seed script

    class Config:
        extra = "forbid"


# ── Scenario Model ──────────────────────────────────────────────────────────


class ScenarioTour(BaseModel):
    """Frontmatter ``tour:`` block: opts the scenario into the in-app tours output.

    ``tour: true`` / ``tour: false`` are shorthands for ``{enabled: ...}``; a
    block without ``enabled`` opts in. Everything else is optional:

    * ``id`` — the tour id apps call ``start()`` with (default: the file slug);
    * ``title`` — a string, or one per locale (default: the scenario title);
    * ``entry`` — the route pattern the tour starts on, e.g.
      ``/invoices/:invoiceId`` (default: inferred from the scenario's first
      navigation after login). Without a step marked ``tour: start``, the tour
      starts after the navigate that matches it; with one, it is only the route;
    * ``role`` — whose app the tour runs in, required when steps use several roles;
    * ``guide_slug`` — the guide page ``Open the full guide`` links to (default:
      the file slug, when the scenario is ``guide.enabled``).

    Route grammar beyond the cheap shape checks below is applied by the tours
    compiler, which also knows the app URL the entry is relative to.
    """

    enabled: bool = True
    id: Optional[str] = None
    title: Optional[Union[str, dict[str, str]]] = None
    entry: Optional[str] = None
    role: Optional[str] = None
    guide_slug: Optional[str] = None

    class Config:
        extra = "forbid"

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if v is not None and not is_tour_id(v):
            raise ValueError(
                f"tour id {v!r} must be 1-64 characters [A-Za-z0-9_-], start with a letter or digit, "
                "and not be a Windows device name"
            )
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("tour title must not be empty")
            return v
        if not v:
            raise ValueError("tour title map must not be empty")
        for locale, text in v.items():
            validate_locale_tag(locale)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"tour title for {locale!r} must be a non-empty string")
        return v

    @field_validator("entry")
    @classmethod
    def validate_entry(cls, v):
        if v is None:
            return v
        v = v.strip()
        bad = v.startswith("//") or v.startswith("/\\") or any(c in v for c in "#? \t\n\\")
        if not v.startswith("/") or bad:
            raise ValueError(
                f"tour entry {v!r} must be a same-origin path pattern such as /invoices/:invoiceId "
                "(no scheme, host, query or fragment)"
            )
        return v

    @field_validator("guide_slug")
    @classmethod
    def validate_guide_slug(cls, v):
        if v is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v):
            raise ValueError(f"guide_slug {v!r} must be 1-128 characters [A-Za-z0-9_-]")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v is not None and not v.strip():
            raise ValueError("tour role must not be empty")
        return v


class Scenario(BaseModel):
    """A scenario with metadata and steps."""

    # Frontmatter metadata
    steppy: int = 1  # Scenario-format version (FLAWS §6.4); absent ⇒ 1.
    title: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    seed: Optional[SeedConfig] = None
    priority: Optional[Literal["critical", "high", "medium", "low"]] = None
    auth: bool = True  # Set to false for scenarios that test the login flow itself
    timeout: Optional[int] = None  # milliseconds; the default timeout of each step, not a whole-test limit

    # Layouts this scenario declares it must pass in (scenario-level). When set,
    # the whole flow parametrizes over these viewports — authoritative on a plain
    # ``steppy run``, filtered by ``--layouts``/``--guide``. Absent ⇒ no
    # scenario-level layout dimension (byte-identical default behaviour).
    layouts: Optional[list[str]] = None

    # Exclusion group. Two scenarios naming the same group never run at the same
    # time under ``steppy run --workers N``; different groups still run
    # concurrently. Absent ⇒ no constraint (byte-identical default behaviour), and
    # inert on the single-worker path, which runs every file sequentially in one
    # pytest process anyway. Promises non-overlap only — not ordering, and not
    # indivisibility (a retry pass legitimately re-runs a subset of a group).
    exclusive: Optional[str] = None

    # Guide rendering configuration (cursor timings, etc.)
    guide: GuideConfig = Field(default_factory=GuideConfig)

    # In-app tour opt-in (``tour: true`` or a ``tour:`` block). Absent ⇒ the
    # project default ``tours.default``; inert unless ``tours.enabled``.
    tour: Optional[ScenarioTour] = None

    # Steps parsed from the body
    steps: list[Step] = Field(default_factory=list)

    # File metadata (set by parser)
    file_path: Optional[Path] = None
    slug: Optional[str] = None  # e.g., "cross--order-lifecycle"

    class Config:
        extra = "forbid"

    @field_validator("steppy")
    @classmethod
    def validate_version(cls, v: int) -> int:
        """Reject scenario-format versions this Steppy can't handle (FLAWS §6.4).

        A too-new file surfaces as a clear, actionable error at parse time (for
        `validate`, `generate`, and `run` alike) instead of a baffling
        `extra="forbid"`/typo-shaped failure downstream.
        """
        if v < 1:
            raise ValueError(f"steppy version must be >= 1 (got {v})")
        if v > SUPPORTED_SCENARIO_VERSION:
            raise ValueError(
                f"scenario declares steppy: {v} but this Steppy supports up to "
                f"version {SUPPORTED_SCENARIO_VERSION}; upgrade Steppy to run it"
            )
        return v

    @field_validator("layouts")
    @classmethod
    def validate_layouts(cls, v):
        """Reject empty lists and empty layout names. Membership against
        ``config.screenshot.layouts`` is checked post-parse in
        ``parser.validate_scenario_layouts``. Mirrors ``Step.validate_layouts``."""
        if v is None:
            return v
        if not v:
            raise ValueError("'layouts' must be a non-empty list (or omitted)")
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"'layouts' entries must be non-empty strings, got: {item!r}")
        return v

    @field_validator("exclusive", mode="before")
    @classmethod
    def normalize_exclusive(cls, v):
        """Validate the exclusion group name (``steppy.exclusive`` owns the rule).

        ``mode="before"`` so a list value is rejected with the closure-rule message
        rather than by pydantic's generic str coercion error — the advice an author
        reaches for when they need two resources is unsound, so the message has to
        say what is actually safe.
        """
        if v is None:
            return None
        return normalize_group_name(v)

    @field_validator("tour", mode="before")
    @classmethod
    def normalize_tour(cls, v):
        """Accept ``tour: true|false`` and the ``tour: {...}`` block (the ``seed`` pattern)."""
        if v is None or isinstance(v, (ScenarioTour, dict)):
            return v
        if isinstance(v, bool):
            return {"enabled": v}
        raise ValueError("'tour' must be true, false, or a block {enabled, id, title, entry, role, guide_slug}")

    def tour_opted_in(self, project_default: bool) -> bool:
        """Whether this scenario produces an in-app tour, given ``tours.default``."""
        if self.tour is None:
            return project_default
        return self.tour.enabled

    @field_validator("seed", mode="before")
    @classmethod
    def normalize_seed(cls, v):
        """Accept both string ('base') and dict ({script: base, run: both}) forms."""
        if v is None:
            return None
        if isinstance(v, str):
            return SeedConfig(script=v, run="before")
        if isinstance(v, dict):
            return SeedConfig(**v)
        return v

    def get_roles(self) -> set[str]:
        """Get all unique roles used in this scenario."""
        roles = set()
        for step in self.steps:
            if step.role:
                roles.add(step.role)
        return roles

    def has_tag(self, tag: str) -> bool:
        """Check if scenario has a specific tag."""
        return tag in self.tags

    def has_all_tags(self, tags: list[str]) -> bool:
        """Check if scenario has ALL specified tags."""
        return all(tag in self.tags for tag in tags)


# ── Config Models ───────────────────────────────────────────────────────────


class _PresetModel(BaseModel):
    """Base for selector-config blocks that support a named ``preset:`` (FLAWS
    §4.3, §6.2).

    A block may name a ``preset:`` to adopt a whole kit's selectors at once. The
    after-validator backfills every selector field the user did NOT set
    explicitly from ``_preset_registry[preset][_preset_key]``. Explicitly set
    fields win; omitting ``preset`` leaves the class defaults untouched — so
    existing configs are byte-identical. The UI-kit blocks read ``PRESETS``; the
    auth form block overrides ``_preset_registry`` with ``IDP_PRESETS``."""

    # Which key in the registry holds this model's selectors. Set by each subclass.
    _preset_key: ClassVar[str] = ""
    # Which preset registry to read. Defaults to the UI-kit registry; the auth
    # form block overrides it with the IdP registry (FLAWS §6.2). Kept a ClassVar
    # so it isn't treated as a pydantic field.
    _preset_registry: ClassVar[dict] = PRESETS

    preset: Optional[str] = None

    class Config:
        extra = "forbid"

    @model_validator(mode="after")
    def _apply_preset(self):
        if self.preset is None:
            return self
        try:
            kit = self._preset_registry[self.preset]
        except KeyError:
            raise ValueError(f"Unknown preset {self.preset!r}; available: {sorted(self._preset_registry)}")
        selectors = kit.get(self._preset_key, {})
        for name, value in selectors.items():
            # Only fill fields the user left at their default; explicit wins.
            if name not in self.model_fields_set:
                setattr(self, name, value)
        return self


class FormAuthConfig(_PresetModel):
    """Selectors for the generic hosted-login form-fill (auth ``mode: form``,
    formerly ``mode: auth0``).

    Works against any IdP with a fillable login form (Auth0, Keycloak, …) by
    overriding the three selectors, or by naming an IdP ``preset:`` (``auth0`` |
    ``keycloak``) that fills them at once (FLAWS §6.2). Defaults mirror the
    historical Auth0 selectors so existing configs are unchanged."""

    _preset_key: ClassVar[str] = "form"
    _preset_registry: ClassVar[dict] = IDP_PRESETS

    username_selector: str = "input[name='username']"
    password_selector: str = "input[name='password']"
    submit_selector: str = "button[data-action-button-primary='true']"
    # Glob the page reaches once login is done (e.g. ``**/home``). Unset, the
    # login waits for ``<app url>/**`` — which a provider served on the app's
    # own origin (Keycloak behind the same proxy) already matches.
    post_login_url_pattern: Optional[str] = None
    # Where automatic login starts (default ``/``), and an element on the app's
    # own login page to click through to the provider (a "Log in" button).
    login_path: Optional[str] = None
    start_selector: Optional[str] = None
    consent_selector: Optional[str] = None
    # TOTP/MFA (FLAWS §6.2). When ``totp_selector`` is set and the app's
    # credentials carry a ``totp_secret``, the login flow fills a time-based
    # one-time code after the password submit. ``totp_submit_selector`` defaults
    # to ``submit_selector`` when omitted. Both may be filled by an IdP preset.
    totp_selector: Optional[str] = None
    totp_submit_selector: Optional[str] = None


# Back-compat alias: the mode/selectors were historically named ``auth0``.
Auth0Config = FormAuthConfig


class AuthCredentials(BaseModel):
    """Authentication credentials."""

    username: str
    password: str
    # Optional TOTP/MFA shared secret (base32) for automated multi-factor login
    # (FLAWS §6.2). Best supplied as an env ref (``${STEPPY_<ROLE>_TOTP}``) so it
    # is never committed in plaintext. Only used when ``auth.form.totp_selector``
    # is also set.
    totp_secret: Optional[str] = None


class BypassConfig(BaseModel):
    """Bypass authentication configuration."""

    header: Optional[dict[str, str]] = None
    cookie: Optional[dict[str, Any]] = None
    local_storage: Optional[dict[str, str]] = None


class TokenConfig(BaseModel):
    """Token-based authentication configuration."""

    endpoint: str
    method: str = "POST"
    body: Optional[dict[str, Any]] = None
    inject_as: Literal["localStorage", "cookie", "header"]
    key: str


class AppAuthConfig(BaseModel):
    """Authentication configuration for a single app."""

    # ``form`` is the generic hosted-login form-fill (any IdP). ``auth0`` is a
    # kept-forever deprecated alias, normalized to ``form`` below (FLAWS §6.2).
    mode: Literal["form", "auth0", "storage-state", "bypass", "token"]
    credentials: Optional[AuthCredentials] = None
    storage_state_path: Optional[str] = None
    bypass: Optional[BypassConfig] = None
    token: Optional[TokenConfig] = None

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v):
        """Map the legacy ``auth0`` mode name onto ``form``."""
        return "form" if v == "auth0" else v


class LocalStorageI18nConfig(BaseModel):
    """Per-locale localStorage values used to switch the app's UI language.

    The same `key` is written for every locale; only the value changes.
    """

    key: str
    values: dict[str, str]

    class Config:
        extra = "forbid"

    @field_validator("values")
    @classmethod
    def validate_values_non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("i18n.localstorage.values must declare at least one locale")
        return v


class AppI18nConfig(BaseModel):
    """Per-app i18n hooks. Currently exposes a localStorage strategy."""

    localstorage: LocalStorageI18nConfig

    class Config:
        extra = "forbid"


class AppConfig(BaseModel):
    """Configuration for a single app/role."""

    name: str
    url: str
    # In-network URL used by parallelism workers running inside Docker. When
    # the coordinator dispatches workers (`steppy run --workers >= 2`), it
    # passes a {role: internal_url} JSON map via STEPPY_BASE_URL_OVERRIDE so the
    # generated conftest can target the app via Docker DNS instead of the
    # host-facing `url`. Optional in single-worker mode; required at
    # coordinator startup for any role used by selected scenarios.
    internal_url: Optional[str] = None
    auth: AppAuthConfig
    i18n: Optional[AppI18nConfig] = None
    # Playwright BrowserContext locale (BCP-47, e.g. "fr-FR"). When set, every
    # generated test runs against a context with this locale → navigator.language,
    # Accept-Language, and Intl.* defaults are pinned. Makes date / number
    # formatting deterministic across single-worker (host OS locale) and parallel
    # (Linux container locale) runs. Optional; when None, Playwright uses the
    # browser default. ``--guide`` runs that pass ``STEPPY_LOCALES`` still override
    # this per parametrization so internationalized captures keep working.
    locale: Optional[str] = None
    # Playwright BrowserContext timezone_id (IANA tz, e.g. "Europe/Brussels").
    # Pins the browser's reported timezone, making time-of-day-sensitive UI
    # deterministic across machines / containers regardless of host TZ.
    timezone: Optional[str] = None


class GlobalAuthConfig(BaseModel):
    """Global authentication settings."""

    storage_state_dir: str = "./.auth"
    storage_state_ttl: int = 3600  # seconds
    # Login-form selectors for auth ``mode: form``. The legacy key ``auth0`` is
    # accepted and copied onto ``form`` by the validator below (FLAWS §6.2).
    form: Optional[FormAuthConfig] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_auth0_key(cls, data):
        """Copy a legacy ``auth0:`` block onto ``form`` when ``form`` is absent."""
        if isinstance(data, dict) and "auth0" in data and data.get("form") is None:
            data = dict(data)
            data["form"] = data.pop("auth0")
        return data


class ViewportConfig(BaseModel):
    """Viewport configuration for screenshots."""

    width: int = 1280
    height: int = 720


class LayoutConfig(BaseModel):
    """A named viewport for guide capture. When ``screenshot.layouts`` is
    populated, ``steppy run --guide`` parametrizes captures over each layout
    and screenshots land under ``output/screenshots/<scenario>/[<locale>/]<layout>/``."""

    width: int
    height: int
    # Capture-time devicePixelRatio for this layout. >1 yields a denser PNG
    # (e.g. 3 on a narrow mobile viewport) so the screenshot stays crisp when
    # the guide displays it at the content-column width. Default 1 = Playwright's
    # default → byte-identical output for projects that don't set it. This does
    # NOT set is_mobile/has_touch, so the captured page layout is unchanged —
    # only the pixel density increases.
    device_scale_factor: int = Field(default=1, ge=1)

    class Config:
        extra = "forbid"


class ScreenshotConfig(BaseModel):
    """Screenshot settings."""

    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    format: Literal["png", "jpeg"] = "png"
    full_page: bool = False
    highlight: bool = True
    highlight_color: str = "rgba(255, 0, 0, 0.3)"
    highlight_border: str = "2px solid red"
    # Default capture-time devicePixelRatio for the no-layout viewport, and the
    # fallback for any layout that omits its own ``device_scale_factor``. Default
    # 1 keeps existing output byte-identical. See ``LayoutConfig`` above.
    device_scale_factor: int = Field(default=1, ge=1)
    # Optional named layouts for per-layout guide capture. Empty = single
    # viewport (the existing behavior). Populated = ``--guide`` parametrizes
    # over these layouts in addition to locales.
    layouts: dict[str, LayoutConfig] = Field(default_factory=dict)
    # Redaction: CSS selectors whose matched elements are covered with a solid
    # box in every captured screenshot (Playwright's native ``mask=``). Use it
    # to keep secrets and personal data (passwords, customer records) out of published guides.
    # Empty (default) = no masking, byte-identical output. ``mask_color`` is any
    # CSS color string for the overlay boxes.
    mask: list[str] = Field(default_factory=list)
    mask_color: str = "#000000"


class TimingConfig(BaseModel):
    """Timing defaults."""

    navigation_wait: int = 2000  # milliseconds
    action_wait: int = 500
    sqs_wait: int = 5000
    step_timeout: int = 30000
    # Default load-state strategy awaited by the ``navigate`` action after goto.
    # Per-step ``wait:`` overrides this. "none" skips the wait entirely (useful
    # for apps with long-lived SSE streams where networkidle never settles).
    navigate_wait: Literal["networkidle", "load", "domcontentloaded", "none"] = "networkidle"


class OutputConfig(BaseModel):
    """Where run artifacts are written and read back (FLAWS §4.6).

    Both live keys are resolved against the project root, so a relative value
    lands under it and an absolute one wins outright. ``screenshots_dir`` is
    read by the *writer* (the generated conftest, at runtime — editing it takes
    effect without re-running ``steppy generate``) and by every reader:
    ``steppy guide``'s ``--screenshots-dir`` default, ``steppy build``'s
    fallback when the TypeScript config omits ``screenshotsDir``, the parallel
    coordinator's failure harvest, and ``steppy bundle create``.
    ``guides_dir`` is ``steppy guide``'s ``--output-dir`` default and the
    bundle's guides root. ``tours_dir`` holds the canonical in-app tour tree
    (``<tours_dir>/<build-id>/``) that ``steppy generate`` / ``steppy tours``
    write and the static ``steppy build`` republishes.

    A value outside ``output/`` is allowed and honoured, but note what only
    archives ``output/``: results bundles, the shipped CI artifact upload, the
    scaffolded ``.gitignore``, and the ``--workers N`` bind mount (where a
    capture written elsewhere cannot leave the container at all). Each of those
    warns rather than failing silently.

    ``reports_dir`` is **deprecated and ignored**. Nothing ever wrote
    ``output/reports``: Playwright traces and videos are top-level
    ``output/traces`` / ``output/videos``, and ``report.junit_xml`` /
    ``report.html`` are explicit paths of their own. It is still accepted so an
    existing ``config.yaml`` keeps loading; ``steppy validate`` warns and
    ``steppy update --check`` reports it.
    """

    screenshots_dir: str = "./output/screenshots"
    guides_dir: str = "./output/guides"
    tours_dir: str = "./output/tours"
    reports_dir: Optional[str] = None  # deprecated, inert — see the docstring

    class Config:
        extra = "forbid"


class DropdownConfig(_PresetModel):
    """Selectors for the component dropdown driven by the ``select-option``
    action. Defaults target PrimeNG ``p-select``; set ``preset:`` (e.g.
    ``material``) or override individual selectors in ``config.yaml`` for other
    UI kits. ``select-option`` works out-of-the-box for PrimeNG apps with no
    config."""

    _preset_key: ClassVar[str] = "dropdown"
    trigger: str = "p-select"
    overlay: str = ".p-select-overlay"
    option: str = ".p-select-option, .p-select-list-container li"
    filter: str = ".p-select-filter-container input, .p-select-filter"
    # How to wait after typing into the filter box before picking an option.
    # 'api'  -> wait_for_load_state('networkidle') (option list fetched remotely)
    # 'none' -> no extra wait (purely client-side filtering)
    settle: Literal["api", "none"] = "api"

    class Config:
        extra = "forbid"


class DialogConfig(_PresetModel):
    """Selectors for the confirmation dialog driven by the ``confirm`` action.
    Defaults target PrimeNG ``p-confirmDialog`` (whose accept/reject buttons
    have no ``data-testid``); set ``preset:`` or override this block in
    ``config.yaml`` for other UI kits. ``confirm`` works out-of-the-box for
    PrimeNG apps with no config."""

    _preset_key: ClassVar[str] = "dialog"
    overlay: str = ".p-confirmdialog, p-confirmdialog"
    accept: str = "button.p-confirmdialog-accept-button, button.p-confirm-dialog-accept"
    reject: str = "button.p-confirmdialog-reject-button, button.p-confirm-dialog-reject"
    danger: str = "button.p-button-danger, button.p-confirmdialog-accept-button"

    class Config:
        extra = "forbid"


class ToggleConfig(_PresetModel):
    """Selectors for the ``toggle`` action. Defaults target PrimeNG: a
    ``ToggleSwitch`` hides a real ``input[role="switch"]`` behind styling, and
    segmented groups are ``p-togglebutton`` items. Set ``preset:`` or override
    for other UI kits."""

    _preset_key: ClassVar[str] = "toggle"
    switch: str = 'input[role="switch"]'
    segment: str = ".p-togglebutton"

    class Config:
        extra = "forbid"


class DatepickerConfig(_PresetModel):
    """Selectors for the ``pick-date`` / ``pick-time`` actions' calendar and
    clock widgets. Defaults target PrimeNG ``p-datepicker``; set ``preset:`` or
    override for other UI kits. Works out-of-the-box for PrimeNG apps with no
    config."""

    _preset_key: ClassVar[str] = "datepicker"
    panel: str = ".p-datepicker-panel"
    next_button: str = ".p-datepicker-next-button"
    prev_button: str = ".p-datepicker-prev-button"
    day_cell: str = ".p-datepicker-day-cell:not(.p-datepicker-other-month)"
    # Optional open trigger for showIcon pickers (calendar/clock button). The
    # helper clicks it when present, else the inner <input>, else the host — so
    # both icon and no-icon pickers open with no extra config.
    trigger: str = "button.p-datepicker-dropdown, button.p-datepicker-trigger"
    # Time picker: PrimeNG renders two columns, each an up button, a value
    # <span>, and a down button. The helper scopes by column ({col} button
    # first/last + {col} > span), independent of the increment/decrement classes.
    hour_picker: str = ".p-datepicker-hour-picker"
    minute_picker: str = ".p-datepicker-minute-picker"

    class Config:
        extra = "forbid"


class FieldsetConfig(_PresetModel):
    """Selectors for the ``expand`` action (collapsible section). Defaults target
    PrimeNG ``p-fieldset``: ``toggle`` is the clickable legend/toggle button and
    ``content`` is the toggleable body (used to detect whether the section is
    already open, so ``expand`` is idempotent). Set ``preset:`` or override for
    other UI kits."""

    _preset_key: ClassVar[str] = "fieldset"
    toggle: str = ".p-fieldset-toggle-button, .p-fieldset-legend"
    content: str = ".p-toggleable-content, .p-fieldset-content-container"

    class Config:
        extra = "forbid"


class PreAuthConfig(BaseModel):
    """Pre-authentication hook for ``steppy run --workers N`` (parallel mode).

    When set, the coordinator injects a one-off ``steppy-preauth`` service into
    each worker's stack and runs ``command`` INSIDE the stack network — gated
    via Compose ``service_completed_successfully`` so it finishes before any
    ``steppy-worker`` claims a scenario. Its job is to populate the shared
    ``.auth`` cache with a session captured at the in-network origin
    (``apps.<role>.internal_url``); a host-captured ``storage-state`` file
    cannot serve that origin because Playwright storage state is origin-bound.
    De-duplicated once-per-run across the N per-worker stacks via an O_EXCL
    lock in the shared coord dir. Unset ⇒ the feature is entirely inert (the
    rendered override + dispatch are byte-identical to today)."""

    # Command run inside the preauth container (cwd = /steppy/project), e.g.
    # ``python auth_login.py --internal``. A plain string is executed via
    # ``sh -c``; an argv list is executed directly.
    command: str | list[str]
    # Seconds before the preauth step is abandoned (covers logging every role);
    # also bounds how long a lock-loser waits for the winner to finish.
    timeout: int = Field(default=600, ge=1)
    # Image for the preauth container. Defaults to the resolved worker image
    # (already carries playwright + chromium + the steppy package).
    image: Optional[str] = None

    class Config:
        extra = "forbid"

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v):
        if isinstance(v, str) and not v.strip():
            raise ValueError("parallel.pre_auth.command must not be empty")
        if isinstance(v, list) and not [p for p in v if str(p).strip()]:
            raise ValueError("parallel.pre_auth.command list must not be empty")
        return v


class RetryConfig(BaseModel):
    """Scenario-level retry pass for ``steppy run --workers N`` (N >= 2).

    Distinct from ``report.retries`` (pytest-rerunfailures), which reruns a
    failed test **in-process, immediately** — same worker, same moment, same
    contention. At scale the dominant flake source is cross-worker contention,
    and retrying into it proves nothing. This pass instead waits for the whole
    queue to drain, then re-runs only the failed scenarios at **reduced
    concurrency** in the still-running stacks.

    That difference is also what makes the outcome meaningful: a scenario that
    fails under 8 workers and passes alone is FLAKY (contention), not broken.

    ``passes: 0`` disables the feature entirely.
    """

    # How many extra passes to run over the failures. 1 is almost always right;
    # >1 mostly buys noise. 0 = off.
    passes: int = Field(default=1, ge=0, le=3)
    # Workers for the retry pass. 1 (the default) means the retries run serially
    # with zero cross-worker contention, which is the whole point.
    workers: int = Field(default=1, ge=1)

    class Config:
        extra = "forbid"


class CircuitBreakerConfig(BaseModel):
    """Abort a parallel run early when nearly everything is failing.

    When the app is down or auth is misconfigured, all N scenarios fail and the
    user waits out the full wall clock to learn it. The coordinator watches
    results stream in and aborts once the evidence is overwhelming.

    Defaults are deliberately conservative — ``min_completed`` scenarios must
    have finished AND ``fail_ratio`` of them must have failed — so a
    legitimately red suite never trips it.
    """

    enabled: bool = True
    # Don't judge before this many scenarios have reported.
    min_completed: int = Field(default=10, ge=1)
    # Fraction of completed scenarios that must have failed to trip. 1.0 =
    # only trip when literally everything failed.
    fail_ratio: float = Field(default=0.9, gt=0.0, le=1.0)

    class Config:
        extra = "forbid"


class ParallelConfig(BaseModel):
    """Settings for ``steppy run --workers N`` (N >= 2). The whole block is
    optional — only consulted when parallel mode is engaged. Single-worker
    runs (no flag, or ``--workers 1``) ignore it entirely."""

    # Default worker count when --workers is omitted on the CLI. CLI value
    # always wins. Accepts an integer or the literal string "auto" — auto
    # resolves to ``min(cpu_count // 2, scenario_count, 8)`` at coordinator
    # startup.
    workers: int | Literal["auto"] = 1
    # Path to the user's docker-compose file (relative to repo root).
    # Coordinator orchestrates one stack per worker via ``-f <user> -f <override>``.
    compose_file: str = "docker-compose.yml"
    # Tag prefix for the worker image. Coordinator probes
    # ``<worker_image>:<package_version>-<src_hash>`` and builds it if missing
    # (when ``build_image`` is true).
    worker_image: str = "steppy-worker"
    # Seconds to wait for ``docker compose up --wait`` to report stack healthy.
    healthcheck_timeout: int = 120
    # Auto-build the worker image on first parallel run if absent locally.
    # Disable with ``--no-build`` on the CLI to fail fast instead.
    build_image: bool = True
    # Escape hatch: build the worker image from your own Dockerfile instead of
    # the one steppy resolves (source checkout → synthesized from the installed
    # package). Path is absolute or relative to the project root.
    dockerfile: Optional[str] = None
    # Build context for ``dockerfile``. Defaults to the Dockerfile's parent
    # directory. Only meaningful together with ``dockerfile``.
    build_context: Optional[str] = None
    # Override the Playwright base image the worker is built FROM. Passed as
    # ``--build-arg PLAYWRIGHT_BASE=...``; folded into the image tag so a
    # change rebuilds. Default: the pin in steppy's bundled Dockerfiles.
    worker_base_image: Optional[str] = None
    # Optional pre-authentication hook run in-network before workers claim.
    # None ⇒ no preauth service is rendered and dispatch is unchanged.
    pre_auth: Optional[PreAuthConfig] = None
    # Scenario-level retry pass over the failures, run after the queue drains.
    retry: RetryConfig = Field(default_factory=RetryConfig)
    # Early abort when nearly every scenario is failing (infra, not tests).
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    # Give every worker a PRIVATE `.auth` dir instead of sharing the project's.
    # The shared dir is bind-mounted read-write into all N workers, so they race
    # on one storage-state file; isolating trades one login for N. Turn this on
    # when each stack has its own database.
    isolate_auth: bool = False

    class Config:
        extra = "forbid"

    @field_validator("workers")
    @classmethod
    def validate_workers(cls, v):
        if isinstance(v, int) and v < 1:
            raise ValueError("parallel.workers must be >= 1 or 'auto'")
        return v

    @model_validator(mode="after")
    def validate_auth_isolation(self):
        """``isolate_auth`` and ``pre_auth`` are mutually exclusive.

        ``pre_auth`` exists to populate ONE shared ``.auth`` cache from a single
        in-network login; ``isolate_auth`` gives every worker its own dir, which
        that single login would never reach. Silently preferring one would leave
        workers unauthenticated with no visible cause, so reject the combination
        at config load.
        """
        if self.isolate_auth and self.pre_auth is not None:
            raise ValueError(
                "parallel.isolate_auth and parallel.pre_auth are mutually exclusive: "
                "pre_auth captures ONE shared session, but isolate_auth gives each worker "
                "a private .auth dir that the shared login never reaches. Drop pre_auth and "
                "let each worker authenticate itself, or turn isolate_auth off."
            )
        return self


class CoverageApiConfig(BaseModel):
    """One backend API tracked by the coverage map (a project may have several,
    e.g. an orders-backend + a billing-backend)."""

    # Identifier baked into endpoint node identity — keep it stable.
    name: str
    # OpenAPI document location: http(s) URL or a local file path, in JSON or
    # YAML (a served /openapi.json is usually the first, a spec committed to the
    # repo the second).
    openapi: str
    # URL prefixes that attribute observed requests to this API. Must mirror
    # the OpenAPI ``servers[].url`` (including any path prefix like /api) and
    # should include Docker-internal ``internal_url`` origins so records
    # captured by parallel workers classify correctly.
    base_urls: list[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    @field_validator("name", "openapi")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("coverage.apis entries need non-empty 'name' and 'openapi'")
        return v


class CoverageConfig(BaseModel):
    """Settings for the coverage map (``steppy run --coverage`` + ``steppy
    coverage`` commands). The whole block is optional — recording is off
    unless ``--coverage`` is passed, regardless of this block."""

    apis: list[CoverageApiConfig] = Field(default_factory=list)
    # Optional sitemap.xml (URL or path) declaring the route surface.
    sitemap: Optional[str] = None
    # Local directory holding the app build's *.js.map files (source maps are
    # read from disk at merge time; inline data: maps work without this).
    sourcemap_dir: Optional[str] = None
    # Persistent sqlite map. Lives OUTSIDE output/ so wiping run artifacts
    # never deletes manual triage flags.
    store_path: str = "./.steppy/coverage.db"
    # Raw per-run records root. Must stay under a worker-writable mount
    # (output/) for parallel runs.
    records_dir: str = "./output/coverage"
    # Playwright resource types treated as API calls (web-standard).
    api_resource_types: list[str] = Field(default_factory=lambda: ["xhr", "fetch"])
    # Extra regexes (full-segment match) collapsed to ``:id`` during route
    # normalization, e.g. '^INV-\\d+$' for business reference segments.
    route_param_patterns: list[str] = Field(default_factory=list)
    # Source-module path prefixes excluded from the module inventory.
    module_ignore: list[str] = Field(default_factory=lambda: ["node_modules/", "webpack/"])
    # Endpoints/routes that can never have a UI test: health checks, webhook
    # receivers, OAuth callbacks, internal cron. Declared HERE rather than only
    # via ``steppy coverage triage set``, because that flag lives in
    # ``.steppy/coverage.db`` and ``.steppy/`` is gitignored — so a fresh CI
    # checkout marks every endpoint ``unknown`` and any percentage computed
    # there is wrong for reasons nobody can fix from CI.
    #
    # Grammar: ``[<METHOD> ]<glob>``, matched against the path template
    # (``/health``, ``GET /health``, ``/internal/*``). ``*`` crosses ``/``, a
    # pattern applies across every configured API, and a method-qualified
    # pattern never matches a route. See steppy/coverage/triage.py.
    non_ui: list[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    @field_validator("non_ui")
    @classmethod
    def validate_non_ui_patterns(cls, v: list[str]) -> list[str]:
        """Reject patterns that cannot mean anything.

        An empty entry would match nothing while looking deliberate, and a bare
        method (``"GET"``) parses as a *glob* of ``GET`` — it would silently
        match no path rather than every GET, which is the opposite of what
        someone writing it intends.

        The import stays inside the body and the grammar keeps one home. Pydantic
        does not run a field validator over a ``default_factory`` value, so a
        project that never declares ``coverage.non_ui`` never reaches this line —
        which is what keeps ``tests/test_no_coverage_import_on_default_path.py``
        honest rather than merely green. ``coverage/__init__.py`` is a docstring
        and ``coverage/triage.py`` is stdlib-only, so a project that *does*
        declare it pays nothing measurable either.
        """
        from .coverage.triage import split_pattern

        for pattern in v:
            if not pattern.strip():
                raise ValueError("coverage.non_ui entries must not be empty")
            method, glob = split_pattern(pattern)
            if not glob:
                raise ValueError(
                    f"coverage.non_ui entry {pattern!r} has no path pattern — "
                    "write '<METHOD> <glob>' or just '<glob>' (e.g. 'GET /health' or '/webhooks/*')"
                )
            if method is None and glob.isalpha() and glob.isupper():
                raise ValueError(
                    f"coverage.non_ui entry {pattern!r} looks like a bare HTTP method — "
                    "it would be read as a path glob and match nothing; add a path (e.g. 'GET /health')"
                )
        return v

    @field_validator("route_param_patterns")
    @classmethod
    def validate_patterns_compile(cls, v: list[str]) -> list[str]:
        import re

        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid coverage.route_param_patterns regex {pattern!r}: {exc}")
        return v

    @field_validator("apis")
    @classmethod
    def validate_unique_api_names(cls, v: list[CoverageApiConfig]) -> list[CoverageApiConfig]:
        names = [a.name for a in v]
        if len(names) != len(set(names)):
            raise ValueError(f"coverage.apis names must be unique, got: {names}")
        return v


class SecurityConfig(BaseModel):
    """Security-sensitive toggles.

    ``allow_shell_capture`` gates the ``capture from.type=shell`` action, which
    runs ``subprocess.run(..., shell=True)`` with values that may be scraped from
    the live page (DOM) or an HTTP response. It is a command-injection vector by
    design, so it is **disabled by default** and must be explicitly enabled.
    """

    allow_shell_capture: bool = False

    class Config:
        extra = "forbid"


class CloudConfig(BaseModel):
    """Where ``steppy bundle upload`` pushes results.

    **The URL only, never a token.** A control-plane token is a credential, and
    ``config.yaml`` is committed — the same line :func:`config._check_credential_hygiene`
    already draws for ``apps.*.auth.credentials``. The token comes from
    ``STEPPY_CLOUD_TOKEN`` or ``--token``, and there is deliberately no config
    key for it to be pasted into.

    Wholly optional and inert when absent, like ``parallel:`` and ``coverage:``.
    Nothing in Steppy assumes a control plane exists; this block is read by one
    command and ignored by every other.
    """

    #: Base URL of the control plane, e.g. ``https://steppy.example.com``. No
    #: version path — the client appends ``/v1``, so a server that moves to v2
    #: does not need every project to edit its config.
    url: Optional[str] = None

    class Config:
        extra = "forbid"


class ReportConfig(BaseModel):
    """CI-friendly reporting artifacts + flaky-retry (FLAWS §6.1).

    All fields are off by default, so omitting this block keeps ``steppy run``
    byte-identical. ``junit_xml``/``html`` are output paths; when set, ``run``
    appends the matching pytest flag. ``trace`` turns on Playwright tracing;
    ``on-failure`` (the useful default when enabled) saves a trace zip only for
    failed tests, ``on`` saves every trace, ``off`` disables it. ``video``
    mirrors ``trace`` for Playwright video recording (``on-failure`` keeps only
    failed-test webms). ``retries`` reruns each failed test up to N times via
    pytest-rerunfailures (the ``steppy-dev[retry]`` extra); 0 disables it. The CLI
    ``--trace``/``--video`` flags force ``on-failure`` and ``--retries N``
    overrides ``retries`` for a single run.
    """

    junit_xml: Optional[str] = None
    html: Optional[str] = None
    trace: Literal["off", "on", "on-failure"] = "off"
    video: Literal["off", "on", "on-failure"] = "off"
    retries: int = Field(default=0, ge=0)  # pytest --reruns N; 0 = off

    class Config:
        extra = "forbid"


class PdfMarginConfig(BaseModel):
    """Page margins for PDF export. Any CSS length Chromium accepts
    (``12mm``, ``0.5in``, ``48px``). The bottom default leaves room for the
    page-number footer."""

    top: str = "12mm"
    right: str = "12mm"
    bottom: str = "14mm"
    left: str = "12mm"

    class Config:
        extra = "forbid"


class PdfConfig(BaseModel):
    """PDF export settings, shared by ``steppy guide --pdf/--handbook`` and
    ``steppy build --pdf/--handbook``.

    Rendering is headless Chromium's print-to-PDF over the already-generated
    guide HTML, so the ``@media print`` rules in the guide templates are what
    the PDF actually shows. Defaults are chosen so an unconfigured project
    gets a usable document: A4 portrait, backgrounds ON (Playwright defaults
    them off, which would drop the step badges and role chip), and a
    ``N / M`` footer.
    """

    format: Literal["A0", "A1", "A2", "A3", "A4", "A5", "A6", "Letter", "Legal", "Tabloid", "Ledger"] = "A4"
    landscape: bool = False
    margin: PdfMarginConfig = Field(default_factory=PdfMarginConfig)
    print_background: bool = True
    scale: float = Field(default=1.0, ge=0.1, le=2.0)  # Chromium's accepted range
    page_numbers: bool = True  # footer "N / M"
    timeout: int = Field(default=30000, ge=1000)  # ms per rendered page

    class Config:
        extra = "forbid"


class TtsHttpConfig(BaseModel):
    """Raw HTTP shape of a text-to-speech request.

    This is the engine every vendor preset runs on: ``elevenlabs``, ``openai``,
    ``azure`` and ``google`` are rows of data in ``steppy/tts/presets.py`` that
    fill these fields in, and a vendor Steppy has never heard of is reachable by
    writing them out here directly. That is what makes "swap the provider by
    editing config.yaml" literally true rather than aspirational.

    String leaves in ``headers``/``json``/``body`` may contain ``{text}``,
    ``{voice}``, ``{locale}``, ``{model}``, ``{speed}``, ``{api_key}``,
    ``{region}``, plus any key from ``TtsConfig.options``. Substitution happens
    on the parsed structure, never on serialized JSON, so narration containing
    a brace cannot corrupt the request body.
    """

    url: Optional[str] = None
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    # Request body as JSON. Mutually exclusive with ``body`` (raw text, e.g. SSML).
    json_body: Optional[dict] = Field(default=None, alias="json")
    body: Optional[str] = None
    content_type: Optional[str] = None
    # ``binary`` = the response *is* the audio; ``json`` = dig it out of a field.
    response: Literal["binary", "json"] = "binary"
    # Dotted path to the audio field when ``response: json`` (e.g. "audioContent").
    audio_path: Optional[str] = None
    encoding: Literal["raw", "base64"] = "raw"
    format: Optional[str] = None

    class Config:
        extra = "forbid"
        populate_by_name = True

    @model_validator(mode="after")
    def validate_body_exclusivity(self):
        if self.json_body is not None and self.body is not None:
            raise ValueError("video.tts.http: set either 'json' or 'body', not both")
        if self.response == "json" and not self.audio_path:
            raise ValueError("video.tts.http: 'audio_path' is required when response is 'json'")
        return self


class PiperConfig(BaseModel):
    """Settings for the ``piper`` provider — the free, offline, neural voice.

    Steppy shells out to ``python -m piper`` and never imports the package:
    piper1-gpl is GPL-3.0, and an arms-length subprocess keeps it a user-installed
    tool rather than a dependency of a permissively-licensed wheel. It is
    deliberately absent from ``pyproject.toml`` extras for the same reason —
    install it yourself with ``pip install piper-tts``.

    Voice models are per-language (``fr_FR-siwis-medium``, ``nl_NL-mls-medium``),
    which is why ``TtsConfig.voice`` accepts a per-locale map.
    """

    # Under .steppy/ so `output/` cleanup never re-downloads tens of MB of voices.
    data_dir: str = ".steppy/voices"
    download: bool = True
    # Interpreter to run ``-m piper`` with. None = the one running Steppy.
    python: Optional[str] = None

    class Config:
        extra = "forbid"


class TtsConfig(BaseModel):
    """Text-to-speech provider selection for video narration.

    ``provider`` is a plain string rather than a ``Literal`` on purpose: besides
    the built-ins (``none``, ``piper``, ``espeak``, ``http``) and the vendor
    presets (``elevenlabs``, ``openai``, ``azure``, ``google``), a third party
    can register one through the ``steppy.tts_providers`` entry-point group, and
    a closed enum would make that unreachable.

    Default is ``none``: captions only, no audio, no network, no bill. Adding a
    voice is one config line.

    Put the key in the environment, not here — ``${ELEVENLABS_API_KEY}`` is
    resolved at config load, and a literal value earns a hygiene warning.
    """

    provider: str = "none"
    # str for one voice everywhere, or {locale: voice} — the multilingual case.
    voice: Optional[Union[str, dict[str, str]]] = None
    model: Optional[str] = None
    speed: float = Field(default=1.0, gt=0, le=4.0)
    format: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    region: Optional[str] = None
    timeout: int = Field(default=60000, ge=1000)
    # Synthesized clips are cached under .steppy/ and keyed by content, so a
    # re-run of an unchanged guide costs nothing. Disabling it re-bills.
    cache: bool = True
    cache_dir: str = ".steppy/tts-cache"
    # Provider-specific extras, also available as {placeholders} in http/presets.
    options: dict[str, Any] = Field(default_factory=dict)
    piper: PiperConfig = Field(default_factory=PiperConfig)
    http: TtsHttpConfig = Field(default_factory=TtsHttpConfig)

    class Config:
        extra = "forbid"

    def voice_for(self, locale: str) -> Optional[str]:
        """Resolve the voice for ``locale`` from the str-or-map form."""
        if self.voice is None:
            return None
        if isinstance(self.voice, str):
            return self.voice
        return self.voice.get(locale)


class VideoBrandingConfig(BaseModel):
    """Marks burned into every frame. ``logo`` is a project-relative image path;
    it is embedded as a data URI, since the render page has no network."""

    logo: Optional[str] = None
    accent: Optional[str] = None
    font_family: Optional[str] = None

    class Config:
        extra = "forbid"


class VideoStampConfig(BaseModel):
    """Provenance line burned into the frame — the audit-trail artifact a
    regulated buyer asks for ("verified against 2.14 on 2026-08-05").

    ``text`` accepts ``{version}``, ``{date}``, ``{locale}``, ``{layout}`` and
    ``{slug}``. Point ``version`` at the environment (``${STEPPY_RELEASE}``) so
    CI stamps the real tag.
    """

    enabled: bool = False
    text: str = "{version} · {date}"
    version: str = "dev"

    class Config:
        extra = "forbid"


class VideoConfig(BaseModel):
    """Narrated-video settings for guides declaring ``modes: ['video']``.

    Only consulted once a render is armed with ``steppy build --video`` or
    ``steppy guide --video``; every other command ignores this block, and a
    guide that declares the mode without the flag renders nothing.

    Frames are stepped deterministically rather than screen-recorded, so the
    output depends on the manifest and this config — not on how fast the render
    host happens to be. Two consequences worth knowing: ``fps`` and ``width``
    are the cost knobs (a render is frames x screenshot latency), and because
    cursor travel is timed over *display* distance, changing ``width`` changes
    the video's duration.

    Requires ffmpeg on PATH — including with ``tts.provider: none``, since a
    silent captioned video still has to be encoded.
    """

    fps: int = Field(default=24, ge=1, le=60)
    # Output width in px; height follows the capture's aspect (so a mobile
    # layout yields a portrait video). Both are rounded down to even for libx264.
    width: int = Field(default=1280, ge=160, le=3840)
    max_height: Optional[int] = Field(default=None, ge=160)
    captions: Literal["band", "off"] = "band"
    crf: int = Field(default=23, ge=0, le=51)
    preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"] = (
        "medium"
    )
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    intro_ms: int = Field(default=1200, ge=0)
    outro_ms: int = Field(default=1500, ge=0)
    hold_ms: int = Field(default=700, ge=0)
    min_step_ms: int = Field(default=1500, ge=0)
    narration_pad_ms: int = Field(default=400, ge=0)
    ffmpeg_path: Optional[str] = None
    # Per-guide budget for the whole render, and for one page load.
    timeout: int = Field(default=120000, ge=1000)
    # Skip a re-render whose inputs are unchanged. Off means every build pays
    # full price for every guide, which "regenerated on every release" cannot
    # afford on a real corpus.
    cache: bool = True
    cache_dir: str = ".steppy/video-cache"
    branding: VideoBrandingConfig = Field(default_factory=VideoBrandingConfig)
    stamp: VideoStampConfig = Field(default_factory=VideoStampConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)

    class Config:
        extra = "forbid"


class AppAssetsTourTarget(BaseModel):
    """Write the tour files flat into a directory your app serves (bundled mode)."""

    type: Literal["app-assets"]
    path: str

    class Config:
        extra = "forbid"

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("an app-assets tour target needs a non-empty 'path'")
        return v


class GuideTourTarget(BaseModel):
    """Publish the tours with the static guide site, under ``tours/<build-id>/``."""

    type: Literal["guide"]

    class Config:
        extra = "forbid"


TourTarget = Annotated[Union[AppAssetsTourTarget, GuideTourTarget], Field(discriminator="type")]


class ToursConfig(BaseModel):
    """In-app tours: guided popovers over the live app, compiled from scenarios.

    Absent or ``enabled: false`` ⇒ no tour output at all and every existing
    output byte-identical. When enabled, ``steppy generate`` and ``steppy
    tours`` compile every opted-in scenario into ``<output.tours_dir>/<build-id>/``
    plus a flat copy per ``app-assets`` target, and ``generate`` appends a CI
    replay to each opted-in scenario's test file (``replay``). A ``guide``
    target makes the static ``steppy build`` republish the tree.

    ``build_id`` is ``git-sha``, ``git-sha:<repo-path>`` or a literal (see
    :mod:`steppy.buildid`); an unset ``${VAR}`` is accepted here and only
    rejected when tours are emitted, so unrelated commands keep working.
    ``layouts`` maps the layout names scenario steps use to CSS media queries
    the overlay evaluates (skip-only: a step whose layouts do not match is
    skipped, never re-routed). ``info_target_click`` is the default of the
    step-level ``tour-target-click`` for every targeted information step.
    """

    enabled: bool = False
    default: bool = False
    build_id: str = "git-sha"
    locales: Optional[list[str]] = None
    default_locale: Optional[str] = None
    guide_base_url: Optional[str] = None
    test_id_attribute: str = "data-testid"
    layouts: Optional[dict[str, str]] = None
    replay: bool = True
    info_target_click: Optional[Literal["pass", "next", "block"]] = None
    targets: list[TourTarget] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    @field_validator("build_id")
    @classmethod
    def validate_build_id(cls, v: str) -> str:
        return check_build_id_syntax(v)

    @field_validator("locales")
    @classmethod
    def validate_locales(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("tours.locales must be a non-empty list (or omitted)")
        for tag in v:
            validate_locale_tag(tag)
        return v  # duplicates and the size cap: SteppyConfig.validate_tours, on the effective list

    @field_validator("default_locale")
    @classmethod
    def validate_default_locale(cls, v):
        if v is not None:
            validate_locale_tag(v)
        return v

    @field_validator("guide_base_url")
    @classmethod
    def validate_guide_base_url(cls, v):
        if v is None or "${" in v:
            return v
        from urllib.parse import urlsplit

        url = v.strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"tours.guide_base_url {v!r} must be an absolute http(s) URL")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("tours.guide_base_url must not carry a query, fragment or credentials")
        if not parts.hostname.isascii():
            raise ValueError("tours.guide_base_url: write an internationalised host in its punycode (xn--) form")
        return url if url.endswith("/") else url + "/"

    @field_validator("test_id_attribute")
    @classmethod
    def validate_test_id_attribute(cls, v: str) -> str:
        return check_test_id_attribute(v)

    @field_validator("layouts")
    @classmethod
    def validate_layouts(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("tours.layouts must map at least one layout (or be omitted)")
        out = {}
        for name, query in v.items():
            if not is_layout_name(name):
                raise ValueError(f"tours.layouts: {name!r} is not a valid layout name")
            q = query.strip() if isinstance(query, str) else query
            if not is_media_query(q):
                raise ValueError(f"tours.layouts[{name!r}]: {query!r} is not a media query this player accepts")
            out[name] = q
        return out

    @model_validator(mode="after")
    def validate_targets(self):
        if self.enabled and not self.targets:
            raise ValueError(
                "tours.enabled needs at least one target: {type: app-assets, path: <dir your app serves>} "
                "and/or {type: guide}"
            )
        if sum(1 for t in self.targets if t.type == "guide") > 1:
            raise ValueError("tours.targets lists more than one 'guide' target")
        paths = [os.path.normpath(t.path) for t in self.targets if t.type == "app-assets"]
        if len(set(paths)) != len(paths):
            raise ValueError("tours.targets lists the same app-assets path twice")
        return self


class SteppyConfig(BaseModel):
    """Main Steppy configuration."""

    apps: dict[str, AppConfig]
    auth: GlobalAuthConfig = Field(default_factory=GlobalAuthConfig)
    locales: list[str] = Field(default_factory=lambda: list(DEFAULT_LOCALES))
    default_locale: str = DEFAULT_LOCALE
    screenshot: ScreenshotConfig = Field(default_factory=ScreenshotConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    # Selector preset for the ``select-option`` action's component dropdown.
    # Defaults to PrimeNG p-select; override for other UI kits.
    dropdown: DropdownConfig = Field(default_factory=DropdownConfig)
    # Selector preset for the ``confirm`` action's confirmation dialog.
    # Defaults to PrimeNG p-confirmDialog; override for other UI kits.
    dialog: DialogConfig = Field(default_factory=DialogConfig)
    # Selector preset for the ``toggle`` action (switch + segmented button).
    toggle: ToggleConfig = Field(default_factory=ToggleConfig)
    # Selector preset for the ``pick-date``/``pick-time`` calendar/clock widgets.
    datepicker: DatepickerConfig = Field(default_factory=DatepickerConfig)
    # Selector preset for the ``expand`` action (collapsible section / fieldset).
    fieldset: FieldsetConfig = Field(default_factory=FieldsetConfig)
    # Optional. Only required when invoking ``steppy run --workers N`` with
    # N >= 2. Single-worker runs ignore this block entirely.
    parallel: Optional[ParallelConfig] = None
    # Optional. Consulted by ``steppy run --coverage`` and the ``steppy
    # coverage`` commands; plain runs ignore this block entirely.
    coverage: Optional[CoverageConfig] = None
    # Security toggles (opt-in shell capture, etc.). Defaults are the safe
    # posture, so omitting this block keeps the locked-down behavior.
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    # CI reporting artifacts (JUnit XML, HTML report, Playwright trace). All off
    # by default, so omitting this block keeps `steppy run` byte-identical.
    report: ReportConfig = Field(default_factory=ReportConfig)
    # Paper settings for PDF export. Only consulted by `steppy guide --pdf`
    # and `steppy build --pdf` (and their `--handbook` siblings); every other
    # command ignores this block entirely.
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    # Narrated-video settings. Only consulted by `steppy build --video` and
    # `steppy guide --video` for guides declaring `modes: ['video']`; every
    # other command ignores this block entirely.
    video: VideoConfig = Field(default_factory=VideoConfig)
    # Optional. Only consulted by `steppy bundle upload`. Holds the control
    # plane's URL and nothing else — the token is env-only, on purpose.
    cloud: Optional[CloudConfig] = None
    # Optional. In-app tours compiled from `tour:`-enabled scenarios. Declared
    # last so its validator can read every other block (locales, layouts).
    tours: Optional[ToursConfig] = None

    class Config:
        extra = "forbid"

    @field_validator("tours")
    @classmethod
    def validate_tours(cls, v, info):
        """Cross-block rules: the tour locales and layouts must be ones the project has."""
        if v is None:
            return v
        locales = v.locales or info.data.get("locales", DEFAULT_LOCALES)
        if v.enabled or v.locales is not None:
            # Each tour locale names a file (`<id>.<locale>.json`), so the list a tour set is
            # emitted for — inherited from `locales` when tours.locales is omitted — must not
            # hold one language twice (`fr-BE`/`fr_be` collide on NTFS/APFS) or exceed the cap
            # the overlay enforces.
            where = "tours.locales" if v.locales is not None else "locales (inherited by tours)"
            if len(locales) > MAX_LOCALES:
                raise ValueError(f"{where} lists {len(locales)} locales; in-app tours support at most {MAX_LOCALES}")
            seen: dict[str, str] = {}
            for tag in locales:
                key = normalize_locale(tag).lower()
                if key in seen:
                    raise ValueError(f"{where} lists the same locale twice: {seen[key]!r} and {tag!r}")
                seen[key] = tag
        default = v.default_locale or info.data.get("default_locale", DEFAULT_LOCALE)
        if default not in locales:
            raise ValueError(
                f"the tours default locale {default!r} must be one of the tour locales {locales} "
                "(set tours.default_locale, or add it to tours.locales)"
            )
        if v.layouts:
            screenshot = info.data.get("screenshot")
            known = set(screenshot.layouts) if screenshot is not None else set()
            unknown = sorted(set(v.layouts) - known)
            if unknown:
                raise ValueError(
                    f"tours.layouts names {unknown} that screenshot.layouts does not declare "
                    f"(declared: {sorted(known) or 'none'}); step `layouts:` refer to screenshot.layouts"
                )
        return v

    @field_validator("locales")
    @classmethod
    def validate_locale_tags(cls, v: list[str]) -> list[str]:
        """Every locale must be a usable BCP-47 tag.

        The capture side writes these verbatim as screenshot directory segments
        (``output/screenshots/<scenario>/<locale>/``) that ``steppy build`` later
        reads back, and the build side has validated its own ``locales`` since
        the hreflang work — so leaving this one unchecked meant the two halves of
        one project could disagree about what a locale is, with the disagreement
        surfacing as missing captures rather than as an error.

        Validated, **not** rewritten: the authored spelling is a join key with
        those directories and with the app's own i18n codes. See
        ``src/steppy/locales.py``.
        """
        for item in v:
            validate_locale_tag(item)
        return v

    @field_validator("default_locale")
    @classmethod
    def validate_default_locale(cls, v: str, info) -> str:
        """Validate that default_locale is a usable tag and is in ``locales``."""
        validate_locale_tag(v)
        # Note: info.data contains already-validated fields
        locales = info.data.get("locales", DEFAULT_LOCALES)
        if v not in locales:
            raise ValueError(f"default_locale '{v}' must be in locales list: {locales}")
        return v
