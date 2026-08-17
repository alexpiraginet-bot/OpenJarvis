import { describe, expect, it } from 'vitest';
import {
  JARVIS_PRESENCE_STATES,
  deriveJarvisPresenceState,
  getJarvisPresenceCopy,
  presenceNeedsProgress,
} from './jarvisPresenceState';

describe('Jarvis presence state', () => {
  it('defines every product state with accessible Portuguese copy', () => {
    expect(JARVIS_PRESENCE_STATES).toEqual([
      'boot',
      'idle',
      'loading',
      'listening',
      'thinking',
      'executing',
      'speaking',
      'success',
      'error',
      'fallback',
    ]);

    for (const state of JARVIS_PRESENCE_STATES) {
      expect(getJarvisPresenceCopy(state).label.trim()).not.toBe('');
      expect(getJarvisPresenceCopy(state).color).toMatch(/^#[\da-f]{6}$/i);
    }
  });

  it.each([
    [{ initializing: true }, 'boot'],
    [{ profileBuilding: true }, 'loading'],
    [{ listening: true }, 'listening'],
    [{ thinking: true }, 'thinking'],
    [{ executing: true }, 'executing'],
    [{ speaking: true }, 'speaking'],
    [{ success: true }, 'success'],
    [{ error: true }, 'error'],
    [{ fallback: true }, 'fallback'],
    [{}, 'idle'],
  ] as const)('derives %s as %s', (signals, expected) => {
    expect(deriveJarvisPresenceState(signals)).toBe(expected);
  });

  it('lets a newer operation interrupt an older visual transition', () => {
    expect(
      deriveJarvisPresenceState({
        profileBuilding: true,
        listening: true,
        thinking: true,
      }),
    ).toBe('listening');
    expect(
      deriveJarvisPresenceState({
        listening: true,
        executing: true,
        success: true,
      }),
    ).toBe('executing');
    expect(
      deriveJarvisPresenceState({
        executing: true,
        error: true,
      }),
    ).toBe('error');
  });

  it('marks only indeterminate work states as progressing', () => {
    expect(JARVIS_PRESENCE_STATES.filter(presenceNeedsProgress)).toEqual([
      'boot',
      'loading',
      'thinking',
      'executing',
    ]);
  });
});
