import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { SteppyTourStart } from './tour-start.directive';
import { SteppyTours } from './tours.service';

@Component({
  template: `
    <button id="plain" steppyTourStart="create-invoice">Help</button>
    <button id="full" [steppyTourStart]="'send-invoice'" [steppyTourParams]="{ invoiceId: 1001 }" steppyTourStep="send-to">
      Help
    </button>
  `,
  imports: [SteppyTourStart],
})
class Page {}

describe('steppyTourStart', () => {
  it('starts the tour on click, with its params and step', async () => {
    const start = vi.spyOn(TestBed.inject(SteppyTours), 'start').mockResolvedValue(true);
    const fixture = TestBed.createComponent(Page);
    await fixture.whenStable();
    fixture.nativeElement.querySelector('#plain').click();
    fixture.nativeElement.querySelector('#full').click();
    expect(start.mock.calls).toEqual([
      ['create-invoice', {}],
      ['send-invoice', { params: { invoiceId: 1001 }, step: 'send-to' }],
    ]);
  });
});
