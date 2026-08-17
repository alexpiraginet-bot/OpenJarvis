import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { LoginScreen, getLoginPresence } from './LoginScreen';

describe('Login Jarvis presence', () => {
  it('maps account creation waits to an explicit profile-building state', () => {
    expect(getLoginPresence('register', true)).toEqual({
      state: 'loading',
      label: 'Construindo seu perfil Jarvis',
    });
    expect(getLoginPresence('login', true)).toEqual({
      state: 'loading',
      label: 'Sincronizando seu perfil Jarvis',
    });
  });

  it('uses the shared optimized neural core at the identity gate', () => {
    const markup = renderToStaticMarkup(
      createElement(LoginScreen, { onAuth: vi.fn() }),
    );

    expect(markup).toContain('data-presence="idle"');
    expect(markup).toContain('/assets/aether-neural-core-768.jpg');
    expect(markup).not.toContain('/aether-neural-core.png');
  });
});
