import { Directive, inject, input } from '@angular/core';
import type { TourStartOptions } from '@steppy/player/tour';

import { SteppyTours } from './tours.service';

/**
 * Start a tour on click:
 *
 * ```html
 * <button steppyTourStart="create-invoice">Show me how</button>
 * <button [steppyTourStart]="'send-invoice'" [steppyTourParams]="{ invoiceId: id }">Help</button>
 * ```
 */
@Directive({
  selector: '[steppyTourStart]',
  host: { '(click)': 'onClick()' },
})
export class SteppyTourStart {
  readonly steppyTourStart = input.required<string>();
  readonly steppyTourParams = input<Record<string, string | number>>();
  readonly steppyTourStep = input<string>();

  private readonly tours = inject(SteppyTours);

  protected onClick(): void {
    const options: TourStartOptions = {};
    const params = this.steppyTourParams();
    const step = this.steppyTourStep();
    if (params) options.params = params;
    if (step) options.step = step;
    void this.tours.start(this.steppyTourStart(), options);
  }
}
