---
title: Send an invoice
description: An editor opens a draft invoice and e-mails it to the customer.
tags: [tour-demo]
layouts: [desktop, mobile]
tour:
  id: send-invoice
  title: {en: Send an invoice, fr: Envoyer une facture}
  entry: /invoices/:invoiceId
---

### 1. Open a draft invoice
- role: editor
- action: navigate
- target: /invoices/1001

### 2. Fill in the e-mail
- role: editor
- action: fill-form
- fields:
    '[data-testid="send-to"]': "billing@example.com"
    '[data-testid="send-message"]': "Thanks for your business"
- guide-text:
    en:
      title: Recipient and message
      text: Enter the recipient's e-mail address and a short message.
    fr:
      title: Destinataire et message
      text: Saisissez l'adresse e-mail du destinataire et un court message.

### 3. Send it
- role: editor
- action: click
- selector: '[data-testid="send-invoice-btn"]'
- expect: contains([data-testid="invoice-status"], "Sent")
- guide-text:
    en: "Click **Send**."
    fr: "Cliquez sur **Envoyer**."

### 4. The invoice is sent
- role: editor
- action: assert
- expect: contains([data-testid="invoice-status"], "Sent")
- guide-text:
    en:
      title: Sent
      text: The status changes to **Sent**.
    fr:
      title: Envoyée
      text: Le statut passe à **Envoyée**.
