/**
 * The zone.js half (`ng test --configuration zone`): the overlay's work stays
 * outside the Angular zone — its observers, polls and frames must never drive
 * change detection — while what reaches the app (events, outputs, the router)
 * comes back inside it.
 */

import { ApplicationRef, Component, NgZone } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { filter, firstValueFrom } from 'rxjs';

import { provideSteppyTours } from './provide';
import { SteppyToursComponent } from './tours.component';
import { SteppyTours } from './tours.service';
import { META, emit, fakeOverlay, settle, type FakeOverlay } from './testing/fake-overlay';

@Component({ template: '' })
class Page {}

let overlay: FakeOverlay;

function setup(readyTimeout = 1000): void {
  overlay = fakeOverlay();
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: 'invoices/:invoiceId', component: Page }]),
      provideSteppyTours({ fallbackManifest: '/tours/tours.manifest.json', readyTimeout }),
      overlay.provider(),
    ],
  });
}

async function mountDefined() {
  const fixture = TestBed.createComponent(SteppyToursComponent);
  fixture.autoDetectChanges();
  await fixture.whenStable();
  await settle();
  overlay.resolveLoad();
  await settle();
  return fixture;
}

describe('zones', () => {
  it('runs as a zone.js app', () => {
    setup();
    expect(TestBed.inject(NgZone)).toBeInstanceOf(NgZone);
    expect(TestBed.inject(NgZone).run(() => NgZone.isInAngularZone())).toBe(true);
  });

  it('calls start(), stop() and next() outside the zone, even from inside it', async () => {
    setup();
    await mountDefined();
    const zone = TestBed.inject(NgZone);
    const tours = TestBed.inject(SteppyTours);
    await zone.run(() => tours.start('create-invoice'));
    zone.run(() => tours.next());
    zone.run(() => tours.stop());
    const calls = overlay.calls.filter((c) => c.kind === 'call');
    expect(calls.map((c) => [c.name, c.inZone])).toEqual([
      ['start', false],
      ['next', false],
      ['stop', false],
    ]);
  });

  it('writes every property outside the zone, updates included', async () => {
    setup();
    const fixture = await mountDefined();
    TestBed.inject(NgZone).run(() => fixture.componentRef.setInput('locale', 'fr'));
    await fixture.whenStable();
    const sets = overlay.calls.filter((c) => c.kind === 'set');
    expect(sets.some((c) => c.name === 'locale' && c.value === 'fr')).toBe(true);
    expect(sets.filter((c) => c.inZone)).toEqual([]);
  });

  it('runs the router inside the zone when the overlay navigates from outside it', async () => {
    setup();
    await mountDefined();
    const router = TestBed.inject(Router);
    const zone = TestBed.inject(NgZone);
    const inZone: boolean[] = [];
    const original = router.navigateByUrl.bind(router);
    vi.spyOn(router, 'navigateByUrl').mockImplementation((url, extras) => {
      inZone.push(NgZone.isInAngularZone());
      return original(url, extras);
    });
    const el = overlay.element()!;
    await zone.runOutsideAngular(() => el.navigate!('/invoices/1001', { appPath: '/invoices/1001', tourId: 'send-invoice' }));
    expect(inZone).toEqual([true]);
    expect(router.url).toBe('/invoices/1001');
  });

  it('delivers outputs and service events inside the zone', async () => {
    setup();
    const fixture = await mountDefined();
    const seen: boolean[] = [];
    fixture.componentInstance.tourComplete.subscribe(() => seen.push(NgZone.isInAngularZone()));
    TestBed.inject(SteppyTours).events.subscribe(() => seen.push(NgZone.isInAngularZone()));
    TestBed.inject(NgZone).runOutsideAngular(() => emit(overlay, 'tour-complete', { ...META, total: 5 }));
    expect(seen).toEqual([true, true]);
  });

  it('does not keep the app unstable while start() waits for the overlay', async () => {
    setup(60_000);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const tours = TestBed.inject(SteppyTours);
    const appRef = TestBed.inject(ApplicationRef);
    const started = TestBed.inject(NgZone).run(() => tours.start('create-invoice'));
    const stable = await Promise.race([
      firstValueFrom(appRef.isStable.pipe(filter(Boolean))),
      new Promise<false>((r) => setTimeout(() => r(false), 1000)),
    ]);
    expect(stable).toBe(true);
    tours.stop();
    expect(await started).toBe(false);
    expect(warn).not.toHaveBeenCalled();
  });
});
