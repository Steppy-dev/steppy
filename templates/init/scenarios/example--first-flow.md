---
title: Create a project
description: A starter scenario — an admin creates a new project
tags:
  - e2e
  - user-guide-admin
priority: high
guide:
  enabled: true
---

# This is a starter scenario written by `steppy init`. Each `###` heading is a
# step; each `- key: value` bullet is a field. Replace the selectors, URLs, and
# text with your app's. Run `steppy validate` to check it, then `steppy generate`
# to produce the Playwright test. See STEPPY-REFERENCE.md for the full grammar.

### 1. Admin opens the projects list
- role: admin
- action: navigate
- target: /projects
- screenshot: admin
- guide-text:
  en: "From the main menu, open 'Projects' to see all projects."
  fr: "Depuis le menu principal, ouvrez 'Projets' pour voir tous les projets."

### 2. Admin starts a new project
- role: admin
- action: click
- selector: [data-testid="new-project-btn"]
- screenshot: admin
- guide-text:
  en: "Click 'New project' in the top-right corner."
  fr: "Cliquez sur 'Nouveau projet' en haut à droite."

### 3. Admin names the project
- role: admin
- action: fill
- selector: [data-testid="project-name"]
- value: "Website redesign"
- screenshot: admin
- guide-text:
  en: "Give the project a clear, descriptive name."
  fr: "Donnez au projet un nom clair et descriptif."

### 4. Admin saves the project
- role: admin
- action: click
- selector: [data-testid="save-project-btn"]
- expect: contains([data-testid="toast"], "Project created")
- screenshot: admin
- guide-text:
  en: "Save to create the project — it now appears in the list."
  fr: "Enregistrez pour créer le projet — il apparaît maintenant dans la liste."
