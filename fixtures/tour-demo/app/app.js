// Invoice demo for the in-app tour overlay. Framework-free on purpose: it
// reproduces the shapes real apps put in a tour's way — a History-API router,
// a dialog appended to <body> behind a z-index 1100 mask with a focus trap, a
// PrimeNG-style dropdown that sets its value programmatically (no `change`
// event), and on a phone a navigation drawer that slides in over a mask, a
// long page and a centred confirmation dialog. Text is only ever written with
// textContent.
//
// /demo-config.json (optional, served by serve.py) tunes it for tests:
//   manifest, fallbackManifest, buildId, targetTimeout  -> the tour host
//   testIdOverrides {"name": "renamed"}                   -> simulate a renamed test-id
//   saveDelayMs (150)                                     -> delay before the detail page
//   focusTrap "sentinel" | "reclaim"                      -> how the dialog keeps focus
//   autoStart "<tour id>"                                -> start a tour on every page load,
//                                                           as some apps do for new users

const TOUR_EVENTS = ['tour-start', 'tour-step', 'tour-complete', 'tour-dismiss', 'tour-offpath', 'tour-error'];
const CUSTOMERS = ['Acme Corp', 'Globex', 'Initech'];
const STORE = 'demo:invoices';
const CUSTOMER_STORE = 'demo:customers';

const TEXT = {
  en: {
    invoices: 'Invoices',
    tourCreate: 'Tour: create an invoice',
    tourSend: 'Tour: send an invoice',
    language: 'Language',
    newInvoice: 'New invoice',
    title: 'Title',
    customer: 'Customer',
    choose: 'Choose a customer',
    save: 'Save',
    cancel: 'Cancel',
    missing: 'Enter a title and choose a customer.',
    open: 'Open',
    status: 'Status',
    Draft: 'Draft',
    Sent: 'Sent',
    sendTo: 'Recipient e-mail',
    message: 'Message',
    send: 'Send',
    back: 'Back to the list',
    footer: 'End of the invoice list.',
    notFound: 'This invoice does not exist.',
    customers: 'Customers',
    menu: 'Menu',
    closeMenu: 'Close the menu',
    tourCustomers: 'Tour: remove inactive customers',
    lastInvoice: 'Last invoice',
    active: 'Active',
    inactive: 'Inactive',
    removeHint: 'A customer with no invoice in the last twelve months is inactive.',
    removeInactive: 'Remove inactive customers',
    confirmText: 'Remove every customer with no invoice in the last twelve months?',
    yes: 'Yes',
    no: 'No',
    removed: 'Inactive customers removed.',
    customersFooter: 'End of the customer list.',
  },
  fr: {
    invoices: 'Factures',
    tourCreate: 'Visite : créer une facture',
    tourSend: 'Visite : envoyer une facture',
    language: 'Langue',
    newInvoice: 'Nouvelle facture',
    title: 'Titre',
    customer: 'Client',
    choose: 'Choisissez un client',
    save: 'Enregistrer',
    cancel: 'Annuler',
    missing: 'Saisissez un titre et choisissez un client.',
    open: 'Ouvrir',
    status: 'Statut',
    Draft: 'Brouillon',
    Sent: 'Envoyée',
    sendTo: 'E-mail du destinataire',
    message: 'Message',
    send: 'Envoyer',
    back: 'Retour à la liste',
    footer: 'Fin de la liste des factures.',
    notFound: "Cette facture n'existe pas.",
    customers: 'Clients',
    menu: 'Menu',
    closeMenu: 'Fermer le menu',
    tourCustomers: 'Visite : retirer les clients inactifs',
    lastInvoice: 'Dernière facture',
    active: 'Actif',
    inactive: 'Inactif',
    removeHint: 'Un client sans facture depuis douze mois est inactif.',
    removeInactive: 'Retirer les clients inactifs',
    confirmText: 'Retirer tous les clients sans facture depuis douze mois ?',
    yes: 'Oui',
    no: 'Non',
    removed: 'Clients inactifs retirés.',
    customersFooter: 'Fin de la liste des clients.',
  },
};

