import { Component, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import type { TourNavigate } from '@steppy/player/tour';

import { createNavigate } from './navigate';
import type { SteppyToursConfig } from './config';

@Component({ template: '' })
class Page {}

function withRoutes(): void {
  TestBed.configureTestingModule({
    providers: [
      provideRouter([
        { path: 'invoices', component: Page },
        { path: 'invoices/:invoiceId', component: Page },
        { path: 'locked', component: Page, canActivate: [() => false] },
        { path: 'moved', component: Page, canActivate: [() => inject(Router).parseUrl('/invoices')] },
      ]),
    ],
  });
}

const make = (option?: SteppyToursConfig['navigate']) => TestBed.runInInjectionContext(() => createNavigate(option));
const ctx = (appPath: string) => ({ appPath, tourId: 'send-invoice' });

describe('the router bridge', () => {
  it("navigates to the tour's app path, not the document path", async () => {
    withRoutes();
    const go = make()!;
    await go('/app/invoices/1001', ctx('/invoices/1001'));
    expect(TestBed.inject(Router).url).toBe('/invoices/1001');
  });

  it('settles on a navigation to the current URL, which the router resolves false', async () => {
    withRoutes();
    const go = make()!;
    await go('/invoices', ctx('/invoices'));
    await expect(go('/invoices', ctx('/invoices'))).resolves.toBeUndefined();
    await expect(go('/invoices?page=2', ctx('/invoices?page=2'))).resolves.toBeUndefined();
  });

  it('rejects a navigation a guard refused, so the tour fails at once', async () => {
    withRoutes();
    await expect(make()!('/locked', ctx('/locked'))).rejects.toThrow(/did not navigate to \/locked/);
  });

  it('rejects a navigation a guard redirected elsewhere', async () => {
    withRoutes();
    await expect(make()!('/moved', ctx('/moved'))).rejects.toThrow(/redirected \/moved to \/invoices/);
  });

  it("uses an app's own function, and passes its rejection through", async () => {
    withRoutes();
    const seen: string[] = [];
    const custom: TourNavigate = (path, { appPath }) => void seen.push(`${path}|${appPath}`);
    await make(custom)!('/app/x', ctx('/x'));
    expect(seen).toEqual(['/app/x|/x']);
    await expect(make(() => Promise.reject(new Error('nope')))!('/x', ctx('/x'))).rejects.toThrow('nope');
    await expect(make(() => { throw new Error('sync'); })!('/x', ctx('/x'))).rejects.toThrow('sync');
  });

  it("leaves navigation to the overlay for 'document', and when the app has no routes", () => {
    withRoutes();
    expect(make('document')).toBeNull();
    TestBed.resetTestingModule();
    expect(make()).toBeNull();
  });
});
