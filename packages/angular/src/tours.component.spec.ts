import { Component, Injectable, PLATFORM_ID, inject, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { TourEventDetailMap } from '@steppy/player/tour';

import { provideSteppyTours } from './provide';
import { SteppyToursComponent } from './tours.component';
import { SteppyTours } from './tours.service';
import type { SteppyToursConfig } from './config';
import { META, emit, fakeOverlay, settle, type FakeOverlay } from './testing/fake-overlay';

let overlay: FakeOverlay;

function setup(config: SteppyToursConfig = {}, providers: unknown[] = []): void {
  overlay = fakeOverlay();
  TestBed.configureTestingModule({
    providers: [provideSteppyTours({ fallbackManifest: '/tours/tours.manifest.json', ...config }), overlay.provider(), ...(providers as [])],
  });
}

async function mount() {
  const fixture = TestBed.createComponent(SteppyToursComponent);
  await fixture.whenStable();
  await settle();
  return fixture;
}

async function mountDefined() {
  const fixture = await mount();
  overlay.resolveLoad();
  await settle();
  return fixture;
}

const own = (el: Element, prop: string) => Object.prototype.hasOwnProperty.call(el, prop);

afterEach(() => vi.restoreAllMocks());

describe('<steppy-tours>', () => {
  it('mounts one overlay element after the first render, with its properties set before the upgrade', async () => {
    setup({ locale: 'fr', targetTimeout: 3000, buildId: 'b-1' });
    await mount();
    const el = overlay.element()!;
    expect(el).not.toBeNull();
    expect(customElements.get(overlay.tag)).toBeUndefined();
    // Not upgraded yet: plain own properties, as the app would leave them.
    expect(own(el, 'fallbackManifest') && own(el, 'navigate')).toBe(true);
    overlay.resolveLoad();
    await settle();
    expect(own(el, 'fallbackManifest')).toBe(false);
    expect([el.fallbackManifest, el.locale, el.buildId, el.targetTimeout]).toEqual(['/tours/tours.manifest.json', 'fr', 'b-1', 3000]);
    expect(el.navigate).toBeNull(); // no routes: the overlay's own full navigation
    expect(el.disabled).toBe(false);
    expect(overlay.loads).toBe(1);
    expect(document.querySelectorAll(overlay.tag)).toHaveLength(1);
  });

  it('lets inputs override the configuration, and falls back to it when an input is cleared', async () => {
    setup({ manifest: 'https://guide.example.com/tours/{buildId}/tours.manifest.json', targetTimeout: 3000 });
    const fixture = await mountDefined();
    const el = overlay.element()!;
    fixture.componentRef.setInput('manifest', 'https://other.example.com/m.json');
    fixture.componentRef.setInput('targetTimeout', '5000');
    fixture.componentRef.setInput('disabled', '');
    await fixture.whenStable();
    expect([el.manifest, el.targetTimeout, el.disabled]).toEqual(['https://other.example.com/m.json', 5000, true]);
    fixture.componentRef.setInput('manifest', undefined);
    fixture.componentRef.setInput('targetTimeout', undefined);
    fixture.componentRef.setInput('disabled', 'false');
    await fixture.whenStable();
    expect([el.manifest, el.targetTimeout, el.disabled]).toEqual(['https://guide.example.com/tours/{buildId}/tours.manifest.json', 3000, false]);
  });

  it('passes the theme and appearance through, inputs over configuration', async () => {
    setup({ theme: { accent: '#0b5cad' }, appearance: 'light' });
    const fixture = await mountDefined();
    const el = overlay.element()!;
    expect([el.theme, el.appearance]).toEqual([{ accent: '#0b5cad' }, 'light']);
    fixture.componentRef.setInput('theme', { width: '360px' });
    fixture.componentRef.setInput('appearance', 'auto');
    await fixture.whenStable();
    expect([el.theme, el.appearance]).toEqual([{ width: '360px' }, 'auto']);
    fixture.componentRef.setInput('theme', undefined);
    await fixture.whenStable();
    expect(el.theme).toEqual({ accent: '#0b5cad' });
  });

  it('leaves the overlay default appearance alone when none is configured', async () => {
    setup();
    await mountDefined();
    expect(overlay.calls.some((c) => c.kind === 'set' && c.name === 'appearance')).toBe(false);
    expect(overlay.calls.some((c) => c.kind === 'set' && c.name === 'infoTargetClick')).toBe(false);
  });

  it('passes infoTargetClick through, input over configuration', async () => {
    setup({ infoTargetClick: 'next' });
    const fixture = await mountDefined();
    const el = overlay.element()!;
    expect(el.infoTargetClick).toBe('next');
    fixture.componentRef.setInput('infoTargetClick', 'block');
    await fixture.whenStable();
    expect(el.infoTargetClick).toBe('block');
  });

  it('emits each overlay event as an output and records it in the service', async () => {
    setup();
    const fixture = await mountDefined();
    const seen: string[] = [];
    const c = fixture.componentInstance;
    c.tourStart.subscribe((d) => seen.push(`start:${d.tourId}`));
    c.tourStep.subscribe((d) => seen.push(`step:${d.stepId}`));
    c.tourError.subscribe((d) => seen.push(`error:${d.reason}`));
    c.tourOffpath.subscribe((d) => seen.push(`offpath:${d.stepId}`));
    c.tourDismiss.subscribe((d) => seen.push(`dismiss:${d.via}`));
    c.tourComplete.subscribe((d) => seen.push(`complete:${d.total}`));
    const step = { ...META, stepId: 'new-invoice-btn', testId: 'new-invoice-btn', index: 0, total: 5 };
    emit(overlay, 'tour-start', { ...META, total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    emit(overlay, 'tour-step', { ...step, advance: 'click', reason: 'start' });
    emit(overlay, 'tour-error', { reason: 'target-timeout', fatal: false, message: 'late' } as TourEventDetailMap['tour-error']);
    emit(overlay, 'tour-offpath', { ...step, reason: 'target-lost' });
    emit(overlay, 'tour-dismiss', { ...step, via: 'close' });
    emit(overlay, 'tour-complete', { ...META, total: 5 });
    expect(seen).toEqual(['start:create-invoice', 'step:new-invoice-btn', 'error:target-timeout', 'offpath:new-invoice-btn', 'dismiss:close', 'complete:5']);
  });

  it('catches an event the overlay dispatches before it is defined (a resumed tour)', async () => {
    setup();
    const fixture = await mount();
    const starts: boolean[] = [];
    fixture.componentInstance.tourStart.subscribe((d) => starts.push(d.resumed));
    overlay.element()!.dispatchEvent(
      new CustomEvent('tour-start', { detail: { ...META, total: 5, source: 'bundled', resumed: true, entryNavigated: false } }),
    );
    expect(starts).toEqual([true]);
    expect(TestBed.inject(SteppyTours).active()).toBe(true);
  });

  it('warns about a second instance and mounts nothing for it', async () => {
    @Component({ template: '<steppy-tours /><steppy-tours />', imports: [SteppyToursComponent] })
    class TwoShells {}
    setup();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fixture = TestBed.createComponent(TwoShells);
    await fixture.whenStable();
    await settle();
    expect(document.querySelectorAll(overlay.tag)).toHaveLength(1);
    expect(warn.mock.calls.flat().join(' ')).toMatch(/another <steppy-tours> is already mounted/);
  });

  it('on destroy, removes the element and releases the service without stopping the tour', async () => {
    setup();
    const fixture = await mountDefined();
    const tours = TestBed.inject(SteppyTours);
    const el = overlay.element()!;
    emit(overlay, 'tour-start', { ...META, total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    expect(tours.active()).toBe(true);
    const outputs: string[] = [];
    fixture.componentInstance.tourComplete.subscribe(() => outputs.push('complete'));
    fixture.destroy();
    expect(el.isConnected).toBe(false);
    expect(tours.active()).toBe(false);
    el.dispatchEvent(new CustomEvent('tour-complete', { detail: { ...META, total: 5 } }));
    expect(outputs).toEqual([]);
    expect(overlay.calls.some((c) => c.name === 'stop')).toBe(false);
  });

  it('renders nothing and loads nothing on the server', async () => {
    setup({}, [{ provide: PLATFORM_ID, useValue: 'server' }]);
    await mount();
    expect(overlay.element()).toBeNull();
    expect(overlay.loads).toBe(0);
  });

  it('re-renders a template bound to the service state', async () => {
    @Component({
      template: '<steppy-tours /><output>{{ tours.current()?.stepId ?? "idle" }}</output>',
      imports: [SteppyToursComponent],
    })
    class Shell {
      readonly tours = inject(SteppyTours);
    }
    setup();
    const fixture = TestBed.createComponent(Shell);
    await fixture.whenStable();
    await settle();
    overlay.resolveLoad();
    await settle();
    expect(fixture.nativeElement.querySelector('output').textContent).toBe('idle');
    emit(overlay, 'tour-step', { ...META, stepId: 'invoice-title', testId: 'invoice-title', index: 1, total: 5, advance: 'input', reason: 'advance' });
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelector('output').textContent).toBe('invoice-title');
  });
});

describe('the tours language', () => {
  it('follows a locale signal', async () => {
    const lang = signal<string | null>('en');
    setup({ locale: lang });
    const fixture = await mountDefined();
    const el = overlay.element()!;
    expect(el.locale).toBe('en');
    lang.set('fr');
    await fixture.whenStable();
    expect(el.locale).toBe('fr');
    lang.set(null);
    await fixture.whenStable();
    expect(el.locale).toBe('');
  });

  it('calls a locale factory once, in an injection context', async () => {
    @Injectable({ providedIn: 'root' })
    class FakeI18n {
      readonly lang = signal('nl');
      calls = 0;
    }
    setup({
      locale: () => {
        const i18n = inject(FakeI18n);
        i18n.calls++;
        return i18n.lang;
      },
    });
    const fixture = await mountDefined();
    const i18n = TestBed.inject(FakeI18n);
    expect(overlay.element()!.locale).toBe('nl');
    i18n.lang.set('de');
    await fixture.whenStable();
    expect(overlay.element()!.locale).toBe('de');
    expect(i18n.calls).toBe(1);
  });

  it('writes an empty string, never undefined, without a locale; the input wins over the source', async () => {
    setup({ locale: undefined });
    const fixture = await mountDefined();
    const el = overlay.element()!;
    expect(el.locale).toBe('');
    fixture.componentRef.setInput('locale', 'fr');
    await fixture.whenStable();
    expect(el.locale).toBe('fr');
    fixture.componentRef.setInput('locale', null);
    await fixture.whenStable();
    expect(el.locale).toBe('');
  });
});
