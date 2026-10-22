/**
 * Readers for the ``input`` and ``change`` advance kinds.
 *
 * ``valueSignature`` summarises everything a "pick a value" widget can change:
 * native controls inside the target, ARIA state, and the visible label. A
 * component library that sets a value programmatically fires no ``change``
 * event (PrimeNG's select does exactly that), so the overlay compares
 * signatures instead of listening for the event.
 */

const CONTROLS = 'input,select,textarea';
const ARIA = '[aria-checked],[aria-pressed],[aria-selected]';
const LIMIT = 50;

function controlSig(el: Element): string {
  if (el instanceof HTMLInputElement) {
    if (el.type === 'checkbox' || el.type === 'radio') return `c:${el.checked}`;
    if (el.type === 'file') return `f:${el.files?.length ?? 0}`;
    return `v:${el.value}`;
  }
  if (el instanceof HTMLSelectElement) {
    return `s:${el.multiple ? [...el.options].filter((o) => o.selected).map((o) => o.value).join('|') : el.value}`;
  }
  if (el instanceof HTMLTextAreaElement) return `v:${el.value}`;
  return '';
}

export function valueSignature(target: Element): string {
  const controls = [...(target.matches(CONTROLS) ? [target] : []), ...[...target.querySelectorAll(CONTROLS)].slice(0, LIMIT)];
  const aria = [...(target.matches(ARIA) ? [target] : []), ...[...target.querySelectorAll(ARIA)].slice(0, LIMIT)].map(
    (el) =>
      `a:${el.getAttribute('aria-checked') ?? ''}${el.getAttribute('aria-pressed') ?? ''}${el.getAttribute('aria-selected') ?? ''}`,
  );
  const text = `t:${(target.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 500)}`;
  return [...controls.map(controlSig), ...aria, text].join('\u0001');
}

/** True while the target, or anything in it, reports an open popup (a dropdown in use). */
export function isExpanded(target: Element): boolean {
  return target.matches('[aria-expanded="true"]') || target.querySelector('[aria-expanded="true"]') !== null;
}

function editableText(el: Element): string | null {
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return el.value;
  const editable = el.getAttribute('contenteditable');
  if (el instanceof HTMLElement && (el.isContentEditable || (editable !== null && editable !== 'false'))) {
    return el.textContent ?? '';
  }
  return null;
}

/** The value an ``input`` step watches: the target's own, else its first field. */
export function readFieldValue(target: Element): string {
  const own = editableText(target);
  if (own !== null) return own;
  const inner = target.querySelector(`${CONTROLS},[contenteditable]:not([contenteditable="false"])`);
  return (inner && editableText(inner)) ?? '';
}

/** A select-like widget, whose popup the bubble should leave room for (placed on top). */
export function isComboboxLike(target: Element): boolean {
  const q = 'select,[role=combobox],[aria-haspopup]:not([aria-haspopup="false"])';
  return target.matches(q) || target.querySelector(q) !== null;
}
