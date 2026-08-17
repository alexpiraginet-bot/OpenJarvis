import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import {
  JarvisPresence,
  createPresenceFrameLoop,
  createWebGLPresenceRenderer,
  getPresenceStatusLabel,
  schedulePresenceBootFallback,
} from './JarvisPresence';

describe('JarvisPresence', () => {
  it('renders a lightweight live progress state for internal waits', () => {
    const markup = renderToStaticMarkup(
      createElement(JarvisPresence, {
        state: 'loading',
        variant: 'compact',
        label: 'Construindo seu perfil Jarvis',
      }),
    );

    expect(markup).toContain('data-presence="loading"');
    expect(markup).toContain('data-renderer="static"');
    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('Construindo seu perfil Jarvis');
    expect(markup).toContain('/assets/aether-neural-core-768.jpg');
    expect(markup).not.toContain('<canvas');
  });

  it('keeps a real asset fallback behind the WebGL presence', () => {
    const markup = renderToStaticMarkup(
      createElement(JarvisPresence, {
        state: 'listening',
        variant: 'hero',
      }),
    );

    expect(markup).toContain('data-presence="listening"');
    expect(markup).toContain('data-renderer="boot"');
    expect(markup).toContain('/assets/aether-neural-core-768.jpg');
    expect(markup).toContain('<canvas');
    expect(markup).toContain('Ouvindo você');
  });

  it('falls back when WebGL is unavailable', () => {
    const canvas = {
      getContext: vi.fn(() => null),
    } as unknown as HTMLCanvasElement;

    expect(
      createWebGLPresenceRenderer(
        canvas,
        {} as TexImageSource,
        vi.fn(),
      ),
    ).toBeNull();
  });

  it('announces renderer fallback even when the caller supplies an idle label', () => {
    expect(
      getPresenceStatusLabel('Núcleo Jarvis pronto', 'idle', 'fallback'),
    ).toBe('Núcleo Jarvis pronto. Modo visual simplificado.');
  });

  it('times out boot and cancels the pending fallback on cleanup', () => {
    const fallback = vi.fn();
    const clear = vi.fn();
    let scheduled: (() => void) | null = null;
    const schedule = vi.fn((callback: () => void) => {
      scheduled = callback;
      return 17;
    });

    const cleanup = schedulePresenceBootFallback(fallback, {
      schedule,
      clear,
    });
    expect(schedule).toHaveBeenCalledWith(expect.any(Function), 5000);
    const runScheduled = scheduled as (() => void) | null;
    if (runScheduled) runScheduled();
    expect(fallback).toHaveBeenCalledWith('timeout');

    cleanup();
    expect(clear).toHaveBeenCalledWith(17);
  });

  it('draws once with reduced motion and cancels continuous frames', () => {
    const draw = vi.fn();
    const requestFrame = vi.fn(() => 23);
    const cancelFrame = vi.fn();

    const reducedCleanup = createPresenceFrameLoop({
      draw,
      reduceMotion: true,
      requestFrame,
      cancelFrame,
    });
    expect(draw).toHaveBeenCalledOnce();
    expect(requestFrame).not.toHaveBeenCalled();
    reducedCleanup();

    draw.mockClear();
    const cleanup = createPresenceFrameLoop({
      draw,
      reduceMotion: false,
      requestFrame,
      cancelFrame,
    });
    expect(requestFrame).toHaveBeenCalledOnce();
    cleanup();
    expect(cancelFrame).toHaveBeenCalledWith(23);
  });
});