let cfg = {};
let lang = 'en';
const host = document.querySelector('steppy-tour-host');
const view = document.getElementById('view');
const t = (key) => TEXT[lang][key] ?? key;
const tid = (name) => (cfg.testIdOverrides && cfg.testIdOverrides[name]) || name;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'testid') node.setAttribute('data-testid', tid(v));
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== undefined) node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children) node.append(child);
  return node;
}

// ── Data ─────────────────────────────────────────────────────────────────

function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE));
    if (Array.isArray(saved)) return saved;
  } catch {
    // Fall through to the seed data.
  }
  return [
    { id: '1001', title: 'Website hosting', customer: 'Globex', status: 'Draft' },
    { id: '1000', title: 'Annual support', customer: 'Initech', status: 'Sent' },
  ];
}
let invoices = load();
function persist() {
  try {
    sessionStorage.setItem(STORE, JSON.stringify(invoices));
  } catch {
    // The demo still works for this page.
  }
}

function loadCustomers() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CUSTOMER_STORE));
    if (Array.isArray(saved)) return saved;
  } catch {
    // Fall through to the seed data.
  }
  return [
    { name: 'Acme Corp', lastInvoice: '2026-09', active: true },
    { name: 'Globex', lastInvoice: '2026-10', active: true },
    { name: 'Initech', lastInvoice: '2024-11', active: false },
  ];
}
let customers = loadCustomers();

// ── Router ───────────────────────────────────────────────────────────────

function go(path, replace = false) {
  history[replace ? 'replaceState' : 'pushState'](null, '', path);
  render();
}

function render() {
  if (location.pathname === '/' || location.pathname === '') history.replaceState(null, '', '/invoices');
  const detail = /^\/invoices\/([^/]+)\/?$/.exec(location.pathname);
  const page = detail
    ? detailPage(decodeURIComponent(detail[1]))
    : /^\/customers\/?$/.test(location.pathname)
      ? customersPage()
      : listPage();
  view.replaceChildren(page);
}

window.addEventListener('popstate', render);
document.addEventListener('click', (e) => {
  const link = e.target.closest && e.target.closest('a[data-link]');
  if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  go(link.getAttribute('href'));
});

// ── Pages ────────────────────────────────────────────────────────────────

function listPage() {
  const rows = invoices.map((inv) =>
    el(
      'tr',
      { testid: 'invoice-row' },
      el('td', {}, inv.id),
      el('td', {}, inv.title),
      el('td', {}, inv.customer),
      el('td', {}, t(inv.status)),
      el('td', {}, el('a', { href: `/invoices/${inv.id}`, 'data-link': true, testid: 'invoice-open' }, t('open'))),
    ),
  );
  return el(
    'section',
    { class: 'page' },
    el('div', { class: 'toolbar' }, el('h1', {}, t('invoices')), el('button', { type: 'button', class: 'primary', testid: 'new-invoice-btn', onclick: openDialog }, t('newInvoice'))),
    el('table', { testid: 'invoice-list' }, el('tbody', {}, ...rows)),
    el('div', { class: 'tall' }),
    el('p', { testid: 'invoices-footer' }, t('footer')),
  );
}

function detailPage(id) {
  const inv = invoices.find((i) => i.id === id);
  if (!inv) return el('section', { class: 'page' }, el('p', {}, t('notFound')));
  const status = el('span', { testid: 'invoice-status', class: `status ${inv.status}` }, t(inv.status));
  const sendTo = el('input', { type: 'email', id: 'send-to', testid: 'send-to', autocomplete: 'off' });
  const message = el('textarea', { id: 'send-message', testid: 'send-message', rows: '3' });
  const send = () => {
    inv.status = 'Sent';
    persist();
    status.className = 'status Sent';
    status.textContent = t('Sent');
  };
  return el(
    'section',
    { class: 'page', testid: 'invoice-detail' },
    el('h1', { testid: 'invoice-detail-title' }, `${inv.id} — ${inv.title}`),
    el('p', {}, `${t('customer')}: ${inv.customer} · ${t('status')}: `, status),
    el('label', { for: 'send-to' }, t('sendTo')),
    sendTo,
    el('label', { for: 'send-message' }, t('message')),
    message,
    el('div', { class: 'actions' }, el('button', { type: 'button', class: 'primary', testid: 'send-invoice-btn', onclick: send }, t('send')), el('a', { href: '/invoices', 'data-link': true, testid: 'back-to-list' }, t('back'))),
  );
}

