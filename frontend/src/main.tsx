import { lazy, StrictMode, Suspense, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import '@fontsource-variable/geist';
import { ErrorBoundary } from './components/ErrorBoundary';
import './index.css';

const OperatorApp = lazy(() => import('./App'));
const MobileApp = lazy(() => import('./mobile/MobileApp'));

/**
 * The client app and the operator console are two products sharing one build.
 *
 * Splitting them at the root rather than at a route matters: `App` mounts the
 * research console's chrome — the telemetry opt-in modal, the update checker,
 * the command palette — and polls models and savings on an interval. A paying
 * client must never be asked to share usage data with a leaderboard, and their
 * phone should not run a dashboard's fetch loop in the background.
 */
const isClientApp =
  typeof window !== 'undefined' && window.location.pathname.startsWith('/vida');

function applyTheme() {
  try {
    const raw = localStorage.getItem('openjarvis-settings');
    const settings = raw ? JSON.parse(raw) : {};
    const theme = settings.theme || 'system';
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else if (theme === 'light') {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  } catch { /* use system default */ }
}

applyTheme();

// Fetch the API base URL from the Tauri backend before rendering.
// This ensures JARVIS_PORT is defined in one place (the Rust backend).
// In non-Tauri environments this is a no-op.
function mount(content: ReactNode) {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <Suspense fallback={<div className="oj-boot" aria-label="Carregando" />}>
          {content}
        </Suspense>
      </ErrorBoundary>
    </StrictMode>,
  );
}

if (isClientApp) {
  // No Tauri probe and no analytics: the client app talks to one origin over
  // relative paths, and a personal-assistant install is not a research
  // datapoint. Mounting straight away also removes a network round trip from
  // the phone's cold start.
  mount(<MobileApp />);
} else {
  // The operator console is a separate lazy chunk. None of its dashboards,
  // charting, markdown or analytics code is downloaded by the iPhone app.
  void Promise.all([import('./lib/api'), import('./lib/analytics')]).then(
    ([{ initApiBase }, { initAnalytics }]) => {
      initApiBase().finally(() => {
        // Kick off analytics init in the background — it's never awaited so
        // a slow/failed identity fetch never delays UI render.
        void initAnalytics();
        mount(
          <BrowserRouter>
            <OperatorApp />
          </BrowserRouter>,
        );
      });
    },
  );
}
