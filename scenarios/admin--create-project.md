---
title: Create a project
description: An admin creates a new project and assigns its owner
tags:
  - e2e
  - user-guide-admin
priority: high
guide:
  enabled: true
---

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

### 4. Admin picks an owner from the dropdown
- role: admin
- action: select-option
- selector: [data-testid="owner-select"]
- option: first
- screenshot: admin
- guide-text:
  en: "Choose the team member who will own this project."
  fr: "Choisissez le membre de l'équipe responsable de ce projet."

### 5. Admin saves the project
- role: admin
- action: click
- selector: [data-testid="save-project-btn"]
- expect: contains([data-testid="toast"], "Project created")
- screenshot: admin
- guide-text:
  en: "Save to create the project — it now appears in the list."
  fr: "Enregistrez pour créer le projet — il apparaît maintenant dans la liste."
