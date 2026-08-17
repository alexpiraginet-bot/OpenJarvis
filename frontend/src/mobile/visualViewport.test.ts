import { describe, expect, it, vi } from 'vitest';
import { bindMobileVisualViewport, type VisualViewportLike } from './visualViewport';

function createViewport(height = 844, offsetTop = 59) {
  const listeners = new Map<string, Set<EventListener>>();
  const viewport: VisualViewportLike & { emit: (type: 'resize' | 'scroll') => void } = {
    height,
    offsetTop,
    addEventListener(type, listener) {
      const registered = listeners.get(type) ?? new Set<EventListener>();
      registered.add(listener);
      listeners.set(type, registered);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    emit(type) {
      listeners.get(type)?.forEach((listener) => listener(new Event(type)));
    },
  };

  return { viewport, listeners };
}

function createTarget() {
  const properties = new Map<string, string>();
  const target = {
    dataset: {} as DOMStringMap,
    style: {
      setProperty: vi.fn((name: string, value: string) => properties.set(name, value)),
      removeProperty: vi.fn((name: string) => properties.delete(name)),
    },
  } as unknown as HTMLElement;

  return { target, properties };
}

describe('mobile visual viewport binding', () => {
  it('publishes the usable viewport height and top offset as CSS pixels', () => {
    const { viewport } = createViewport();
    const { target, properties } = createTarget();

    bindMobileVisualViewport(target, viewport);

    expect(properties.get('--oj-visual-viewport-height')).toBe('844px');
    expect(properties.get('--oj-visual-viewport-offset-top')).toBe('59px');
  });

  it('keeps the CSS viewport contract current across resize and scroll events', () => {
    const { viewport } = createViewport();
    const { target, properties } = createTarget();

    bindMobileVisualViewport(target, viewport);
    viewport.height = 430.5;
    viewport.offsetTop = 12;
    viewport.emit('resize');

    expect(properties.get('--oj-visual-viewport-height')).toBe('430.5px');
    expect(properties.get('--oj-visual-viewport-offset-top')).toBe('12px');

    viewport.offsetTop = 18;
    viewport.emit('scroll');
    expect(properties.get('--oj-visual-viewport-offset-top')).toBe('18px');
  });

  it('removes listeners and transient CSS properties when detached', () => {
    const { viewport, listeners } = createViewport();
    const { target, properties } = createTarget();
    const detach = bindMobileVisualViewport(target, viewport);

    detach();

    expect(listeners.get('resize')).toHaveLength(0);
    expect(listeners.get('scroll')).toHaveLength(0);
    expect(properties.has('--oj-visual-viewport-height')).toBe(false);
    expect(properties.has('--oj-visual-viewport-offset-top')).toBe(false);
  });

  it('publishes measured native safe areas instead of assuming env is non-zero', () => {
    const { viewport } = createViewport(932, 0);
    const { target, properties } = createTarget();

    bindMobileVisualViewport(target, viewport, {
      nativeShell: true,
      safeAreaInsets: { top: 59, right: 0, bottom: 34, left: 0 },
    });

    expect(target.dataset.nativeShell).toBe('true');
    expect(target.dataset.safeAreaResolved).toBe('true');
    expect(properties.get('--oj-safe-area-inset-top')).toBe('59px');
    expect(properties.get('--oj-safe-area-inset-bottom')).toBe('34px');
  });

  it('marks a native zero-inset viewport as unresolved without inventing pixels', () => {
    const { viewport } = createViewport(932, 0);
    const { target, properties } = createTarget();

    bindMobileVisualViewport(target, viewport, {
      nativeShell: true,
      safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
    });

    expect(target.dataset.safeAreaResolved).toBe('false');
    expect(properties.get('--oj-safe-area-inset-top')).toBe('0px');
  });
});
