/**
 * The home screen — greeting, a Today widget, and the app grid.
 *
 * Tapping an icon does not navigate: it asks the shell to open that app in
 * place, so the whole product stays one surface.
 */

import {
  Briefcase,
  Dumbbell,
  Heart,
  LogOut,
  Repeat,
  Sparkles,
  Wallet,
} from 'lucide-react';
import type { ComponentType } from 'react';
import type { AppId, Today } from './types';
import { Empty, formatLongDate, formatMoney, Spinner } from './ui';

const ICONS: Record<AppId, ComponentType<{ size?: number; color?: string }>> = {
  finance: Wallet,
  fitness: Dumbbell,
  routine: Repeat,
  family: Heart,
  work: Briefcase,
};

const LABELS: Record<AppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
};

const ORDER: AppId[] = ['finance', 'fitness', 'routine', 'family', 'work'];

/** How many alerts the widget shows before it stops being a glance. */
const WIDGET_ALERT_LIMIT = 4;

export function Springboard({
  today,
  loading,
  error,
  userName,
  onOpenApp,
  onAskJarvis,
  onLogout,
}: {
  today: Today | null;
  loading: boolean;
  error: string;
  userName: string;
  onOpenApp: (app: AppId) => void;
  onAskJarvis: () => void;
  onLogout: () => void;
}) {
  if (loading && !today) return <Spinner />;

  const alerts = today?.alerts ?? [];
  const visible = alerts.slice(0, WIDGET_ALERT_LIMIT);

  return (
    <>
      <div className="oj-scroll">
        <div className="oj-home">
          <header className="oj-home-head">
            <div>
              <h1 className="oj-greeting">{today?.greeting ?? `Olá, ${userName}`}</h1>
              <div className="oj-date">
                {today ? formatLongDate(today.date) : ''}
              </div>
            </div>
            <button
              type="button"
              className="oj-hud-btn"
              onClick={onLogout}
              aria-label="Sair da conta"
            >
              <LogOut size={18} />
            </button>
          </header>

          {error && <div className="oj-error">{error}</div>}

          <section className="oj-widget" aria-label="Resumo de hoje">
            <div className="oj-widget-title">
              <span>Hoje</span>
              {alerts.length > WIDGET_ALERT_LIMIT && (
                <span>+{alerts.length - WIDGET_ALERT_LIMIT}</span>
              )}
            </div>

            {visible.length === 0 ? (
              <Empty>Tudo em dia. Nada precisa de você agora.</Empty>
            ) : (
              visible.map((alert) => (
                <button
                  key={`${alert.app}-${alert.record_id}-${alert.title}`}
                  type="button"
                  className="oj-alert"
                  onClick={() => onOpenApp(alert.app)}
                >
                  <span className={`oj-dot oj-dot--${alert.severity}`} />
                  <span className="oj-alert-body">
                    <span className="oj-alert-title">{alert.title}</span>
                    <span className="oj-alert-detail">{alert.detail}</span>
                  </span>
                  {alert.amount_cents > 0 && (
                    <span className="oj-alert-amount">
                      {formatMoney(alert.amount_cents, today?.currency)}
                    </span>
                  )}
                </button>
              ))
            )}
          </section>

          <nav className="oj-grid" aria-label="Aplicativos">
            {ORDER.map((app) => {
              const Icon = ICONS[app];
              const badge = today?.badges?.[app] ?? 0;
              return (
                <button
                  key={app}
                  type="button"
                  className="oj-app"
                  onClick={() => onOpenApp(app)}
                  aria-label={
                    badge > 0
                      ? `${LABELS[app]}, ${badge} pendência(s)`
                      : LABELS[app]
                  }
                >
                  <span className={`oj-icon oj-icon--${app}`}>
                    <Icon size={30} color="#fff" />
                    {badge > 0 && (
                      <span className="oj-badge">{badge > 99 ? '99+' : badge}</span>
                    )}
                  </span>
                  <span className="oj-app-label">{LABELS[app]}</span>
                </button>
              );
            })}

            <button
              type="button"
              className="oj-app"
              onClick={onAskJarvis}
              aria-label="Conversar com o Jarvis"
            >
              <span className="oj-icon oj-icon--jarvis">
                <Sparkles size={30} color="#fff" />
              </span>
              <span className="oj-app-label">Jarvis</span>
            </button>
          </nav>
        </div>
      </div>

      <button type="button" className="oj-dock" onClick={onAskJarvis}>
        <Sparkles size={18} />
        <span>Perguntar ao Jarvis…</span>
      </button>
    </>
  );
}
