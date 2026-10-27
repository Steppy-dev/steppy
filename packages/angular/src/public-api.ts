/*
 * @steppy/angular — the Angular adapter for the Steppy in-app tour overlay.
 */

export { provideSteppyTours } from './provide';
export type { SteppyLocaleSource, SteppyNavigate, SteppyToursConfig } from './config';
export { SteppyTours, type SteppyTourEvent } from './tours.service';
export { SteppyToursComponent } from './tours.component';
export { SteppyTourStart } from './tour-start.directive';
export { routerNavigate } from './navigate';

// Types only: erased at build time, so nothing from the overlay loads on the server.
export type {
  TargetClickMode,
  TourAppearance,
  TourErrorDetail,
  TourEventDetailMap,
  TourNavigateContext,
  TourPosition,
  TourSource,
  TourStartOptions,
  TourStringOverrides,
  TourTheme,
} from '@steppy/player/tour';
