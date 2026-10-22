# Steppy — New Features Specification

> **Historical planning doc.** Both features below have shipped. Path references
> to `examples/…` predate the repo's current layout — the in-repo scenario corpus
> now lives in [`scenarios/`](scenarios/) (e.g. `scenarios/admin--create-project.md`),
> and the authoritative format spec is [`STEPPY-REFERENCE.md`](STEPPY-REFERENCE.md).

This document specifies two new features for Steppy:

1. **Cursors & Interaction Vocabulary** — visual polish for the guide rendering layer
2. **Keycloak Integration** — second auth provider alongside the existing Auth0 support

Both features can be implemented independently. Cursors should ship first (smaller scope, immediate visual impact). Keycloak should be implemented as a refactor of the current Auth0-only auth handling into a provider-agnostic abstraction.

A reference scenario is available at `examples/create-customer.md` and should be treated as the canonical format. **Do not invent new syntax.** All additions described below extend the existing format; they do not replace it.

---

## Feature 1 — Cursors & Interaction Vocabulary

### Goal

Make Steppy guides feel like a polished, guided tour rather than a sequence of static screenshots with arrows. The cursor is the single most visible element of a guide and currently does not exist as a first-class concept in the guide rendering layer.

This feature introduces:

- An animated cursor that moves between targets
- A mapping from existing scenario actions to visual cursor behavior
- A small state machine per interaction (idle → pre-action → action → post-action)

### Non-goals

- The recorder feature (separate spec)
- Live-overlay mode against a real running app (separate spec; this feature targets the screenshot-based guide rendering)
- Multi-device cursor variants (separate spec)
- New scenario actions — this feature **renders** existing actions, it does not add new ones

### Mapping existing actions to cursor behavior

Steppy scenarios already define actions like `click`, `fill`, `fill-form`, `navigate`, `assert`, `wait-for-text`, `custom`, `auth0-login`. The cursor layer must map each existing action to a visual idiom. The mapping is closed for v1.

| Scenario action | Cursor behavior |
|---|---|
| `click` | Cursor travels to the selector, plays pre-action pulse, then ripple ring on click |
| `fill` | Cursor travels to the selector, lands, then a small keyboard glyph appears with the typed value rendered character-by-character |
| `fill-form` | Cursor visits each field in declaration order, performing a `fill`-style animation per field |
| `navigate` | Cursor is hidden during the navigation; the new screenshot fades in, then the cursor reappears at a neutral position |
| `assert` | Cursor is hidden — assertions are invisible to the guide viewer (they exist for the test runner, not the guide) |
| `wait-for-text` | Cursor is hidden during the wait, reappears when the text becomes visible |
| `auth0-login` (and future `keycloak-login`) | Cursor is hidden — auth flows are not part of the guide narrative |
| `custom` | Cursor is hidden by default; can be overridden per step (see below) |

For `custom` steps, an optional `guide-cursor` key can override the default hide-cursor behavior:

```yaml
### 8. Remplir la date de naissance via clavier
- role: editor
- action: custom
- code: "..."
- guide-cursor:
    behavior: type
    target: '[data-testid="customer-form-input-date-of-birth"] input'
    value: "21-06-1985"
```

This is the **only** new key being added to the scenario step format for this feature. It is optional and only meaningful when the action is `custom`.

### Hidden by default for non-visual steps

Steps with `action: assert`, `action: wait-for-text`, or any other non-visual action must not produce cursor movement in the guide. Assertions exist for the test runner; the guide viewer should not see a cursor twitch on a verification step. The same screenshot stays on screen, the narration text (`guide-text`) updates if present, then the next visual step takes over.

This is important and easy to get wrong: the guide rendering layer should iterate the scenario steps in order but **only animate the cursor for visually meaningful actions**. Non-visual steps still advance narration and may still update the displayed screenshot if `screenshot:` is set.

### Cursor state machine

For every visually meaningful step, the cursor goes through four states:

1. **idle** — cursor is moving toward the target
2. **pre_action** — cursor has arrived at the target, brief anticipation effect (small pulse or ring growing for ~200ms)
3. **action** — the actual interaction fires (ripple for click, keyboard glyph for fill, etc.)
4. **post_action** — brief confirmation (~150ms) before the next step begins

