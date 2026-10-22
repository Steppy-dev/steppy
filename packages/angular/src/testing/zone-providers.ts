import { provideZoneChangeDetection } from '@angular/core';

// The zone suite runs every spec as a zone.js app, like most apps in the field.
export default [provideZoneChangeDetection()];
