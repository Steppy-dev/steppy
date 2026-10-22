---
title: Create an invoice
description: An editor opens the invoice list, creates an invoice in the dialog and lands on its detail page.
tags: [tour-demo]
layouts: [desktop, mobile]
guide:
  enabled: true
tour:
  id: create-invoice
  title: {en: Create an invoice, fr: Créer une facture}
  guide_slug: create-invoice
---

### 1. Open the invoice list
- role: editor
- action: navigate
- target: /invoices

### 2. Wait for the list
- role: editor
- action: wait-for
- selector: '[data-testid="invoice-list"]'

### 3. Start a new invoice
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
- tour-placement: bottom
- guide-text:
    en:
      title: New invoice
      text: Click **New invoice** to open the invoice form.
    fr:
      title: Nouvelle facture
      text: Cliquez sur **Nouvelle facture** pour ouvrir le formulaire.

### 4. Name the invoice
- role: editor
- action: fill
- selector: '[data-testid="invoice-title"]'
- value: "Quarterly maintenance"
- guide-text:
    en: "Type a title, for example Quarterly maintenance."
    fr: "Saisissez un titre, par exemple Quarterly maintenance."
- tour-text:
    en:
      title: Title
      text: Give the invoice a short title your customer will recognise.
    fr:
      title: Titre
      text: Donnez à la facture un titre court que votre client reconnaîtra.

### 5. Pick the customer
- role: editor
- action: select-option
- selector: '[data-testid="customer-select"]'
- option: 'text:"Acme Corp"'
- guide-text:
    en:
      title: Customer
      text: Choose the customer to bill.
    fr:
      title: Client
      text: Choisissez le client à facturer.

### 6. Save the invoice
- role: editor
- action: click
- selector: '[data-testid="save-invoice-btn"]'
- expect: visible([data-testid="invoice-detail"])
- guide-text:
    en:
      title: Save
      text: Click **Save**. The invoice opens on its own page.
    fr:
      title: Enregistrer
      text: Cliquez sur **Enregistrer**. La facture s'ouvre sur sa propre page.

### 7. The invoice is created
- role: editor
- action: assert
- expect: visible([data-testid="invoice-detail"])
- guide-text:
    en:
      title: Done
      text: Your invoice is saved as a draft. You can send it from this page.
    fr:
      title: Terminé
      text: Votre facture est enregistrée en brouillon. Vous pouvez l'envoyer depuis cette page.