State durations are configurable globally via scenario frontmatter, with sensible defaults. Add a new optional top-level frontmatter block alongside the existing `title`, `description`, `tags`, `seed`, `priority`, `auth` keys:

```yaml
---
title: Création d'un client par l'éditeur
# ... existing keys ...
guide:
  cursor:
    pre_action_ms: 200
    action_ms: 300
    post_action_ms: 150
    travel_speed_px_per_ms: 1.5
---
```

Defaults are used when the block is omitted, which is the expected case for almost all scenarios.

### Movement between targets

The cursor must **animate** between targets, not teleport. Requirements:

- Travel duration is proportional to distance, computed from `travel_speed_px_per_ms`
- Easing: `ease-in-out` cubic — never linear
- Path: slight arc, not a straight line. Use a quadratic Bézier with the control point offset perpendicular to the straight line by ~15% of the distance. This makes the movement feel organic rather than robotic.
- During travel, the cursor is in the `idle` state (no ripple, no glyphs)

### Implementation notes

- **Render as DOM, not SVG.** The cursor should be an absolutely-positioned `<div>` (or small set of divs) inside the guide rendering layer, animated with CSS transitions and CSS animations. SVG was considered but rejected: DOM gives ripple/pulse/trail effects basically for free with CSS, and the byte cost is irrelevant in the guide context.
- **Coordinate system.** Cursor positions are derived from selector resolution against the screenshot. The guide rendering layer needs a way to know where a given selector was on screen at the moment the screenshot was captured. If this mapping does not currently exist, it must be added: when a screenshot is captured during a test run, the bounding box of the action's target selector should be recorded alongside the screenshot. Without this, the cursor cannot know where to land.
- **Z-index discipline.** The cursor must always render above the screenshot but below any narration tooltip or modal layer. Define the z-index scale in one place.
- **Asset family.** All cursor glyphs (base cursor, keyboard glyph, ripple ring) should live in a single `cursors/` asset folder, share the same design tokens (stroke weight, color), and be themeable via CSS custom properties.

### What needs to be captured at test-run time

Currently the test runner produces screenshots. For the cursor to know where to animate, each screenshot must be accompanied by the bounding box of the action's primary target. Suggested artifact format alongside each screenshot:

```json
{
  "step": 6,
  "screenshot": "step-06-editor.png",
  "viewport": { "width": 1280, "height": 800 },
  "target": {
    "selector": "[data-testid=\"customer-list-btn-create\"]",
    "bbox": { "x": 1120, "y": 184, "width": 140, "height": 40 }
  }
}
```

For `fill-form`, an array of bounding boxes — one per field, in declaration order — so the cursor can visit each.

This metadata capture is part of this feature. Without it, the cursor has nothing to point at. Investigating how to plumb this through the existing Playwright-based runner is the first task and may surface constraints that change other parts of this spec.

### Acceptance criteria

- All action types from `examples/create-customer.md` render correctly with appropriate cursor behavior or are correctly hidden
- Cursor animates smoothly between steps with arc motion and ease-in-out timing
- Screenshots produced by the test runner are accompanied by target bounding-box metadata
- Existing scenarios render without modification (the only new optional keys are `guide-cursor` on `custom` steps and the `guide.cursor` frontmatter block)
- Default timings produce a guide that feels neither sluggish nor rushed (manual review on `examples/create-customer.md`)
- All cursor glyphs are themeable via CSS custom properties
- `examples/create-customer.md` renders end-to-end with cursors and serves as the visual baseline

---

## Feature 2 — Keycloak Integration

### Goal

Add Keycloak as a supported authentication provider for Steppy scenarios, alongside the existing Auth0 support. The two providers must coexist: every existing scenario using `action: auth0-login` must continue to work without modification.

This is **not** a migration. Auth0 stays. Keycloak is added.

### Why

Keycloak is the de facto auth choice for European public-sector, healthcare, and EU-sovereignty-conscious clients. Supporting it unblocks a category of prospects that will not adopt Steppy if Auth0 is the only option. It also positions Steppy correctly for eventual eID/itsme support, which typically arrives as a Keycloak identity broker.

