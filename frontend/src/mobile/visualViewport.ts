export interface VisualViewportLike {
  height: number;
  offsetTop: number;
  addEventListener: (type: 'resize' | 'scroll', listener: EventListener) => void;
  removeEventListener: (type: 'resize' | 'scroll', listener: EventListener) => void;
}

export interface SafeAreaInsets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

interface MobileViewportOptions {
  nativeShell?: boolean;
  safeAreaInsets?: SafeAreaInsets;
}

const ZERO_SAFE_AREA: SafeAreaInsets = { top: 0, right: 0, bottom: 0, left: 0 };

export function hasJarvisNativeBridge(
  scope: unknown = typeof window === 'undefined' ? undefined : window,
): boolean {
  const handlers = (
    scope as {
      webkit?: { messageHandlers?: Record<string, unknown> };
    } | undefined
  )?.webkit?.messageHandlers;
  return Boolean(handlers?.jarvisVoice || handlers?.jarvisIntegrations);
}

export function measureCssSafeAreaInsets(): SafeAreaInsets {
  if (typeof document === 'undefined' || typeof getComputedStyle === 'undefined') {
    return ZERO_SAFE_AREA;
  }

  const probe = document.createElement('div');
  probe.style.position = 'fixed';
  probe.style.visibility = 'hidden';
  probe.style.pointerEvents = 'none';
  probe.style.paddingTop = 'env(safe-area-inset-top, 0px)';
  probe.style.paddingRight = 'env(safe-area-inset-right, 0px)';
  probe.style.paddingBottom = 'env(safe-area-inset-bottom, 0px)';
  probe.style.paddingLeft = 'env(safe-area-inset-left, 0px)';
  document.documentElement.appendChild(probe);
  const computed = getComputedStyle(probe);
  const insets = {
    top: Number.parseFloat(computed.paddingTop) || 0,
    right: Number.parseFloat(computed.paddingRight) || 0,
    bottom: Number.parseFloat(computed.paddingBottom) || 0,
    left: Number.parseFloat(computed.paddingLeft) || 0,
  };
  probe.remove();
  return insets;
}

export function bindMobileVisualViewport(
  target: HTMLElement,
  viewport: VisualViewportLike | null =
    typeof window === 'undefined'
      ? null
      : (window.visualViewport as VisualViewportLike | null),
  options: MobileViewportOptions = {},
) {
  if (!viewport) return () => undefined;

  const nativeShell = options.nativeShell ?? hasJarvisNativeBridge();
  target.dataset.nativeShell = String(nativeShell);

  const sync: EventListener = () => {
    const safeAreaInsets =
      options.safeAreaInsets ?? measureCssSafeAreaInsets();
    target.style.setProperty(
      '--oj-visual-viewport-height',
      `${Math.max(0, viewport.height)}px`,
    );
    target.style.setProperty(
      '--oj-visual-viewport-offset-top',
      `${Math.max(0, viewport.offsetTop)}px`,
    );
    for (const edge of ['top', 'right', 'bottom', 'left'] as const) {
      target.style.setProperty(
        `--oj-safe-area-inset-${edge}`,
        `${Math.max(0, safeAreaInsets[edge])}px`,
      );
    }
    target.dataset.safeAreaResolved = String(
      !nativeShell || safeAreaInsets.top + viewport.offsetTop > 0,
    );
  };

  sync(new Event('resize'));
  viewport.addEventListener('resize', sync);
  viewport.addEventListener('scroll', sync);

  return () => {
    viewport.removeEventListener('resize', sync);
    viewport.removeEventListener('scroll', sync);
    target.style.removeProperty('--oj-visual-viewport-height');
    target.style.removeProperty('--oj-visual-viewport-offset-top');
    for (const edge of ['top', 'right', 'bottom', 'left'] as const) {
      target.style.removeProperty(`--oj-safe-area-inset-${edge}`);
    }
    delete target.dataset.nativeShell;
    delete target.dataset.safeAreaResolved;
  };
}
