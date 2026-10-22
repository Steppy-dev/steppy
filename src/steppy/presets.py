"""UI-kit selector presets for the component-driver config blocks (FLAWS §4.3).

The ``select-option`` / ``confirm`` / ``toggle`` / ``pick-date`` / ``pick-time`` /
``expand`` actions drive framework components (dropdowns, dialogs, switches,
datepickers, fieldsets) whose internal markup has no stable ``data-testid``.
Their selectors therefore come from a config preset. Historically the only
preset was PrimeNG, baked in as the field defaults.

A config block may now name a ``preset:`` and get a whole kit's selectors at
once — e.g. ``dropdown: {preset: material}``. Explicitly set selector fields
always win over the preset; omitting ``preset:`` keeps the PrimeNG defaults, so
existing configs are byte-identical.

``primeng`` is authoritative (it mirrors the long-standing defaults, verified
against real PrimeNG apps). Every other preset — ``material`` (Angular Material
MDC), ``primevue`` (PrimeVue), ``mui`` (Material UI / React), ``vuetify``
(Vuetify 3 / Vue), and ``shadcn`` (shadcn/ui + Radix / React) — is a
**best-effort starter set**: the markup varies by framework version and by
add-ons, so treat these selectors as a starting point and override any that
does not match your app. They are foundational and will be hardened against
real markup as Steppy is exercised on cross-framework showcase projects; until
then, `steppy validate` + a quick run against your app is the way to confirm
them.

Each entry maps a config key (``dropdown``/``dialog``/``toggle``/
``datepicker``/``fieldset``) to the selector fields of the matching model.
Only selector fields appear here — non-selector fields (e.g. dropdown
``settle``) are never touched by a preset.
"""

