import { describe, expect, it } from 'vitest';
import { shouldDismissVerticalDrag } from './dragDismiss';

describe('vertical drag-to-dismiss', () => {
  it('dismisses after a deliberate downward drag', () => {
    expect(
      shouldDismissVerticalDrag({
        offsetY: 120,
        velocityY: 120,
        viewportHeight: 932,
      }),
    ).toBe(true);
  });

  it('dismisses a short but intentional downward flick', () => {
    expect(
      shouldDismissVerticalDrag({
        offsetY: 28,
        velocityY: 900,
        viewportHeight: 932,
      }),
    ).toBe(true);
  });

  it.each([
    { offsetY: -160, velocityY: -900, viewportHeight: 932 },
    { offsetY: 62, velocityY: 180, viewportHeight: 932 },
    { offsetY: 5, velocityY: 980, viewportHeight: 932 },
  ])('ignores scroll, upward movement and incidental taps: %o', (gesture) => {
    expect(shouldDismissVerticalDrag(gesture)).toBe(false);
  });
});
