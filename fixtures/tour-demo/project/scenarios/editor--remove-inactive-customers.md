---
title: Remove inactive customers
description: An editor opens the customer list, from the header on a computer or from the menu on a phone, and removes the customers with no recent invoice.
tags: [tour-demo]
layouts: [desktop, mobile]
tour:
  id: remove-inactive-customers
  title: {en: Remove inactive customers, fr: Retirer les clients inactifs}
---

### 1. Open the invoice list
- role: editor
- action: navigate
- target: /invoices

### 2. Open the customer list from the header
- role: editor
- action: click
- selector: '[data-testid="nav-customers"]'
- layouts: [desktop]
- guide-text:
    en:
      title: Customers
      text: Open **Customers** in the header.
    fr:
      title: Clients
      text: Ouvrez **Clients** dans l'en-tête.

### 3. Open the menu
- role: editor
- action: click
- selector: '[data-testid="menu-btn"]'
- layouts: [mobile]
- guide-text:
    en:
      title: Menu
      text: Open the **Menu**.
    fr:
      title: Menu
      text: Ouvrez le **Menu**.

### 4. Open the customer list from the menu
- role: editor
- action: click
- selector: '[data-testid="drawer-customers"]'
- layouts: [mobile]
- guide-text:
    en:
      title: Customers
      text: Choose **Customers**.
    fr:
      title: Clients
      text: Choisissez **Clients**.

### 5. Remove the inactive customers
- role: editor
- action: click
- selector: '[data-testid="remove-inactive-btn"]'
- guide-text:
    en:
      title: Remove inactive customers
      text: At the end of the list, click **Remove inactive customers**.
    fr:
      title: Retirer les clients inactifs
      text: En bas de la liste, cliquez sur **Retirer les clients inactifs**.

### 6. Confirm
- role: editor
- action: confirm
- tour: keep
- guide-text:
    en:
      title: Confirm
      text: Click **Yes** to remove them.
    fr:
      title: Confirmer
      text: Cliquez sur **Oui** pour les retirer.

### 7. The inactive customers are removed
- role: editor
- action: assert
- expect: toast("Inactive customers removed.")
- guide-text:
    en:
      title: Done
      text: The customers you still bill stay on the list.
    fr:
      title: Terminé
      text: Les clients que vous facturez encore restent dans la liste.