### Two integration surfaces

Auth in Steppy currently shows up in two places, and the Keycloak work must touch both:

1. **As a scenario step** — the reference scenario uses `action: auth0-login` as a step that drives the browser through the Auth0 hosted login UI. Keycloak needs an equivalent: `action: keycloak-login`.
2. **As a configured backend** — the runtime knows how to fill in credentials, handle token refresh, etc. This logic is currently Auth0-specific and must be refactored into a provider abstraction so Keycloak can plug in alongside.

### New scenario action: `keycloak-login`

A new action type, used the same way `auth0-login` is used today:

```markdown
### 3. Connexion éditeur — remplir les identifiants Keycloak
- role: editor
- action: keycloak-login
```

Like `auth0-login`, this is a high-level action that handles the entire login flow (filling username, password, MFA if configured, handling redirects). The credentials and Keycloak instance configuration come from the project config, **not** the scenario file. The scenario only declares which role is logging in — the runtime resolves which provider and which credentials to use based on project config.

### Provider abstraction (runtime refactor)

The current Auth0 login logic must be refactored into a provider-agnostic interface with two implementations.

Define an `AuthProvider` interface in the runtime:

```python
from typing import Protocol
from dataclasses import dataclass

@dataclass
class AuthCredentials:
    username: str
    password: str
    extras: dict  # totp_secret, realm hints, etc.

@dataclass
class AuthSession:
    access_token: str
    refresh_token: str | None
    expires_at: float
    provider: str
    raw: dict

class AuthProvider(Protocol):
    name: str

    async def login(self, page, credentials: AuthCredentials) -> AuthSession: ...
    async def refresh(self, session: AuthSession) -> AuthSession: ...
    async def logout(self, page, session: AuthSession) -> None: ...
    async def is_valid(self, session: AuthSession) -> bool: ...
```

`page` is the Playwright page for the relevant role. `login()` drives the browser through whatever UI flow the provider requires.

Two implementations:

- `Auth0Provider` — wraps the existing Auth0 login flow (refactor of current code, no behavior change)
- `KeycloakProvider` — new

The action handlers `auth0-login` and `keycloak-login` are thin wrappers that call into the corresponding provider.

### Project config

Both providers are configured at the project level. The scenario chooses which to use by which action it calls (`auth0-login` vs `keycloak-login`), and the role config determines which credentials to use.

```yaml
auth:
  providers:
    auth0:
      domain: example.eu.auth0.com
      client_id: ...
      audience: ...
    keycloak:
      base_url: https://auth.client.example/auth
      realm: client-realm
      client_id: steppy-tests
      token_refresh_threshold_seconds: 60
  roles:
    editor:
      provider: auth0
      username: ${env:STEPPY_EDITOR_USER}
      password: ${env:STEPPY_EDITOR_PASS}
    reviewer:
      provider: keycloak
      username: ${env:STEPPY_REVIEWER_USER}
      password: ${env:STEPPY_REVIEWER_PASS}
      extras:
        totp_secret: ${env:STEPPY_REVIEWER_TOTP}
```

A role can use either provider. The action in the scenario (`auth0-login` or `keycloak-login`) and the role's configured provider must match — if they don't, the runtime errors with a clear message before running the scenario.

This means existing scenarios using `auth0-login` keep working as long as the corresponding role's provider stays `auth0` in project config. Nothing in the scenario file needs to change for backward compatibility.

### Keycloak-specific concerns

Keycloak login flows are significantly more variable than Auth0's. The implementation must handle:

1. **Realm configuration variance.** Different clients will have different realms with different required actions. The provider must accept the realm name as configuration, not hardcode it.

2. **Required-action interrupts.** Keycloak frequently interrupts login with screens like "update password," "configure MFA," or "accept terms." For v1, the provider must **detect these and fail with a clear error message naming the required action**, rather than silently retrying or guessing. Document the failure modes so users know what to configure on the Keycloak side before running tests.