PRESETS: dict[str, dict[str, dict[str, str]]] = {
    # ── PrimeNG (default) ────────────────────────────────────────────────────
    # Mirrors the field defaults on the models below. Keep in sync if a default
    # changes, so `preset: primeng` stays equivalent to omitting `preset`.
    "primeng": {
        "dropdown": {
            "trigger": "p-select",
            "overlay": ".p-select-overlay",
            "option": ".p-select-option, .p-select-list-container li",
            "filter": ".p-select-filter-container input, .p-select-filter",
        },
        "dialog": {
            "overlay": ".p-confirmdialog, p-confirmdialog",
            "accept": "button.p-confirmdialog-accept-button, button.p-confirm-dialog-accept",
            "reject": "button.p-confirmdialog-reject-button, button.p-confirm-dialog-reject",
            "danger": "button.p-button-danger, button.p-confirmdialog-accept-button",
        },
        "toggle": {
            "switch": 'input[role="switch"]',
            "segment": ".p-togglebutton",
        },
        "datepicker": {
            "panel": ".p-datepicker-panel",
            "next_button": ".p-datepicker-next-button",
            "prev_button": ".p-datepicker-prev-button",
            "day_cell": ".p-datepicker-day-cell:not(.p-datepicker-other-month)",
            "trigger": "button.p-datepicker-dropdown, button.p-datepicker-trigger",
            "hour_picker": ".p-datepicker-hour-picker",
            "minute_picker": ".p-datepicker-minute-picker",
        },
        "fieldset": {
            "toggle": ".p-fieldset-toggle-button, .p-fieldset-legend",
            "content": ".p-toggleable-content, .p-fieldset-content-container",
        },
    },
    # ── Angular Material (MDC) — starter set, verify against your app ─────────
    "material": {
        "dropdown": {
            "trigger": "mat-select",
            "overlay": ".cdk-overlay-pane .mat-mdc-select-panel",
            "option": "mat-option, .mat-mdc-option",
            # mat-select has no native filter; ngx-mat-select-search adds one.
            "filter": ".mat-select-search-input, input.mat-mdc-input-element",
        },
        "dialog": {
            "overlay": "mat-dialog-container, .mat-mdc-dialog-container",
            "accept": '.mat-mdc-dialog-actions button[color="primary"], .mat-mdc-dialog-actions button:last-child',
            "reject": '.mat-mdc-dialog-actions button:not([color="primary"]), .mat-mdc-dialog-actions button:first-child',
            "danger": '.mat-mdc-dialog-actions button[color="warn"]',
        },
        "toggle": {
            "switch": 'mat-slide-toggle button[role="switch"], .mat-mdc-slide-toggle button',
            "segment": ".mat-button-toggle",
        },
        "datepicker": {
            "panel": ".mat-datepicker-content, .mat-calendar",
            "next_button": ".mat-calendar-next-button",
            "prev_button": ".mat-calendar-previous-button",
            "day_cell": ".mat-calendar-body-cell:not(.mat-calendar-body-disabled)",
            "trigger": ".mat-datepicker-toggle button",
            # Material core has no time picker; ngx-mat-timepicker markup shown.
            "hour_picker": ".ngx-mat-timepicker .mat-mdc-form-field:nth-child(1) input",
            "minute_picker": ".ngx-mat-timepicker .mat-mdc-form-field:nth-child(2) input",
        },
        "fieldset": {
            "toggle": "mat-expansion-panel-header, .mat-expansion-panel-header",
            "content": ".mat-expansion-panel-content, .mat-expansion-panel-body",
        },
    },
    # ── PrimeVue — starter set, verify against your app ───────────────────────
    # PrimeVue shares PrimeNG's ``.p-*`` DNA, so the cheapest cross-framework
    # win is to reuse the PrimeNG selectors almost verbatim. Kept as its own
    # entry (rather than aliasing) so it can diverge as PrimeVue-specific markup
    # is discovered on real apps.
    "primevue": {
        "dropdown": {
            "trigger": ".p-select, .p-dropdown",
            "overlay": ".p-select-overlay, .p-dropdown-panel",
            "option": ".p-select-option, .p-dropdown-item, .p-select-list-container li",
            "filter": ".p-select-filter, .p-dropdown-filter, .p-select-filter-container input",
        },
        "dialog": {
            "overlay": ".p-confirmdialog, .p-confirm-dialog",
            "accept": "button.p-confirmdialog-accept-button, button.p-confirm-dialog-accept",
            "reject": "button.p-confirmdialog-reject-button, button.p-confirm-dialog-reject",
            "danger": "button.p-button-danger, button.p-confirmdialog-accept-button",
        },
        "toggle": {
            "switch": 'input[role="switch"], .p-toggleswitch-input',
            "segment": ".p-togglebutton",
        },
        "datepicker": {
            "panel": ".p-datepicker-panel",
            "next_button": ".p-datepicker-next-button",
            "prev_button": ".p-datepicker-prev-button",
            "day_cell": ".p-datepicker-day-cell:not(.p-datepicker-other-month)",
            "trigger": "button.p-datepicker-dropdown, button.p-datepicker-trigger",
            "hour_picker": ".p-datepicker-hour-picker",
            "minute_picker": ".p-datepicker-minute-picker",
        },
        "fieldset": {
            "toggle": ".p-fieldset-toggle-button, .p-fieldset-legend",
            "content": ".p-toggleable-content, .p-fieldset-content-container",
        },
    },
    # ── Material UI (React / MUI v5–v6) — starter set, verify against your app ─
    # MUI ships stable ``.Mui*`` class names; the date picker is the ``@mui/x``
    # add-on (``.MuiPickers*`` / ``.MuiClock*``). Override if you use a custom
    # theme that strips the default classes.
    "mui": {
        "dropdown": {
            "trigger": ".MuiSelect-select, .MuiAutocomplete-root .MuiInputBase-root",
            "overlay": ".MuiMenu-paper, .MuiAutocomplete-popper .MuiPaper-root",
            "option": ".MuiMenuItem-root, .MuiAutocomplete-option",
            "filter": ".MuiAutocomplete-input, .MuiInputBase-input",
        },
        "dialog": {
            "overlay": ".MuiDialog-paper",
            "accept": ".MuiDialogActions-root button:last-child",
            "reject": ".MuiDialogActions-root button:first-child",
            "danger": ".MuiDialogActions-root button.MuiButton-colorError, .MuiDialogActions-root button.MuiButton-containedError",
        },
        "toggle": {
            "switch": ".MuiSwitch-input",
            "segment": ".MuiToggleButton-root",
        },
        "datepicker": {
            "panel": ".MuiPickersLayout-root, .MuiDateCalendar-root",
            "next_button": '.MuiPickersArrowSwitcher-button[title="Next month"], .MuiPickersArrowSwitcher-button:last-child',
            "prev_button": '.MuiPickersArrowSwitcher-button[title="Previous month"], .MuiPickersArrowSwitcher-button:first-child',
            "day_cell": ".MuiPickersDay-root:not(.Mui-disabled):not(.MuiPickersDay-dayOutsideMonth)",
            "trigger": ".MuiInputAdornment-root button, button.MuiIconButton-root",
            # @mui/x digital clock: sections of selectable minute/hour options.
            "hour_picker": ".MuiMultiSectionDigitalClockSection-root:nth-child(1)",
            "minute_picker": ".MuiMultiSectionDigitalClockSection-root:nth-child(2)",
        },
        "fieldset": {
            "toggle": ".MuiAccordionSummary-root",
            "content": ".MuiAccordionDetails-root, .MuiCollapse-root",
        },
    },
    # ── Vuetify 3 (Vue) — starter set, verify against your app ────────────────
    # Vuetify renders overlays into a ``.v-overlay-container``; components use
    # stable ``.v-*`` BEM-ish classes. The date picker is ``.v-date-picker``.
    "vuetify": {
        "dropdown": {
            "trigger": ".v-select, .v-autocomplete",
            "overlay": ".v-overlay .v-list, .v-menu > .v-overlay__content",
            "option": ".v-list-item",
            "filter": ".v-autocomplete input, .v-field__input",
        },
        "dialog": {
            "overlay": ".v-dialog .v-card, .v-overlay--active .v-card",
            "accept": ".v-card-actions button:last-child",
            "reject": ".v-card-actions button:first-child",
            "danger": ".v-card-actions button.text-error, .v-card-actions button.bg-error",
        },
        "toggle": {
            "switch": ".v-switch input",
            "segment": ".v-btn-toggle .v-btn",
        },
        "datepicker": {
            "panel": ".v-date-picker, .v-date-picker-month",
            "next_button": ".v-date-picker-controls button:last-child, .v-date-picker-month__header button:last-child",
            "prev_button": ".v-date-picker-controls button:first-child, .v-date-picker-month__header button:first-child",
            "day_cell": ".v-date-picker-month__day:not(.v-date-picker-month__day--hide-adjacent) .v-btn:not(.v-btn--disabled)",
            "trigger": ".v-field__append-inner button, .v-input__append button",
            # Vuetify core has no time picker; ``.v-time-picker`` is the add-on.
            "hour_picker": ".v-time-picker-clock__container, .v-time-picker__controls .v-time-picker__hour",
            "minute_picker": ".v-time-picker-clock__container, .v-time-picker__controls .v-time-picker__minute",
        },
        "fieldset": {
            "toggle": ".v-expansion-panel-title",
            "content": ".v-expansion-panel-text",
        },
    },
    # ── shadcn/ui (Radix primitives, React) — starter set, verify per app ─────
    # shadcn/ui composes Radix primitives, which expose ARIA roles and
    # ``[data-state]`` / ``[data-radix-*]`` attributes rather than stable class
    # names — so these selectors lean on roles/data-attributes. Class names come
    # from your own Tailwind copy of each component, so override where you
    # renamed things.
    "shadcn": {
        "dropdown": {
            # Radix Select trigger + Command combobox both expose role.
            "trigger": '[role="combobox"]',
            "overlay": '[role="listbox"], [data-radix-popper-content-wrapper]',
            "option": '[role="option"]',
            "filter": '[role="combobox"] input, [cmdk-input]',
        },
        "dialog": {
            "overlay": '[role="alertdialog"], [role="dialog"]',
            # shadcn AlertDialog: Cancel then Action, in DOM order.
            "accept": '[role="alertdialog"] button:last-of-type, [role="dialog"] button:last-of-type',
            "reject": '[role="alertdialog"] button:first-of-type, [role="dialog"] button:first-of-type',
            "danger": '[role="alertdialog"] button.bg-destructive, [role="dialog"] button.bg-destructive',
        },
        "toggle": {
            "switch": '[role="switch"]',
            "segment": '[role="radiogroup"] button, [role="group"] [data-state]',
        },
        "datepicker": {
            # shadcn date picker = a Popover wrapping react-day-picker (rdp).
            "panel": ".rdp, [data-radix-popper-content-wrapper] table",
            "next_button": 'button[name="next-month"], .rdp-button_next',
            "prev_button": 'button[name="previous-month"], .rdp-button_previous',
            "day_cell": ".rdp-day:not([disabled]):not(.rdp-day_outside)",
            "trigger": '[data-state] > button, button[aria-haspopup="dialog"]',
            # react-day-picker has no time picker; leave the PrimeNG-style
            # placeholders empty-ish so pick-time is clearly unsupported until a
            # real app supplies a widget. Override these if you add one.
            "hour_picker": '[data-time="hour"]',
            "minute_picker": '[data-time="minute"]',
        },
        "fieldset": {
            # shadcn Accordion (Radix): trigger + region content.
            "toggle": "[data-radix-collection-item] button, [data-state] > h3 button",
            "content": '[role="region"], [data-state="open"] > [data-radix-accordion-content]',
        },
    },
}


def available_presets() -> list[str]:
    """Sorted list of known preset names (for error messages)."""
    return sorted(PRESETS)


# ── IdP login-form presets (FLAWS §6.2) ──────────────────────────────────────
# The generic hosted-login form-fill (auth ``mode: form``, formerly ``auth0``)
# needs three selectors: username field, password field, submit button. Most
# IdPs differ only in these — so a named ``preset:`` fills them at once, exactly
# like the UI-kit presets above but on a separate registry so the two name
# spaces don't collide. ``auth0`` mirrors the historical FormAuthConfig defaults.
IDP_PRESETS: dict[str, dict[str, dict[str, str]]] = {
    "auth0": {
        "form": {
            "username_selector": "input[name='username']",
            "password_selector": "input[name='password']",
            "submit_selector": "button[data-action-button-primary='true']",
            # Auth0 Guardian OTP prompt — best-effort starter, verify per tenant.
            "totp_selector": "input[name='code']",
        },
    },
    "keycloak": {
        "form": {
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "#kc-login",
            # Keycloak OTP form — best-effort starter, verify per realm/theme.
            "totp_selector": "#otp",
        },
    },
}
