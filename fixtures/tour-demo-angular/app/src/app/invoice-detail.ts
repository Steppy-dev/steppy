import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TranslatePipe } from '@ngx-translate/core';
import { InputText } from 'primeng/inputtext';
import { Textarea } from 'primeng/textarea';

import { TidPipe } from './demo-config';
import { Invoices } from './invoices';

/** An invoice and its send form. Lazy-loaded: the tour waits for it to render. */
@Component({
  selector: 'app-invoice-detail',
  imports: [RouterLink, TranslatePipe, TidPipe, InputText, Textarea],
  template: `
    @if (invoice(); as inv) {
      <section class="page" [attr.data-testid]="'invoice-detail' | tid">
        <h1 [attr.data-testid]="'invoice-detail-title' | tid">{{ inv.id }} — {{ inv.title }}</h1>
        <p>
          {{ 'customer' | translate }}: {{ inv.customer }} · {{ 'status' | translate }}:
          <span [attr.data-testid]="'invoice-status' | tid" [class]="'status ' + inv.status">{{ inv.status | translate }}</span>
        </p>
        <label for="send-to">{{ 'sendTo' | translate }}</label>
        <input pInputText type="email" id="send-to" autocomplete="off" [attr.data-testid]="'send-to' | tid" />
        <label for="send-message">{{ 'message' | translate }}</label>
        <textarea pTextarea id="send-message" rows="3" [attr.data-testid]="'send-message' | tid"></textarea>
        <div class="actions">
          <button type="button" class="primary" [attr.data-testid]="'send-invoice-btn' | tid" (click)="invoices.send(inv.id)">
            {{ 'send' | translate }}
          </button>
          <a routerLink="/invoices" [attr.data-testid]="'back-to-list' | tid">{{ 'back' | translate }}</a>
        </div>
      </section>
    } @else {
      <section class="page">
        <p>{{ 'notFound' | translate }}</p>
      </section>
    }
  `,
})
export class InvoiceDetail {
  /** The route's ``:invoiceId`` (``withComponentInputBinding``). */
  readonly invoiceId = input.required<string>();
  protected readonly invoices = inject(Invoices);
  protected readonly invoice = computed(() => this.invoices.find(this.invoiceId()));
}
