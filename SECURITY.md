# Security

This document describes Steppy's security-relevant behavior and how to harden a
deployment. Steppy is a **code generator** for Playwright/pytest tests and guide
artifacts; the generated tests run with the full privileges of whoever runs them.

## Credentials

App login credentials are configured under `apps.<role>.auth.credentials`.
**Do not commit plaintext secrets.** Steppy gives you two safe options:

1. **Environment-variable references** — any string in `config.yaml` (and in the
   `credentials.yaml` overlay) may use `${VAR}` or `${VAR:-default}`. References
   are resolved at load time (`steppy` commands) and again at test runtime (the
   generated `conftest.py`), so the same config works locally and in CI:

   ```yaml
   credentials:
     username: "${STEPPY_EDITOR_USERNAME}"
     password: "${STEPPY_EDITOR_PASSWORD}"
   ```

   An unset variable with no default is left as the literal `${...}` and, for the
   CLI, prints a `WARNING` to stderr.

2. **`credentials.yaml` overlay** — a gitignored file next to `config.yaml` that
   is deep-merged on top of it. Copy `credentials.yaml.example` to
   `credentials.yaml` and fill in real values. It is listed in `.gitignore`.

Steppy prints a `WARNING` when it finds a **plaintext** username/password
literal directly in `config.yaml` (i.e. not a `${VAR}` reference), nudging you
toward the options above.

> Note: credentials are read as plain strings by the generated `conftest.py`
> (no Pydantic layer), so they are not wrapped in a secret type in memory.

## Session tokens on disk

Successful auth caches Playwright storage state — **cleartext cookies and
JWTs** — under `.auth/<role>.json`, with a time-based TTL (`auth.storage_state_ttl`).
Hardening:

- `.auth/` is gitignored — never commit it.
- Steppy sets `0o600` (owner read/write only) on each saved file on POSIX
  platforms. Windows uses ACLs, where this is a no-op.
- Prefer **ephemeral CI runners** so cached state doesn't outlive the job.
- In parallel mode with per-worker databases, set `auth.storage_state_ttl: 0` to
  force per-worker re-auth rather than sharing a cached token.

## Authentication modes

`apps.<role>.auth.mode` selects how Steppy authenticates:

- **`form`** — the generic hosted-login form-fill. Waits for the username field,
  fills the role's credentials, submits, handles an optional consent screen, and
  waits for the redirect. Works against **any IdP with a fillable login form**
  (Auth0, Keycloak, …). The three selectors live under global `auth.form:` and
  can be filled at once with an IdP `preset:` (`auth0` | `keycloak`). *(The old
  name `auth0` — both the mode and the `auth0-login` step action — is a
  kept-forever deprecated alias.)*
- **`storage-state`** — reuse a Playwright storage-state file you captured out of
  band.
- **`bypass`** — inject a cookie / localStorage value directly (no login round-trip).
- **`token`** — fetch a JWT from an endpoint and inject it via
  `inject_as: localStorage | cookie | header`. For `header`, `key` is the header
  name (e.g. `Authorization`) and the endpoint should return the full header value
  when a scheme is required (e.g. `Bearer <jwt>`).

**Flows without a scriptable form** — SAML / corporate SSO with conditional
access, magic-link email, or CAPTCHA — are not automatable headlessly by design.
The supported answer is a **one-time headed login**: run `steppy auth login`,
complete the challenge in the visible browser, and Steppy caches the storage
state (subject to `auth.storage_state_ttl`). This is the same posture Playwright
itself recommends. TOTP-based MFA automation (via a shared secret) is a possible
future opt-in, not shipped today.

## `capture from.type=shell` — command execution

The `capture` action can run a shell command:

```yaml
- capture:
    store: order_id
    from:
      type: shell
      command: "echo {{some_value}}"
```

This executes `subprocess.run(..., shell=True)`. Because interpolated `{{var}}`
values can come from **page content** (`capture from.type=dom`) or **HTTP
responses**, it is a command-injection vector. Steppy mitigates this two ways:

1. **Opt-in only.** Shell capture is disabled by default and raises unless you set:

   ```yaml
   security:
     allow_shell_capture: true
   ```