3. **Token lifetimes.** Keycloak tokens are often configured for shorter sessions in enterprise setups (5-15 minutes is common) versus Auth0's typical longer-lived tokens. The runtime must call `refresh()` proactively when `expires_at` is within `token_refresh_threshold_seconds` of expiring (default: 60 seconds), not wait for a 401.

4. **TOTP MFA.** TOTP-based MFA is common in Keycloak deployments. The provider should accept a TOTP secret in `AuthCredentials.extras["totp_secret"]` and generate the code at login time using `pyotp` or equivalent. Push-based MFA is out of scope.

5. **Identity brokering.** Keycloak can federate to upstream IdPs (Google, Microsoft, eID, itsme). For v1, only direct Keycloak realm authentication is in scope. The provider abstraction must not foreclose adding brokered flows later, but no implementation work for them in this feature.

### Auth0 refactor

The existing Auth0 login code must be refactored into `Auth0Provider` implementing the same interface. This refactor must:

- Preserve all existing behavior — no scenario should break
- Move Auth0-specific configuration into the provider's own config object under `auth.providers.auth0`
- Be covered by tests against at least one existing scenario (`examples/create-customer.md` is a good candidate) before the Keycloak provider is added on top

The refactor is the riskiest part of this feature. Land it as a self-contained PR with green tests before starting on `KeycloakProvider`.

### Secrets handling

Credentials (passwords, client secrets, TOTP secrets) must **never** appear in scenario files or project config in plaintext. They are referenced via the `${env:VAR_NAME}` syntax shown above, resolved at runtime from environment variables. Use whatever secrets resolution mechanism Steppy currently uses for Auth0 credentials — do not invent a new one.

### Acceptance criteria

- `AuthProvider` protocol exists and is the only auth interface used by the scenario runtime
- `Auth0Provider` implements the protocol; `examples/create-customer.md` and any other existing `auth0-login` scenarios pass without modification
- `KeycloakProvider` implements the protocol and can successfully run a scenario against a test Keycloak instance with:
  - Direct realm authentication (username + password)
  - TOTP-based MFA via secret in role config
  - Token refresh mid-scenario when the token nears expiration
- New `keycloak-login` action is available in scenarios and behaves symmetrically to `auth0-login`
- Clear error messages when Keycloak returns a required-action interrupt (named action, not just "login failed")
- Clear error message when a role's configured provider does not match the action used in the scenario
- Project config supports configuring both providers and per-role provider selection simultaneously
- Documentation in `docs/auth.md` explains both providers, when to use each, and how to configure them
- An example scenario `examples/auth-keycloak.md` mirrors the Auth0 portion of `create-customer.md` but uses `keycloak-login`

### Out of scope (explicitly)

- Identity brokering (federated login through Keycloak to upstream IdPs)
- Push-based MFA
- eID / itsme integration (will reuse this work later but is not part of this feature)
- Migrating any existing scenarios from Auth0 to Keycloak
- A UI for managing auth configuration (config-file-only for now)

---

## Implementation order

1. **Cursors first.** Smaller scope, no risk to existing functionality, immediate visual payoff. The biggest unknown is the bounding-box capture at test-run time — investigate that first to confirm the test runner can be extended to record it.
2. **Auth0 refactor second** (provider abstraction, no behavior change, comprehensive tests against existing scenarios).
3. **Keycloak provider third** (new code on top of the now-stable abstraction).

The two features are independent and can be worked on in parallel if the cursor work blocks on the bounding-box capture investigation.

---

## Notes for the implementer

- **Do not invent new scenario syntax.** The reference scenario `examples/create-customer.md` defines the format. The only additions in this spec are: the optional `guide-cursor` key on `custom` steps, the optional `guide.cursor` frontmatter block, and the new `keycloak-login` action. Anything else is a bug in this spec — flag it and ask before adding syntax.
- When in doubt about cursor timing or visual feel, err on the side of slower and more deliberate. Guides are watched, not skimmed.
- When in doubt about Keycloak edge cases, fail loudly with a clear error rather than silently retry. Auth bugs that fail silently are the worst kind.
- Both features should land with updated documentation in the same PR. No "docs to follow."
