import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { TranslatePipe } from '@ngx-translate/core';
import { Dialog } from 'primeng/dialog';
import { InputText } from 'primeng/inputtext';
import { Select } from 'primeng/select';

import { DemoConfig, TidPipe } from './demo-config';
import { CUSTOMERS, Invoices } from './invoices';

/**
 * The invoice list, with the new-invoice form in a PrimeNG modal dialog
 * (appended to <body>, behind a mask, with a focus trap) and the customer in a
 * PrimeNG select (its option list appended to <body>, its value set
 * programmatically) — the shapes a tour has to live with in a real app.
 */
@Component({
  selector: 'app-invoice-list',
  imports: [FormsModule, RouterLink, TranslatePipe, TidPipe, Dialog, InputText, Select],
  template: `
    <section class="page">
      <div class="toolbar">
        <h1>{{ 'invoices' | translate }}</h1>
        <button type="button" class="primary" [attr.data-testid]="'new-invoice-btn' | tid" (click)="open()">
          {{ 'newInvoice' | translate }}
        </button>
      </div>
      <table [attr.data-testid]="'invoice-list' | tid">
        <tbody>
          @for (inv of invoices.all(); track inv.id) {
            <tr [attr.data-testid]="'invoice-row' | tid">
              <td>{{ inv.id }}</td>
              <td>{{ inv.title }}</td>
              <td>{{ inv.customer }}</td>
              <td>{{ inv.status | translate }}</td>
              <td>
                <a [routerLink]="['/invoices', inv.id]" [attr.data-testid]="'invoice-open' | tid">{{ 'open' | translate }}</a>
              </td>
            </tr>
          }
        </tbody>
      </table>
      <div class="tall"></div>
      <p [attr.data-testid]="'invoices-footer' | tid">{{ 'footer' | translate }}</p>
    </section>

    <p-dialog [(visible)]="dialogOpen" [modal]="true" appendTo="body" [header]="'newInvoice' | translate" (onHide)="reset()">
      <div class="form" [attr.data-testid]="'invoice-dialog' | tid">
        <label for="invoice-title">{{ 'title' | translate }}</label>
        <input pInputText id="invoice-title" autocomplete="off" [attr.data-testid]="'invoice-title' | tid" [(ngModel)]="title" />
        <span class="label">{{ 'customer' | translate }}</span>
        <p-select
          [attr.data-testid]="'customer-select' | tid"
          [options]="customers"
          [(ngModel)]="customer"
          [placeholder]="'choose' | translate"
          appendTo="body"
        />
        @if (error()) {
          <p class="error" role="alert" [attr.data-testid]="'invoice-form-error' | tid">{{ 'missing' | translate }}</p>
        }
        <div class="actions">
          <button type="button" [attr.data-testid]="'cancel-invoice-btn' | tid" (click)="dialogOpen = false">
            {{ 'cancel' | translate }}
          </button>
          <button type="button" class="primary" [attr.data-testid]="'save-invoice-btn' | tid" (click)="save()">
            {{ 'save' | translate }}
          </button>
        </div>
      </div>
    </p-dialog>
  `,
})
export class InvoiceList {
  protected readonly invoices = inject(Invoices);
  protected readonly customers = CUSTOMERS;
  private readonly router = inject(Router);
  private readonly config = inject(DemoConfig);

  protected dialogOpen = false;
  protected title = '';
  protected customer: string | null = null;
  protected readonly error = signal(false);

  protected open(): void {
    this.dialogOpen = true;
  }

  protected reset(): void {
    this.title = '';
    this.customer = null;
    this.error.set(false);
  }

  protected save(): void {
    if (!this.title.trim() || !this.customer) {
      this.error.set(true);
      return;
    }
    const id = this.invoices.create(this.title.trim(), this.customer);
    this.dialogOpen = false;
    setTimeout(() => void this.router.navigate(['/invoices', id]), this.config.value().saveDelayMs ?? 150);
  }
}