2. **Quoted substitution.** Interpolated values are `shlex.quote`d before being
   spliced into the command, so page/HTTP content cannot inject shell
   metacharacters. (The static command template itself is author-controlled, so
   intended shell syntax like pipes still works.)

Only enable shell capture for scenarios you trust, and keep the command
templates under review.

## Screenshot redaction

Screenshots captured for guides can contain sensitive data (passwords, personal data).
Configure `screenshot.mask` with CSS selectors to cover matching elements with a
solid box (Playwright's native `mask=`), applied to **every** screenshot:

```yaml
screenshot:
  mask:
    - "input[name='password']"
    - ".customer-name"
  mask_color: "#000000"
```

An empty/omitted `mask` list disables redaction (default). Masking is applied at
capture time, so the raw PNGs never contain the redacted pixels.

## Results bundles

`steppy bundle create` packages a whole run into one portable file: every
screenshot, the failure logs, and (unless excluded) Playwright traces and videos.
Masking is applied at capture time, so a bundle inherits it — there are no
unredacted pixels to leak. But the aggregation itself is the thing to think about:

- **A bundle is as sensitive as the screenshots in it.** One file, easy to attach
  to a ticket or forward. Treat it like the `output/` directory it contains.
- **Traces and videos are the least redacted artifacts.** A Playwright trace holds
  full DOM snapshots and network payloads, and neither trace nor video goes through
  `screenshot.mask`. Drop them when a bundle is going anywhere you would not send
  the raw run: `steppy bundle create --no-traces --no-videos`.
- **Inspect before sharing.** `steppy bundle inspect <file>` prints the manifest —
  what is in the archive, by kind and count — without extracting anything.
- **No credential store is archived.** The bundler walks `output/` only, so
  `.auth/<role>.json` session state (which lives beside it, not inside it) and
  `credentials.yaml` are never included. The run spec in `manifest.json` records
  which flags ran, not what `${VAR}` refs resolved to.
- **Failure output is verbatim, though.** `manifest.tests[].fail_tail_b64`,
  `output/failures/<slug>/pytest.log` and traces are diagnostic text straight from
  pytest and Playwright. If a test surfaces a value in an error — a filled field in
  a call log, an API response in an assertion — it lands in the bundle unredacted.
  `screenshot.mask` covers pixels, not tracebacks.

## In-app tours

In-app tours put Steppy output into a production application: tour files the
app loads, and an overlay (`steppy-tour.js`) that runs in its pages. The
overlay is the security boundary, and it is built to live under a strict page.

### What the overlay accepts

- **Data, never code.** A tour file is JSON under a versioned contract. The
  overlay validates every file whole before rendering anything and rejects it on
  any violation — an unknown `schema` major, an unknown `advance.on` or
  `placement` value, a field over its size cap, a file without its SRI hash,
  two locales that differ only in case or `-`/`_`. Unknown keys are ignored,
  never copied.
- **Targets are test-id values**, matched as `[data-testid="…"]` (the attribute
  is configurable, `data-*` only) with `CSS.escape`, in the light DOM. A selector
  in a tour file is a contract violation, not something to evaluate.
- **Routes** are same-origin, base-relative patterns: no `//host`, no `/\host`,
  no scheme, no `.` or `..` segment — percent-encoded spellings (`%2e%2e`)
  included, since a URL parser resolves those too. The tour only ever changes
  routes through the app's `navigate` hook (default `location.assign`).
- **Text** is rendered as text with a small Markdown subset — no HTML. A link
  is live only when it is an app route (same tab, through `navigate`) or on the
  trusted guide origin (new tab, `rel="noopener noreferrer"`); anything else
  stays literal text. An app-route link is checked **after** URL resolution:
  `/.//evil.example`, `/a/..//evil.example` and `/%2e%2e//evil.example` look
  like site paths but resolve to the protocol-relative `//evil.example`, so they
  stay literal. The guide player's renderer refuses protocol-relative links too.
- The overlay needs no inline script, no inline `style` attribute and no `eval`,
  so it runs under `script-src 'self'; style-src 'self'` and under Trusted Types
  (`trusted-types lit-html`; add `'allow-duplicates'` when the app ships its own
  Lit or loads two overlay bundles).

### Loading

The bundled manifest must be same-origin. A remote manifest (a published guide
site) is fetched with `credentials: 'omit'`, `mode: 'cors'` and
`redirect: 'error'`; every body is read with a byte cap and refused when it is
HTML.

When the bundled manifest and another one — a remote guide site, or a
same-origin proxy — are for the same build, the other manifest only says where
the files are. The bundled manifest picks the tour, the locale and the SRI hash,
and the file is fetched with that hash as its `integrity`: a tampered copy is
rejected by the browser and the bundled copy is used, and a tour, a locale or a
case variant that only the other manifest lists is never fetched. It cannot
widen what the bundled one trusts either: the guide origin and the test-id
attribute come from the host or the bundled manifest first.

Without a bundled manifest of the same build (remote-only mode, or a host
`build-id` for another build), the remote manifest's own hashes are used. They
detect a file changed after its manifest, not a compromised guide host: in that
mode the guide host is trusted for tour text and links on the guide origin.
Bundle the manifest when that host is less trusted than the app.

### Auth-walled guides

Because remote requests carry no credentials, a guide behind a login cannot
serve tour files to the overlay directly. Ship them bundled (recommended), serve
them through a same-origin proxy that authenticates on its own, or supply them
through the host's `source` hook from your own authenticated client. A proxy's
files are pinned by the bundled manifest like a remote's, so it must relay the
bytes unchanged; in remote-only mode behind a proxy, set the host's
`guide-origin` rather than trusting the proxied manifest's.

### Interaction and storage

The overlay never clicks, types or submits for the user, and lets every click
through to the app. It stores one `sessionStorage` key (`steppy:tour`: the
running tour, step, locale and entry params, for 30 minutes; an expired or
malformed record is removed when read) and sends nothing anywhere; its events
stay in the page.

### Writing into the app

`steppy generate` and `steppy tours` write into an `app-assets` folder of
*your app's* repository. A folder is only ever written to when it is not the
project root, a parent of it, a folder Steppy owns for something else, or a
project-looking folder; a foreign `tours.manifest.json` stops the run; and a
file is only overwritten or deleted when it is itself a Steppy tour file (its
JSON `kind`). Any refusal (`target-path`) happens before the first byte is
written.

Generated tests set `globalThis.__STEPPY_TOURS_DISABLED__` in every browser
context when tours are enabled, so a tour the app starts on its own cannot cover
what a test clicks.

### Tour text is public

On a public guide site, tour files are as public as the guide — and the static
build's `_headers` sends `Access-Control-Allow-Origin: *` for them on private
builds too, so any page can read them from a host it can reach. Keep
confidential data out of `guide-text` and `tour-text`; the compiler warns when
step text repeats a value the test types (`fixture-leak`).

## Reporting a vulnerability

Please **do not open a public issue** for a suspected vulnerability. Report it
privately through GitHub's private vulnerability reporting: the repository's
**Security** tab → **Report a vulnerability**. That opens a private advisory
visible only to you and the maintainers.

Include what you can of: the affected command or config block, the Steppy version
(`pip show steppy`), a minimal reproduction, and the impact you expect.

What to expect:

- an acknowledgement within 7 days;
- a fix, or an explanation of why the behaviour is intended, as soon as the
  issue is understood — fixes land on `main` and ship in the next tagged release;
- credit in the release notes and the published advisory, unless you prefer
  otherwise.

Only the latest release is supported with security fixes.

**In scope:** Steppy's own code, including the code it generates (`conftest.py`,
test files), the guide/report sites it builds, the bundle upload client, and the
in-app tour overlay (`steppy-tour.js`) with the tour files Steppy generates.
**Out of scope:** the application under test, and the documented trade-offs above
(storage state on disk, shell capture once enabled, artifacts being unredacted
diagnostic text); for in-app tours, your application's CSP, the guide host's CORS
and caching, and any proxy or ticket service you build for auth-walled guides.
