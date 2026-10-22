---
title: Task approval flow
description: An editor submits a task for review and an admin approves it
tags:
  - e2e
  - user-guide-editor
  - user-guide-admin
priority: critical
guide:
  enabled: true
---

### 1. Editor opens their tasks
- role: editor
- action: navigate
- target: /tasks
- screenshot: editor
- guide-text:
  en: "Open 'Tasks' to see the work assigned to you."
  fr: "Ouvrez 'Tâches' pour voir le travail qui vous est assigné."

### 2. Editor opens a task
- role: editor
- action: click
- selector: [data-testid="task-row-0"]
- screenshot: editor
- guide-text:
  en: "Click a task to open its detail view."
  fr: "Cliquez sur une tâche pour ouvrir sa vue détaillée."

### 3. Editor submits the task for review
- role: editor
- action: click
- selector: [data-testid="submit-for-review-btn"]
- expect: contains([data-testid="toast"], "Submitted for review")
- screenshot: editor
- guide-text:
  en: "Submit the task — it now waits for an admin's approval."
  fr: "Soumettez la tâche — elle attend maintenant l'approbation d'un admin."

### 4. Wait for the review queue to update
- action: wait
- duration: 2s
- note: "Give the review queue a moment to reflect the new submission"

### 5. Admin opens the review queue
- role: admin
- action: navigate
- target: /reviews
- expect: contains([data-testid="reviews"], "Website redesign")
- screenshot: admin
- guide-text:
  en: "The submitted task appears in your review queue."
  fr: "La tâche soumise apparaît dans votre file de revue."

### 6. Admin approves the task
- role: admin
- action: click
- selector: [data-testid="approve-btn"]
- expect: contains([data-testid="toast"], "Approved")
- screenshot: admin
- guide-text:
  en: "Approve the task to mark it complete."
  fr: "Approuvez la tâche pour la marquer comme terminée."

### 7. Editor sees the approved status
- role: editor
- action: navigate
- target: /tasks
- expect: contains([data-testid="task-row-0"], "Approved")
- screenshot: editor
- guide-text:
  en: "Your task status updates to 'Approved'."
  fr: "Le statut de votre tâche passe à 'Approuvé'."
