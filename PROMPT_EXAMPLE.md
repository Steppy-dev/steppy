# Steppy — Example Prompts for Claude Code

Copy and adapt these prompts when you want Claude Code to generate Steppy scenarios for your app.

---

## 1. Tests only (single app)

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format.

Then explore the app's codebase:
- Identify all routes/pages and their paths
- Find data-testid attributes already in the code
- Understand the main user flows (what can a user do?)
- Note any forms, buttons, tables, modals, toasts, and their selectors

Then:

1. Create a `config.yaml` at the project root following the Steppy format. The app runs on http://localhost:3000. Auth mode is [bypass|auth0|token] — adapt credentials as needed.

2. Create a `scenarios/` directory and write scenario .md files for the following flows:
   - [Describe flow 1: e.g., "User creates a new project"]
   - [Describe flow 2: e.g., "User invites a team member"]
   - [Describe flow 3: e.g., "Admin views analytics dashboard"]

Use real selectors from the codebase (prefer data-testid). If a component lacks a data-testid, add one.

Each scenario should:
- Have a clear title and appropriate tags
- Use navigate to reach the page, then interact with real selectors
- Include expect assertions to verify outcomes (toasts, visibility, text, URL changes)
- Be self-contained (each scenario starts from a clean navigation)

3. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate
```

---

## 2. Tests only (multi-app / multi-tenant)

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format.

Then explore the codebase. There are multiple apps:
- App A (tenant/admin): runs on http://localhost:3000 — explore its routes, selectors, and flows
- App B (tenant/client): runs on http://localhost:3001 — explore its routes, selectors, and flows

Then:

1. Create a `config.yaml` with both apps defined:
   - app-a at http://localhost:3000 with auth mode [bypass|auth0]
   - app-b at http://localhost:3001 with auth mode [bypass|auth0]

2. Create scenarios that test cross-app workflows:
   - [e.g., "Admin creates an order on app-a, client sees it on app-b"]
   - [e.g., "Client submits a request on app-b, admin approves it on app-a, client sees approval on app-b"]

Use role switching between steps (role: app-a / role: app-b). Add `wait` steps where async propagation is needed between the two apps.

3. Also create single-app scenarios for each app's standalone flows.

4. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate
```

---

## 3. Tests + step-by-step user guides (single app)

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format, including the guide-text and screenshot features.

Then explore the app's codebase:
- Identify all routes/pages and their paths
- Find data-testid attributes already in the code
- Understand the main user flows
- Note any forms, buttons, tables, modals, toasts, and their selectors

Then:

1. Create a `config.yaml` at the project root. The app runs on http://localhost:3000. Auth mode is [bypass|auth0|token]. Set the locales to [fr, en] (adapt to your needs).

2. Create a `scenarios/` directory and write scenario .md files for the following flows:
   - [Describe flow 1: e.g., "User creates a new project"]
   - [Describe flow 2: e.g., "User invites a team member"]

Each scenario should:
- Have a clear title and tags including `user-guide-[role]` (e.g., `user-guide-myapp`)
- Use real selectors from the codebase (prefer data-testid). If a component lacks a data-testid, add one.
- Include expect assertions to verify outcomes
- Be self-contained

For guide generation, each step that should appear in the user guide must have:
- `screenshot: [role]` (e.g., `screenshot: myapp`) to capture the screen at that step
- `guide-text:` with a sub-key per locale containing a clear, end-user-friendly instruction

Example step with guide-text:

### 3. User clicks "New Project"
- role: myapp
- action: click
- selector: [data-testid="new-project-btn"]
- screenshot: myapp
- guide-text:
    fr: "Cliquez sur le bouton 'Nouveau Projet' en haut a droite de la page."
    en: "Click the 'New Project' button at the top right of the page."

Write guide-text as if explaining to a non-technical end user. Describe what they see and what to click, not technical details.

Not every step needs guide-text — skip it for waits, technical assertions, or intermediate steps that don't add clarity to the user guide.

3. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate

4. Run tests with screenshots enabled to capture the images:
   steppy run --screenshots

5. Generate the guides:
   steppy guide --tag user-guide-myapp --locale fr
   steppy guide --tag user-guide-myapp --locale en

