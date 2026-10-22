---
title: Manage catalog items
description: An editor edits an item, categorizes it, and schedules its publish date
tags:
  - e2e
  - user-guide-editor
priority: medium
guide:
  enabled: true
---

# Exercises within/match locator disambiguation, only-in / layouts conditional
# steps, a plain native select, and pick-date. Selectors are illustrative.

### 1. Editor opens the item catalog
- role: editor
- action: navigate
- target: /items
- screenshot: editor
- guide-text:
  en: "Open 'Items' to see the catalog."
  fr: "Ouvrez 'Articles' pour voir le catalogue."

### 2. Editor edits the first item in the list
- role: editor
- action: click
- selector: button.edit
- within: [data-testid="items-list"]
- match: first
- note: "within scopes the repeated Edit button to the list; match: first picks one"
- screenshot: editor
- guide-text:
  en: "Click 'Edit' on the first item."
  fr: "Cliquez sur 'Modifier' sur le premier article."

### 3. Editor picks a category (plain native select)
- role: editor
- action: select
- selector: [data-testid="category-select"]
- value: "Featured"
- screenshot: editor
- guide-text:
  en: "Choose the item's category."
  fr: "Choisissez la catégorie de l'article."

### 4. Editor schedules the publish date
- role: editor
- action: pick-date
- selector: [data-testid="publish-date"]
- date: next-monday
- screenshot: editor
- guide-text:
  en: "Schedule the item to publish next Monday."
  fr: "Programmez la publication de l'article pour lundi prochain."

### 5. Guide-only tip about the preview (desktop layout)
- role: editor
- action: hover
- selector: [data-testid="preview-btn"]
- only-in: guide
- layouts:
  - desktop
- note: "runs only in guide mode, desktop layout only"
- screenshot: editor
- guide-text:
  en: "Hover 'Preview' to see how the item will look once published."
  fr: "Survolez 'Aperçu' pour voir le rendu de l'article une fois publié."

### 6. Editor saves the item
- role: editor
- action: click
- selector: [data-testid="save-item-btn"]
- expect: contains([data-testid="toast"], "Item saved")
- screenshot: editor
- guide-text:
  en: "Save the item."
  fr: "Enregistrez l'article."
