import { Component, PLATFORM_ID } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { TourEventDetailMap } from '@steppy/player/tour';

import { provideSteppyTours } from './provide';
import { SteppyToursComponent } from './tours.component';
import { SteppyTours, type SteppyTourEvent } from './tours.service';
import { META, emit, fakeOverlay, settle, type FakeOverlay } from './testing/fake-overlay';

@Component({ template: '<steppy-tours />', imports: [SteppyToursComponent] })
class Shell {}

let overlay: FakeOverlay;
let tours: SteppyTours;

function setup(readyTimeout = 1000, providers: unknown[] = []): void {
  overlay = fakeOverlay();
  TestBed.configureTestingModule({
    providers: [provideSteppyTours({ fallbackManifest: '/tours/tours.manifest.json', readyTimeout }), overlay.provider(), ...(providers as [])],
  });
  tours = TestBed.inject(SteppyTours);
}

async function mountShell(): Promise<void> {
  const fixture = TestBed.createComponent(Shell);
  await fixture.whenStable();
  await settle();
}

const startCalls = () => overlay.calls.filter((c) => c.name === 'start').map((c) => c.value);

afterEach(() => vi.restoreAllMocks());

describe('SteppyTours.start()', () => {
  it('waits for <steppy-tours> and the overlay, then starts once', async () => {
    setup();
    const started = tours.start('send-invoice', { params: { invoiceId: 1001 } });
    await mountShell();
    expect(startCalls()).toEqual([]);
    overlay.resolveLoad();
    expect(await started).toBe(true);
    expect(startCalls()).toEqual([['send-invoice', { params: { invoiceId: 1001 } }]]);
    overlay.startResult = false;
    expect(await tours.start('create-invoice')).toBe(false); // the overlay's own answer
  });

  it('resolves false and warns when nothing is mounted in time', async () => {
    setup(30);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(await tours.start('create-invoice')).toBe(false);
    expect(warn.mock.calls.flat().join(' ')).toMatch(/\[steppy-tours\] start\("create-invoice"\): no <steppy-tours> is mounted/);
  });

  it('is cancelled by stop() while it waits', async () => {
    setup();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const started = tours.start('create-invoice');
    tours.stop();
    expect(await started).toBe(false);
    await mountShell();
    overlay.resolveLoad();
    await settle();
    expect(startCalls()).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
  });

  it('lets the last call win', async () => {
    setup();
    const first = tours.start('create-invoice');
    const second = tours.start('send-invoice');
    await mountShell();
    overlay.resolveLoad();
    expect([await first, await second]).toEqual([false, true]);
    expect(startCalls()).toEqual([['send-invoice', {}]]);
  });

  it('resolves false at once when the overlay fails to load', async () => {
    setup();
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const started = tours.start('create-invoice');
    await mountShell();
    overlay.rejectLoad();
    expect(await started).toBe(false);
    expect(error.mock.calls.flat().join(' ')).toMatch(/the tour overlay failed to load/);
    expect(await tours.start('create-invoice')).toBe(false);
  });

  it('does nothing on the server', async () => {
    setup(1000, [{ provide: PLATFORM_ID, useValue: 'server' }]);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(await tours.start('create-invoice')).toBe(false);
    expect(warn).not.toHaveBeenCalled();
    expect(overlay.loads).toBe(0);
  });
});

describe('SteppyTours state', () => {
  it('follows the overlay events', async () => {
    setup();
    await mountShell();
    overlay.resolveLoad();
    await settle();
    const seen: SteppyTourEvent['type'][] = [];
    tours.events.subscribe((e) => seen.push(e.type));
    const step = { ...META, stepId: 'invoice-title', testId: 'invoice-title', index: 1, total: 5 };

    emit(overlay, 'tour-start', { ...META, total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    expect([tours.active(), tours.current(), tours.lastError()]).toEqual([true, null, null]);
    emit(overlay, 'tour-step', { ...step, advance: 'input', reason: 'advance' });
    expect(tours.current()).toEqual({ tourId: 'create-invoice', stepId: 'invoice-title', testId: 'invoice-title', index: 1, total: 5 });
    const late = { reason: 'target-timeout', fatal: false, message: 'late', tourId: 'create-invoice' } as TourEventDetailMap['tour-error'];
    emit(overlay, 'tour-error', late);
    expect([tours.active(), tours.lastError()]).toEqual([true, late]);
    emit(overlay, 'tour-offpath', { ...step, reason: 'target-lost' });
    expect(tours.active()).toBe(true);
    emit(overlay, 'tour-complete', { ...META, total: 5 });
    expect([tours.active(), tours.current()]).toEqual([false, null]);

    emit(overlay, 'tour-start', { ...META, total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    expect(tours.lastError()).toBeNull();
    emit(overlay, 'tour-error', { reason: 'invalid-data', fatal: true, message: 'bad' } as TourEventDetailMap['tour-error']);
    expect([tours.active(), tours.current(), tours.lastError()?.reason]).toEqual([false, null, 'invalid-data']);
    emit(overlay, 'tour-start', { ...META, total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    emit(overlay, 'tour-dismiss', { ...step, via: 'escape' });
    expect(tours.active()).toBe(false);

    expect(seen).toEqual([
      'tour-start',
      'tour-step',
      'tour-error',
      'tour-offpath',
      'tour-complete',
      'tour-start',
      'tour-error',
      'tour-start',
      'tour-dismiss',
    ]);
  });

  it('forwards stop() and next() to the overlay', async () => {
    setup();
    await mountShell();
    overlay.resolveLoad();
    await settle();
    tours.next();
    tours.stop('help closed');
    expect(overlay.calls.filter((c) => c.kind === 'call').map((c) => [c.name, c.value])).toEqual([
      ['next', undefined],
      ['stop', 'help closed'],
    ]);
  });
});
