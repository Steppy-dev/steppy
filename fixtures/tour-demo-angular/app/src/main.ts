import { NgZone } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';

import { App } from './app/app';
import { appConfig } from './app/app.config';

// Test aids, read by tests/integration/test_tour_angular_it.py: how many full
// page loads this tab saw (an entry route must go through the router, not
// reload), and how many change-detection turns the zone ran (the overlay's
// polls and observers must not add any).
try {
  sessionStorage.setItem('demo:loads', String(Number(sessionStorage.getItem('demo:loads') || 0) + 1));
} catch {
  // Counting page loads is only a test aid.
}

bootstrapApplication(App, appConfig)
  .then((ref) => {
    const w = window as unknown as { __demoZoneTurns: number };
    w.__demoZoneTurns = 0;
    ref.injector.get(NgZone).onMicrotaskEmpty.subscribe(() => w.__demoZoneTurns++);
  })
  .catch((err) => console.error(err));