Guides are output as Markdown and HTML in output/guides/[role]/[locale]/.
```

---

## 4. Tests + step-by-step user guides (multi-app)

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format, including the guide-text and screenshot features.

Then explore the codebase. There are multiple apps:
- App A (admin): runs on http://localhost:3000 — explore its routes, selectors, and flows
- App B (client): runs on http://localhost:3001 — explore its routes, selectors, and flows

Then:

1. Create a `config.yaml` with both apps defined:
   - app-a at http://localhost:3000 with auth mode [bypass|auth0]
   - app-b at http://localhost:3001 with auth mode [bypass|auth0]
   Set the locales to [fr, en] (adapt to your needs).

2. Create scenarios with BOTH test coverage AND user guide content. Each scenario that should produce a user guide needs:
   - A tag `user-guide-[role]` for each role that gets its own guide (e.g., `user-guide-app-a` and/or `user-guide-app-b`)
   - `screenshot: [role]` on steps that should appear in that role's guide (use `screenshot: all` if the step should appear in all guides)
   - `guide-text:` with per-locale instructions written for end users

Example cross-app scenario with guides for both roles:

---
title: Admin creates order, client receives it
tags: [e2e, user-guide-app-a, user-guide-app-b]
priority: high
---

### 1. Admin navigates to orders
- role: app-a
- action: navigate
- target: /orders
- screenshot: app-a
- guide-text:
    fr: "Rendez-vous sur la page 'Commandes' depuis le menu principal."
    en: "Navigate to the 'Orders' page from the main menu."

### 2. Admin clicks new order
- role: app-a
- action: click
- selector: [data-testid="create-order-btn"]
- screenshot: app-a
- guide-text:
    fr: "Cliquez sur 'Nouvelle commande'."
    en: "Click 'New order'."

### 3. Admin fills order form
- role: app-a
- action: fill-form
- fields:
    [data-testid="product"]: "Widget X"
    [data-testid="quantity"]: "10"
- screenshot: app-a
- guide-text:
    fr: "Remplissez le formulaire avec le produit et la quantite souhaitee."
    en: "Fill in the form with the product and desired quantity."

### 4. Admin submits
- role: app-a
- action: click
- selector: [data-testid="submit-btn"]
- expect: toast("Order created")
- screenshot: app-a
- guide-text:
    fr: "Cliquez sur 'Valider'. Un message de confirmation apparait."
    en: "Click 'Submit'. A confirmation message appears."

### 5. Wait for sync
- action: wait
- duration: 3s

### 6. Client sees new order
- role: app-b
- action: navigate
- target: /orders
- expect: row-contains("Widget X")
- screenshot: app-b
- guide-text:
    fr: "La commande apparait dans votre liste de commandes."
    en: "The order appears in your orders list."

Note how:
- Steps 1-4 have `screenshot: app-a` → they appear in the app-a guide
- Step 6 has `screenshot: app-b` → it appears in the app-b guide
- Step 5 (wait) has no screenshot or guide-text → it's test-only
- The scenario has BOTH `user-guide-app-a` and `user-guide-app-b` tags

3. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate

4. Run tests with screenshots:
   steppy run --screenshots

5. Generate separate guides per role and locale:
   steppy guide --tag user-guide-app-a --locale fr
   steppy guide --tag user-guide-app-a --locale en
   steppy guide --tag user-guide-app-b --locale fr
   steppy guide --tag user-guide-app-b --locale en

Each role gets its own guide showing only the steps relevant to them, with their own screenshots and guide-text.
```

---

## 5. User guides only (single app)

Use this when you already have a working app and just want to produce step-by-step guides — no test assertions needed.

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format, focusing on guide-text and screenshot features.

Then explore the app's codebase:
- Identify the main user-facing flows
- Find data-testid attributes and selectors for buttons, forms, tables, modals
- Understand the page routes

Then:

1. Create a `config.yaml` at the project root. The app runs on http://localhost:3000. Auth mode is [bypass|auth0|token]. Set the locales to [fr, en] (adapt to your needs).

2. Create a `scenarios/` directory and write scenario .md files for the following guides:
   - [Describe guide 1: e.g., "How to create a new project"]
   - [Describe guide 2: e.g., "How to manage team members"]

The goal is user documentation, not test coverage. Each scenario should:
- Have tags including `user-guide-[role]` (e.g., `user-guide-myapp`)
- Walk through the flow step by step as a user would
- Have `screenshot: [role]` on every step you want in the guide
- Have `guide-text:` on every screenshot step with clear, end-user instructions per locale
- Assertions (expect) are optional — only add them if they help verify the guide stays accurate

Write guide-text as if explaining to a non-technical end user. Describe what they see on screen, what to click, what to type. Skip jargon.

3. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate

