import { describe, expect, it } from 'vitest';
import { buildAppPresentationMotion } from './appMotion';

describe('app presentation motion', () => {
  it('keeps Home visible while a forward app sheet enters with transform-only motion', () => {
    const motion = buildAppPresentationMotion(false, true);

    expect(motion.shell.animate).toEqual({
      opacity: 0.72,
      x: -10,
      y: 3,
      scale: 0.975,
    });
    expect(motion.sheet.initial).toEqual({
      opacity: 0,
      x: '16%',
      y: 18,
      scale: 0.965,
    });
    expect(motion.sheet.animate).toEqual({
      opacity: 1,
      x: 0,
      y: 0,
      scale: 1,
    });
    expect(motion.sheet.exit).toEqual({
      opacity: 0,
      x: '9%',
      y: 10,
      scale: 0.982,
    });

    for (const frame of [
      motion.shell.animate,
      motion.sheet.initial,
      motion.sheet.animate,
      motion.sheet.exit,
    ]) {
      expect(frame).not.toHaveProperty('top');
      expect(frame).not.toHaveProperty('left');
      expect(frame).not.toHaveProperty('width');
      expect(frame).not.toHaveProperty('height');
      expect(frame).not.toHaveProperty('filter');
    }
  });

  it('removes animated travel when reduced motion is requested', () => {
    const open = buildAppPresentationMotion(true, true);
    const closed = buildAppPresentationMotion(true, false);

    expect(open.shell.animate).toEqual({ opacity: 0.82 });
    expect(closed.shell.animate).toEqual({ opacity: 1 });
    expect(open.shell.transition).toEqual({ duration: 0 });
    expect(open.sheet.initial).toBe(false);
    expect(open.sheet.exit).toEqual({ opacity: 0 });
    expect(open.sheet.transition).toEqual({ duration: 0 });
  });
});
