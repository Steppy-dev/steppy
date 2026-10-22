---
title: Edit workspace settings
description: An admin configures notification settings, a schedule, and confirms the change
tags:
  - e2e
  - user-guide-admin
priority: medium
guide:
  enabled: true
---

# Exercises the component-driver actions (expand / toggle / fill-date / pick-time /
# confirm) and the count(>=N) assertion comparator. Selectors are illustrative —
# retarget them to your app.

### 1. Admin opens workspace settings
- role: admin
- action: navigate
- target: /settings
- screenshot: admin
- guide-text:
  en: "Open 'Settings' to configure the workspace."
  fr: "Ouvrez 'Paramètres' pour configurer l'espace de travail."

### 2. Admin expands the notifications section
- role: admin
- action: expand
- selector: [data-testid="notifications-fieldset"]
- screenshot: admin
- guide-text:
  en: "Expand 'Notifications' to reveal the options."
  fr: "Développez 'Notifications' pour afficher les options."

### 3. Admin enables email notifications
- role: admin
- action: toggle
- selector: [data-testid="email-notifications-switch"]
- state: on
- screenshot: admin
- guide-text:
  en: "Turn on email notifications."
  fr: "Activez les notifications par e-mail."

### 4. Admin sets a review reminder date
- role: admin
- action: fill-date
- selector: [data-testid="reminder-date"]
- date: +30d
- format: "%d/%m/%Y"
- screenshot: admin
- guide-text:
  en: "Set the review reminder 30 days from today."
  fr: "Définissez le rappel de revue à 30 jours."

### 5. Admin sets the daily digest time
- role: admin
- action: pick-time
- selector: [data-testid="digest-time"]
- time: "09:00"
- screenshot: admin
- guide-text:
  en: "Pick 09:00 for the daily digest."
  fr: "Choisissez 09:00 pour le résumé quotidien."

### 6. Admin saves the settings
- role: admin
- action: click
- selector: [data-testid="save-settings-btn"]
- screenshot: admin
- guide-text:
  en: "Save your changes."
  fr: "Enregistrez vos modifications."

### 7. Admin confirms the change
- role: admin
- action: confirm
- screenshot: admin
- guide-text:
  en: "Confirm to apply the new settings."
  fr: "Confirmez pour appliquer les nouveaux paramètres."

### 8. The active settings are listed
- role: admin
- action: assert
- expect: count([data-testid^="setting-row-"], >=1)
