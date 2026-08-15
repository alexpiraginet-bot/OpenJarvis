import { describe, expect, it } from 'vitest';
import { initialMobileShellState, mobileShellReducer } from './mobileShell';

describe('unified mobile shell', () => {
  it('starts on the Springboard with no separate Jarvis navigation layer', () => {
    expect(initialMobileShellState).toEqual({
      openApp: null,
      assistantOpen: false,
      assistantContext: null,
    });
  });

  it('opens Jarvis inline over the life overview', () => {
    const state = mobileShellReducer(initialMobileShellState, {
      type: 'open_assistant',
    });

    expect(state).toEqual({
      openApp: null,
      assistantOpen: true,
      assistantContext: null,
    });
  });

  it('closes an app and carries its specialist context into inline Jarvis', () => {
    const finance = mobileShellReducer(initialMobileShellState, {
      type: 'open_app',
      app: 'finance',
    });
    const jarvis = mobileShellReducer(finance, {
      type: 'open_assistant',
      context: 'finance',
    });

    expect(jarvis).toEqual({
      openApp: null,
      assistantOpen: true,
      assistantContext: 'finance',
    });
  });

  it('clears specialist context when the panel is closed', () => {
    const state = mobileShellReducer(
      {
        openApp: null,
        assistantOpen: true,
        assistantContext: 'health',
      },
      { type: 'close_assistant' },
    );

    expect(state).toEqual(initialMobileShellState);
  });
});