// A long page: the button a tour points at is below the fold.
function customersPage() {
  const rows = customers.map((c) =>
    el('tr', { testid: 'customer-row' }, el('td', {}, c.name), el('td', {}, c.lastInvoice), el('td', {}, t(c.active ? 'active' : 'inactive'))),
  );
  return el(
    'section',
    { class: 'page', testid: 'customers-page' },
    el('div', { class: 'toolbar' }, el('h1', {}, t('customers'))),
    el(
      'table',
      { testid: 'customer-list' },
      el('thead', {}, el('tr', {}, el('th', {}, t('customer')), el('th', {}, t('lastInvoice')), el('th', {}, t('status')))),
      el('tbody', {}, ...rows),
    ),
    el('div', { class: 'tall' }),
    el(
      'div',
      { class: 'danger' },
      el('p', {}, t('removeHint')),
      el('button', { type: 'button', testid: 'remove-inactive-btn', onclick: confirmRemoval }, t('removeInactive')),
    ),
    el('p', { testid: 'customers-footer' }, t('customersFooter')),
  );
}

// ── A confirmation (PrimeNG ConfirmDialog's markup: an alertdialog centred in
// the viewport over a mask, its Yes / No buttons without a test-id) ──────────

function confirmRemoval() {
  const opener = document.activeElement;
  const mask = el('div', { class: 'demo-mask' });
  const close = () => {
    mask.remove();
    box.remove();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const accept = el(
    'button',
    {
      type: 'button',
      class: 'primary p-confirmdialog-accept-button',
      onclick: () => {
        close();
        removeInactive();
      },
    },
    t('yes'),
  );
  const box = el(
    'div',
    { role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'confirm-title', 'aria-describedby': 'confirm-text', class: 'p-confirmdialog demo-dialog demo-confirm' },
    el('h2', { id: 'confirm-title' }, t('removeInactive')),
    el('p', { id: 'confirm-text' }, t('confirmText')),
    el('div', { class: 'actions' }, el('button', { type: 'button', class: 'p-confirmdialog-reject-button', onclick: close }, t('no')), accept),
  );
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !e.defaultPrevented) close();
  });
  document.body.append(mask, box);
  accept.focus();
}

function removeInactive() {
  customers = customers.filter((c) => c.active);
  try {
    sessionStorage.setItem(CUSTOMER_STORE, JSON.stringify(customers));
  } catch {
    // The demo still works for this page.
  }
  render();
  // PrimeNG's toast: top right, no test-id, gone after a while.
  const toast = el('div', { class: 'demo-toast', role: 'alert' }, t('removed'));
  document.body.append(toast);
  setTimeout(() => toast.remove(), 8000);
}

// ── The dialog (appended to <body>, like PrimeNG / Angular CDK overlays) ──

let dialog = null;

