import { Injectable, signal } from '@angular/core';

export type InvoiceStatus = 'Draft' | 'Sent';

export interface Invoice {
  id: string;
  title: string;
  customer: string;
  status: InvoiceStatus;
}

export const CUSTOMERS = ['Acme Corp', 'Globex', 'Initech'];

const STORE = 'demo:invoices';

function load(): Invoice[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) ?? 'null');
    if (Array.isArray(saved)) return saved;
  } catch {
    // Fall through to the seed data.
  }
  return [
    { id: '1001', title: 'Website hosting', customer: 'Globex', status: 'Draft' },
    { id: '1000', title: 'Annual support', customer: 'Initech', status: 'Sent' },
  ];
}

/** The same seed data and session storage as the plain demo. */
@Injectable({ providedIn: 'root' })
export class Invoices {
  readonly all = signal<Invoice[]>(load());

  find(id: string): Invoice | undefined {
    return this.all().find((i) => i.id === id);
  }

  create(title: string, customer: string): string {
    const id = String(Math.max(...this.all().map((i) => Number(i.id))) + 1);
    this.all.update((list) => [{ id, title, customer, status: 'Draft' }, ...list]);
    this.persist();
    return id;
  }

  send(id: string): void {
    this.all.update((list) => list.map((i) => (i.id === id ? { ...i, status: 'Sent' } : i)));
    this.persist();
  }

  private persist(): void {
    try {
      sessionStorage.setItem(STORE, JSON.stringify(this.all()));
    } catch {
      // The demo still works for this page.
    }
  }
}
