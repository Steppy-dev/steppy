import { Component, DOCUMENT, afterNextRender, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { TranslatePipe, TranslateService } from '@ngx-translate/core';
import { SteppyTourStart, SteppyTours, SteppyToursComponent } from '@steppy/angular';
import { Drawer } from 'primeng/drawer';

import { DemoConfig, TidPipe } from './demo-config';

/**
 * The app shell: the navigation, the tour buttons, the language switch, the
 * routed page, and ``<steppy-tours>`` mounted once. On a phone the header
 * links move into a PrimeNG drawer behind a menu button. The aside shows the
 * tour state and events the ``SteppyTours`` service reports (tests read them).
 */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, TranslatePipe, TidPipe, SteppyToursComponent, SteppyTourStart, Drawer],
  template: `
    <div id="app-shell">
      <header class="shell-header">
        <button
          type="button"
          class="menu-btn"
          [attr.data-testid]="'menu-btn' | tid"
          [attr.aria-expanded]="menuOpen()"
          (click)="menuOpen.set(true)"
        >
          <span aria-hidden="true">☰</span> {{ 'menu' | translate }}
        </button>
        <a routerLink="/invoices" [attr.data-testid]="'nav-invoices' | tid" class="brand">{{ 'invoices' | translate }}</a>
        <nav class="nav-links">
          <a routerLink="/customers" [attr.data-testid]="'nav-customers' | tid">{{ 'customers' | translate }}</a>
        </nav>
        <span class="spacer"></span>
        <button type="button" [attr.data-testid]="'start-tour-btn' | tid" steppyTourStart="create-invoice">
          {{ 'tourCreate' | translate }}
        </button>
        <button type="button" [attr.data-testid]="'start-send-tour-btn' | tid" (click)="startSend()">
          {{ 'tourSend' | translate }}
        </button>
        <button type="button" [attr.data-testid]="'start-customers-tour-btn' | tid" steppyTourStart="remove-inactive-customers">
          {{ 'tourCustomers' | translate }}
        </button>
        <label class="locale">
          <span>{{ 'language' | translate }}</span>
          <select [attr.data-testid]="'locale-select' | tid" [value]="lang()" (change)="setLang($any($event.target).value)">
            <option value="en">English</option>
            <option value="fr">Français</option>
          </select>
        </label>
      </header>
      <p-drawer [visible]="menuOpen()" (visibleChange)="menuOpen.set($event)" [header]="'menu' | translate">
        <nav class="drawer-links">
          <a routerLink="/invoices" [attr.data-testid]="'drawer-invoices' | tid" (click)="menuOpen.set(false)">
            {{ 'invoices' | translate }}
          </a>
          <a routerLink="/customers" [attr.data-testid]="'drawer-customers' | tid" (click)="menuOpen.set(false)">
            {{ 'customers' | translate }}
          </a>
        </nav>
      </p-drawer>
      <main id="view"><router-outlet /></main>
      <steppy-tours
        [manifest]="cfg().manifest"
        [fallbackManifest]="cfg().fallbackManifest"
        [buildId]="cfg().buildId"
        [targetTimeout]="cfg().targetTimeout"
      />
    </div>
    <aside class="event-log" aria-label="Tour events">
      <h2>{{ 'events' | translate }}</h2>
      <output data-testid="tour-state">{{ state() }}</output>
      <ol data-testid="event-log">
        @for (line of log(); track $index) {
          <li>{{ line }}</li>
        }
      </ol>
    </aside>
  `,
})
export class App {
  protected readonly cfg = inject(DemoConfig).value;
  protected readonly lang = signal('en');
  protected readonly menuOpen = signal(false);
  protected readonly log = signal<string[]>([]);
  private readonly tours = inject(SteppyTours);
  private readonly translate = inject(TranslateService);
  private readonly router = inject(Router);
  private readonly document = inject(DOCUMENT);

  protected readonly state = computed(() => {
    const at = this.tours.current();
    if (!this.tours.active()) return 'idle';
    return at ? `active ${at.tourId} ${at.stepId} ${at.index + 1}/${at.total}` : 'active';
  });

  constructor() {
    this.tours.events
      .pipe(takeUntilDestroyed())
      .subscribe((e) => this.log.update((lines) => [`${e.type} ${JSON.stringify(e.detail)}`, ...lines]));
    // Some apps start a tour for new users on load: start() waits for the overlay.
    const auto = this.cfg().autoStart;
    if (auto) afterNextRender(() => void this.tours.start(auto));
  }

  protected setLang(lang: string): void {
    this.lang.set(lang);
    this.document.documentElement.lang = lang;
    this.translate.use(lang);
  }

  /** From the list, the send tour starts at an invoice: its entry route needs one. */
  protected startSend(): void {
    const onDetail = /^\/invoices\/[^/]+/.test(this.router.url);
    void this.tours.start('send-invoice', onDetail ? {} : { params: { invoiceId: '1001' } });
  }
}
