import { inject } from '@angular/core';
import type { Routes } from '@angular/router';

import { DemoConfig } from './demo-config';
import { InvoiceList } from './invoice-list';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'invoices' },
  { path: 'invoices', component: InvoiceList },
  {
    path: 'invoices/:invoiceId',
    loadComponent: () => import('./invoice-detail').then((m) => m.InvoiceDetail),
    // `blockDetail` in /demo-config.json: a guard that refuses the page, so a
    // tour whose entry route it is must fail at once.
    canActivate: [() => !inject(DemoConfig).value().blockDetail],
  },
  { path: 'customers', loadComponent: () => import('./customers').then((m) => m.CustomerList) },
  { path: '**', redirectTo: 'invoices' },
];