4. Run with screenshots to capture the images:
   steppy run --screenshots

5. Generate the guides:
   steppy guide --tag user-guide-myapp --locale fr
   steppy guide --tag user-guide-myapp --locale en

Guides are output as Markdown and HTML in output/guides/[role]/[locale]/.
```

---

## 6. User guides only (multi-app, guide for ONE role)

Use this when the guide is for one role (e.g., editor), but the scenario requires actions from another role (e.g., reviewer) to set up the right state. The other role's steps are executed but excluded from the guide.

The key rule: only add `screenshot` and `guide-text` to steps belonging to the role you want in the guide. Steps from the other role run during the test but stay invisible in the generated guide.

```
Read STEPPY-REFERENCE.md to understand the Steppy scenario format, focusing on guide-text and screenshot features.

Then explore the codebase. There are multiple apps:
- Editor app: runs on http://localhost:3000
- Reviewer app: runs on http://localhost:3001

Then:

1. Create a `config.yaml` with both apps:
   - editor at http://localhost:3000 with auth mode [bypass|auth0]
   - reviewer at http://localhost:3001 with auth mode [bypass|auth0]
   Set the locales to [fr, en].

2. Create scenarios that produce a guide for ONE role, while using the other role as a "helper" to set up state. Tag them `user-guide-editor` (or whichever role the guide is for).

Key principle: steps from the helper role (reviewer) have NO screenshot and NO guide-text. They are executed to put the system in the right state, but they don't appear in the generated guide.

Example — editor guide for "Order lifecycle":

---
title: Suivi de commande (Éditeur)
tags: [user-guide-editor]
priority: high
---

### 1. Editor creates an order
- role: editor
- action: navigate
- target: /orders/new
- screenshot: editor
- guide-text:
    fr: "Rendez-vous sur la page 'Nouvelle commande'."
    en: "Navigate to the 'New order' page."

### 2. Editor fills order form
- role: editor
- action: fill-form
- fields:
    [data-testid="product"]: "Standing desk"
    [data-testid="quantity"]: "2"
- screenshot: editor
- guide-text:
    fr: "Remplissez le produit et la quantite souhaitee."
    en: "Fill in the product and desired quantity."

### 3. Editor submits
- role: editor
- action: click
- selector: [data-testid="submit-btn"]
- screenshot: editor
- guide-text:
    fr: "Cliquez sur 'Envoyer'. La commande passe en statut 'En attente'."
    en: "Click 'Submit'. The order status changes to 'Pending'."

### 4. Reviewer accepts the order (helper step — NOT in guide)
- role: reviewer
- action: navigate
- target: /orders

### 5. Reviewer clicks accept (helper step — NOT in guide)
- role: reviewer
- action: click
- selector: [data-testid="accept-btn"]

### 6. Wait for sync
- action: wait
- duration: 3s

### 7. Editor sees accepted status
- role: editor
- action: navigate
- target: /orders
- screenshot: editor
- guide-text:
    fr: "Retournez sur la liste des commandes. Votre commande est maintenant 'Acceptee'."
    en: "Go back to the orders list. Your order is now 'Accepted'."
- expect: text([data-testid="order-status"], "Accepted")

Note how:
- Steps 1-3, 7 have `screenshot: editor` + `guide-text` → they appear in the editor guide
- Steps 4-5 (reviewer) have NO screenshot, NO guide-text → they run during the test but are invisible in the guide
- Step 6 (wait) is purely technical → also invisible
- The tag is ONLY `user-guide-editor` (not user-guide-reviewer)

The generated editor guide will read as a clean 4-step flow:
  1. Go to new order page
  2. Fill in the form
  3. Submit
  4. See the accepted status

The reviewer actions happen behind the scenes to make step 4 possible.

3. Install steppy and validate:
   pip install -e /path/to/Steppy
   steppy validate
   steppy generate

4. Run with screenshots:
   steppy run --screenshots

5. Generate the guide:
   steppy guide --tag user-guide-editor --locale fr
   steppy guide --tag user-guide-editor --locale en
```

---

## Tips

- Replace `[bracketed text]` with your actual details
- The more specific you are about which flows to test, the better the output
- If your app doesn't use data-testid yet, ask Claude Code to add them as part of the task
- Point Claude Code to the right directories if your project is a monorepo
- Guide-text should be written for end users, not developers — describe what they see on screen
- Not every step needs guide-text. Skip waits, technical assertions, and intermediate navigation
- Use `screenshot: all` when a step is relevant to every role's guide