function openDialog() {
  if (dialog) return;
  const opener = document.activeElement;
  const title = el('input', { id: 'invoice-title', testid: 'invoice-title', autocomplete: 'off' });
  const select = combobox();
  const error = el('p', { class: 'error', testid: 'invoice-form-error', role: 'alert', hidden: true });
  const close = () => {
    mask.remove();
    box.remove();
    select.closePopup();
    dialog = null;
    document.removeEventListener('focusout', reclaim, true);
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const save = () => {
    if (!title.value.trim() || !select.value) {
      error.textContent = t('missing');
      error.hidden = false;
      return;
    }
    const id = String(Math.max(...invoices.map((i) => Number(i.id))) + 1);
    invoices = [{ id, title: title.value.trim(), customer: select.value, status: 'Draft' }, ...invoices];
    persist();
    close();
    setTimeout(() => go(`/invoices/${id}`), cfg.saveDelayMs ?? 150);
  };
  const sentinel = () => el('span', { tabindex: '0', class: 'focus-sentinel' });
  const first = sentinel();
  const last = sentinel();
  const mask = el('div', { class: 'demo-mask' });
  const box = el(
    'div',
    { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'invoice-dialog-title', class: 'demo-dialog', testid: 'invoice-dialog' },
    first,
    el('h2', { id: 'invoice-dialog-title' }, t('newInvoice')),
    el('label', { for: 'invoice-title' }, t('title')),
    title,
    el('span', { class: 'label' }, t('customer')),
    select.root,
    error,
    el(
      'div',
      { class: 'actions' },
      el('button', { type: 'button', testid: 'cancel-invoice-btn', onclick: close }, t('cancel')),
      el('button', { type: 'button', class: 'primary', testid: 'save-invoice-btn', onclick: save }, t('save')),
    ),
    last,
  );
  const focusables = () => [...box.querySelectorAll('input,button,[tabindex="0"]:not(.focus-sentinel)')];
  first.addEventListener('focus', () => focusables().at(-1)?.focus());
  last.addEventListener('focus', () => focusables()[0]?.focus());
  // Escape closes the dialog without preventDefault: the tour must still
  // recognise it as the app's Escape (it is inside a dialog).
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !e.defaultPrevented) close();
  });
  // "reclaim" mimics CDK's cdkTrapFocus: focus leaving the dialog is pulled back.
  function reclaim(e) {
    if (cfg.focusTrap === 'reclaim' && dialog && !box.contains(e.relatedTarget)) setTimeout(() => title.focus(), 0);
  }
  document.addEventListener('focusout', reclaim, true);
  document.body.append(mask, box);
  dialog = box;
  title.focus();
}

