import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { ErrorBoundary } from './components/ErrorBoundary';
import App from './App';
import MobileApp from './mobile/MobileApp';
import { initApiBase } from './lib/api';
import { initAnalytics } from './lib/analytics';
import './index.css';

/**
 * The client app and the operator console are two products sharing one bundle.
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
function mount() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        {isClientApp ? (
          <MobileApp />
        ) : (
          <BrowserRouter>
            <App />
          </BrowserRouter>
        )}
      </ErrorBoundary>
    </StrictMode>,
  );
}

if (isClientApp) {
  // No Tauri probe and no analytics: the client app talks to one origin over
  // relative paths, and a personal-assistant install is not a research
  // datapoint. Mounting straight away also removes a network round trip from
  // the phone's cold start.
  mount();
} else {
  initApiBase().finally(() => {
    // Kick off analytics init in the background — it's never awaited so
    // a slow/failed identity fetch never delays UI render.
    void initAnalytics();
    mount();
  });
}
