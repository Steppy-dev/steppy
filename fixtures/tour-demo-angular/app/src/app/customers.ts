import { Component, inject, signal } from '@angular/core';
import { TranslatePipe, TranslateService } from '@ngx-translate/core';
import { ConfirmationService, MessageService } from 'primeng/api';
import { ConfirmDialog } from 'primeng/confirmdialog';
import { Toast } from 'primeng/toast';

import { TidPipe } from './demo-config';

export interface Customer {
  name: string;
  lastInvoice: string;
  active: boolean;
}

const STORE = 'demo:customers';

function load(): Customer[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) ?? 'null');
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

/**
 * The customer list: long enough that the button a tour points at is below
 * the fold, guarded by a PrimeNG ConfirmDialog (centred in the viewport, its
 * Yes / No buttons without a test-id) and answered with a PrimeNG toast.
 */
@Component({
  selector: 'app-customer-list',
  imports: [TranslatePipe, TidPipe, ConfirmDialog, Toast],
  providers: [ConfirmationService, MessageService],
  template: `
    <section class="page" [attr.data-testid]="'customers-page' | tid">
      <div class="toolbar">
        <h1>{{ 'customers' | translate }}</h1>
      </div>
      <table [attr.data-testid]="'customer-list' | tid">
        <thead>
          <tr>
            <th>{{ 'customer' | translate }}</th>
            <th>{{ 'lastInvoice' | translate }}</th>
            <th>{{ 'status' | translate }}</th>
          </tr>
        </thead>
        <tbody>
          @for (c of customers(); track c.name) {
            <tr [attr.data-testid]="'customer-row' | tid">
              <td>{{ c.name }}</td>
              <td>{{ c.lastInvoice }}</td>
              <td>{{ (c.active ? 'active' : 'inactive') | translate }}</td>
            </tr>
          }
        </tbody>
      </table>
      <div class="tall"></div>
      <div class="danger">
        <p>{{ 'removeHint' | translate }}</p>
        <button type="button" [attr.data-testid]="'remove-inactive-btn' | tid" (click)="confirmRemoval()">
          {{ 'removeInactive' | translate }}
        </button>
      </div>
      <p [attr.data-testid]="'customers-footer' | tid">{{ 'customersFooter' | translate }}</p>
    </section>
    <p-confirmdialog />
    <p-toast />
  `,
})
export class CustomerList {
  protected readonly customers = signal<Customer[]>(load());
  private readonly confirmation = inject(ConfirmationService);
  private readonly messages = inject(MessageService);
  private readonly translate = inject(TranslateService);

  protected confirmRemoval(): void {
    this.confirmation.confirm({
      header: this.translate.instant('removeInactive'),
      message: this.translate.instant('confirmText'),
      acceptLabel: this.translate.instant('yes'),
      rejectLabel: this.translate.instant('no'),
      accept: () => this.removeInactive(),
    });
  }

  private removeInactive(): void {
    this.customers.update((list) => list.filter((c) => c.active));
    try {
      sessionStorage.setItem(STORE, JSON.stringify(this.customers()));
    } catch {
      // The demo still works for this page.
    }
    this.messages.add({ severity: 'success', summary: this.translate.instant('removed'), life: 8000 });
  }
}