// A PrimeNG-shaped dropdown: the popup is appended to <body>, the value is set
// programmatically and no `change` event fires — the tour sees the label change.
function combobox() {
  const label = el('span', { class: 'p-select-label demo-select-label' }, t('choose'));
  const button = el('button', { type: 'button', role: 'combobox', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', class: 'demo-select-button' }, label);
  const root = el('div', { class: 'p-select demo-select', testid: 'customer-select' }, button);
  const api = { root, value: '', closePopup };
  let popup = null;
  let active = 0;

  function closePopup() {
    popup?.remove();
    popup = null;
    button.setAttribute('aria-expanded', 'false');
    button.removeAttribute('aria-activedescendant');
  }
  function pick(name) {
    api.value = name;
    root.setAttribute('data-value', name);
    label.textContent = name;
    closePopup();
    button.focus();
  }
  function mark() {
    popup?.querySelectorAll('li').forEach((li, i) => li.toggleAttribute('data-active', i === active));
    button.setAttribute('aria-activedescendant', `customer-option-${active}`);
  }
  function open() {
    if (popup) return;
    const r = root.getBoundingClientRect();
    const items = CUSTOMERS.map((name, i) =>
      el('li', { id: `customer-option-${i}`, role: 'option', class: 'p-select-option', 'aria-selected': String(api.value === name), onclick: () => pick(name) }, name),
    );
    popup = el('div', { class: 'p-select-overlay demo-select-overlay' }, el('ul', { class: 'p-select-list-container', role: 'listbox' }, ...items));
    popup.style.setProperty('--x', `${r.left}px`);
    popup.style.setProperty('--y', `${r.bottom + 4}px`);
    popup.style.setProperty('--w', `${r.width}px`);
    document.body.append(popup);
    button.setAttribute('aria-expanded', 'true');
    active = Math.max(0, CUSTOMERS.indexOf(api.value));
    mark();
  }
  root.addEventListener('click', () => (popup ? closePopup() : open()));
  button.addEventListener('keydown', (e) => {
    if (!popup && ['Enter', ' ', 'ArrowDown'].includes(e.key)) {
      e.preventDefault();
      open();
    } else if (popup && e.key === 'ArrowDown') {
      e.preventDefault();
      active = Math.min(CUSTOMERS.length - 1, active + 1);
      mark();
    } else if (popup && e.key === 'ArrowUp') {
      e.preventDefault();
      active = Math.max(0, active - 1);
      mark();
    } else if (popup && e.key === 'Enter') {
      e.preventDefault();
      pick(CUSTOMERS[active]);
    } else if (popup && e.key === 'Escape') {
      e.preventDefault(); // the app consumed it: the tour must not end
      closePopup();
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (popup && !popup.contains(e.target) && !root.contains(e.target)) closePopup();
  });
  return api;
}

// ── The navigation drawer (phones) ───────────────────────────────────────
// Shaped like PrimeNG's p-drawer: appended to <body> over a mask, sliding in
// from the left, role="complementary" with a focus trap; Escape closes it and
// is consumed (preventDefault). Choosing a link navigates and closes it.

const menuButton = document.querySelector('[data-testid="menu-btn"]');
let drawer = null;

function openDrawer() {
  if (drawer) return;
  const link = (testid, href, key) => el('a', { href, 'data-link': true, testid, onclick: () => closeDrawer(false) }, t(key));
  const sentinel = () => el('span', { tabindex: '0', class: 'focus-sentinel' });
  const first = sentinel();
  const last = sentinel();
  const mask = el('div', { class: 'demo-drawer-mask', onclick: () => closeDrawer() });
  const box = el(
    'div',
    { role: 'complementary', 'aria-label': t('menu'), class: 'demo-drawer', testid: 'drawer' },
    first,
    el(
      'div',
      { class: 'drawer-header' },
      el('strong', {}, t('menu')),
      el('button', { type: 'button', class: 'drawer-close', 'aria-label': t('closeMenu'), testid: 'drawer-close', onclick: () => closeDrawer() }, '×'),
    ),
    el('nav', {}, link('drawer-invoices', '/invoices', 'invoices'), link('drawer-customers', '/customers', 'customers')),
    last,
  );
  const focusables = () => [...box.querySelectorAll('a,button')];
  first.addEventListener('focus', () => focusables().at(-1)?.focus());
  last.addEventListener('focus', () => focusables()[0]?.focus());
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    closeDrawer();
  });
  document.body.append(mask, box);
  drawer = { mask, box };
  menuButton.setAttribute('aria-expanded', 'true');
  box.querySelector('nav a').focus({ preventScroll: true });
  // Two frames, so the closed position is painted and the slide-in transitions.
  requestAnimationFrame(() => requestAnimationFrame(() => box.setAttribute('data-open', '')));
}

function closeDrawer(restoreFocus = true) {
  if (!drawer) return;
  drawer.mask.remove();
  drawer.box.remove();
  drawer = null;
  menuButton.setAttribute('aria-expanded', 'false');
  if (restoreFocus) menuButton.focus();
}

// ── Shell ────────────────────────────────────────────────────────────────

function applyLanguage() {
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach((node) => (node.textContent = t(node.getAttribute('data-i18n'))));
  host.locale = lang;
  render();
}

function logEvent(e) {
  const item = document.createElement('li');
  item.textContent = `${e.type} ${JSON.stringify(e.detail)}`;
  document.getElementById('event-log').prepend(item);
}

async function boot() {
  try {
    const res = await fetch('/demo-config.json', { cache: 'no-store' });
    if (res.ok) cfg = await res.json();
  } catch {
    cfg = {};
  }
  try {
    sessionStorage.setItem('demo:loads', String(Number(sessionStorage.getItem('demo:loads') || 0) + 1));
  } catch {
    // Counting page loads is only a test aid.
  }
  if (cfg.manifest) host.manifest = cfg.manifest;
  if (cfg.fallbackManifest !== undefined) host.fallbackManifest = cfg.fallbackManifest;
  if (cfg.buildId) host.buildId = cfg.buildId;
  if (cfg.targetTimeout) host.targetTimeout = cfg.targetTimeout;
  // Entry routes and links in step text go through the app's router: no reload.
  host.navigate = (_path, { appPath }) => go(appPath);
  for (const name of TOUR_EVENTS) host.addEventListener(name, logEvent);

  menuButton.addEventListener('click', openDrawer);
  document.querySelector('[data-testid="locale-select"]').addEventListener('change', (e) => {
    lang = e.target.value;
    applyLanguage();
  });
  for (const button of document.querySelectorAll('[data-tour]')) {
    button.addEventListener('click', () => {
      const tour = button.getAttribute('data-tour');
      const onDetail = /^\/invoices\/[^/]+/.test(location.pathname);
      host.start(tour, tour === 'send-invoice' && !onDetail ? { params: { invoiceId: '1001' } } : {});
    });
  }
  render();
  if (cfg.autoStart) host.start(cfg.autoStart);
}

boot();
