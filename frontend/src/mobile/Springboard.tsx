/**
 * The home screen — greeting, a Today widget, and the app grid.
 *
 * Tapping an icon does not navigate: it asks the shell to open that app in
 * place, so the whole product stays one surface.
 */

import {
  Activity,
  Briefcase,
  BrainCircuit,
  ChevronRight,
  Dumbbell,
  Heart,
  LogOut,
  Repeat,
  Satellite,
  Wallet,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { ConnectionsPanel } from './apps/ConnectionsApp';
import type { AppId, ShellAppId, Today } from './types';
import { Empty, formatLongDate, formatMoney, Spinner } from './ui';

const ICONS: Record<ShellAppId, ComponentType<{ size?: number; color?: string }>> = {
  finance: Wallet,
  fitness: Dumbbell,
  routine: Repeat,
  family: Heart,
  work: Briefcase,
  connections: Satellite,
};

const LABELS: Record<ShellAppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
  connections: 'Conexões',
};

const ORDER: AppId[] = ['finance', 'fitness', 'routine', 'family', 'work'];
const ORBIT_ORDER: ShellAppId[] = [
  'work',
  'fitness',
  'routine',
  'connections',
  'finance',
  'family',
];

const SPECIALISTS: Record<ShellAppId, string> = {
  finance: 'Diretor financeiro',
  fitness: 'Coach de performance',
  routine: 'Chefe de gabinete',
  family: 'Concierge familiar',
  work: 'Assistente executivo',
  connections: 'Engenheiro de integrações',
};

const ORBIT_CODES: Record<ShellAppId, string> = {
  finance: 'FN-01',
  fitness: 'PF-02',
  routine: 'RT-03',
  family: 'FM-04',
  work: 'EX-05',
  connections: 'NX-06',
};

/** How many alerts stay visible below the orbital system. */
const WIDGET_ALERT_LIMIT = 2;

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
  onOpenApp: (app: ShellAppId) => void;
  onAskJarvis: () => void;
  onLogout: () => void;
}) {
  if (loading && !today) return <Spinner />;

  const alerts = today?.alerts ?? [];
  const visible = alerts.slice(0, WIDGET_ALERT_LIMIT);
  const pendingTotal = today
    ? Object.values(today.badges).reduce((total, count) => total + count, 0)
    : 0;

  return (
    <div className="oj-scroll oj-home-scroll">
      <main className="oj-home">
        <header className="oj-home-head">
          <div>
            <span className="oj-home-kicker">
              <span className="oj-system-led" /> Jarvis Life / sistema ativo
            </span>
            <h1 className="oj-greeting">{today?.greeting ?? `Olá, ${userName}`}</h1>
            <div className="oj-date">{today ? formatLongDate(today.date) : ''}</div>
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

        <section className="oj-system-strip" aria-label="Estado do sistema">
          <div className="oj-system-metric">
            <span>PENDÊNCIAS</span>
            <strong>{pendingTotal}</strong>
          </div>
          <div className="oj-system-metric">
            <span>ESPECIALISTAS</span>
            <strong>{String(ORDER.length).padStart(2, '0')}</strong>
          </div>
          <div className="oj-system-metric oj-system-metric--online">
            <span>NÚCLEO</span>
            <strong><Activity size={14} /> ATIVO</strong>
          </div>
        </section>

        {today && (
          <section className="oj-life-overview" aria-label="Visão 360 da sua vida">
            <div className="oj-life-overview-head">
              <span>Visão 360</span>
              <small>dados de hoje</small>
            </div>
            <div className="oj-life-overview-track">
              <button type="button" onClick={() => onOpenApp('finance')}>
                <span><Wallet size={15} /> Finanças</span>
                <strong>{formatMoney(today.finance.balance_cents, today.currency)}</strong>
                <small>{formatMoney(today.finance.expense_cents, today.currency)} em saídas</small>
              </button>
              <button type="button" onClick={() => onOpenApp('fitness')}>
                <span><Dumbbell size={15} /> Treino</span>
                <strong>
                  {today.fitness.todays_workout?.name ??
                    `${today.fitness.week_completed}/${today.fitness.week_planned} concluídos`}
                </strong>
                <small>
                  {today.fitness.todays_workout
                    ? 'planejado para hoje'
                    : 'progresso da semana'}
                </small>
              </button>
              <button type="button" onClick={() => onOpenApp('routine')}>
                <span><Repeat size={15} /> Rotina</span>
                <strong>{today.routine.completed}/{today.routine.total}</strong>
                <small>hábitos concluídos hoje</small>
              </button>
              <button type="button" onClick={() => onOpenApp('work')}>
                <span><Briefcase size={15} /> Trabalho</span>
                <strong>{today.work.due_today_count}</strong>
                <small>{today.work.open_count} tarefas abertas</small>
              </button>
              <button type="button" onClick={() => onOpenApp('family')}>
                <span><Heart size={15} /> Família</span>
                <strong>{today.family.upcoming[0]?.title ?? 'Tudo alinhado'}</strong>
                <small>
                  {today.family.upcoming[0]
                    ? today.family.upcoming[0].days_away === 0
                      ? 'acontece hoje'
                      : `em ${today.family.upcoming[0].days_away} dia(s)`
                    : 'nenhum evento próximo'}
                </small>
              </button>
            </div>
          </section>
        )}

        <nav className="oj-orbit" aria-label="Aplicativos conectados ao Jarvis">
          <span className="oj-orbit-track oj-orbit-track--outer" aria-hidden="true" />
          <span className="oj-orbit-track oj-orbit-track--mid" aria-hidden="true" />
          <span className="oj-orbit-track oj-orbit-track--inner" aria-hidden="true" />
          <span className="oj-orbit-crosshair" aria-hidden="true" />
          <span className="oj-orbit-sweep" aria-hidden="true" />

          <button
            type="button"
            className="oj-orbit-core"
            onClick={onAskJarvis}
            aria-label="Conversar com o Jarvis"
          >
            <img src="/aether-neural-core.png" alt="" />
            <span className="oj-orbit-core-pulse" aria-hidden="true" />
            <span className="oj-orbit-core-label">
              <strong>JARVIS</strong>
              <small>NEURAL CORE</small>
            </span>
          </button>

          {ORBIT_ORDER.map((app) => {
            const Icon = ICONS[app];
            const badge = app === 'connections' ? 0 : (today?.badges?.[app] ?? 0);
            return (
              <button
                key={app}
                type="button"
                className={`oj-orbit-app oj-orbit-app--${app}`}
                onClick={() => onOpenApp(app)}
                aria-label={
                  badge > 0
                    ? `${LABELS[app]}, ${badge} pendência(s)`
                    : LABELS[app]
                }
              >
                <span className="oj-orbit-node">
                  <Icon size={26} />
                  <span className="oj-orbit-code" aria-hidden="true">
                    {ORBIT_CODES[app]}
                  </span>
                  {badge > 0 && (
                    <span className="oj-badge">{badge > 99 ? '99+' : badge}</span>
                  )}
                </span>
                <span className="oj-orbit-label">{LABELS[app]}</span>
                <span className="oj-orbit-role">{SPECIALISTS[app]}</span>
              </button>
            );
          })}
        </nav>

        <section className="oj-specialist-panel" aria-label="Conselho de especialistas">
          <div className="oj-panel-heading">
            <span><BrainCircuit size={15} /> Conselho Jarvis</span>
            <small>{ORDER.length} especialistas ativos</small>
          </div>
          <div className="oj-specialist-list">
            {ORDER.map((app) => {
              const Icon = ICONS[app];
              return (
                <button
                  key={`specialist-${app}`}
                  type="button"
                  className="oj-specialist-row"
                  onClick={() => onOpenApp(app)}
                >
                  <span className="oj-specialist-icon"><Icon size={17} /></span>
                  <span>
                    <strong>{SPECIALISTS[app]}</strong>
                    <small>{LABELS[app]}</small>
                  </span>
                  <span className="oj-specialist-status">IA</span>
                  <ChevronRight size={15} />
                </button>
              );
            })}
          </div>
        </section>

        <ConnectionsPanel
          onOpen={() => onOpenApp('connections')}
          refreshSignal={today}
        />

        <section className="oj-widget" aria-label="Resumo de hoje">
          <div className="oj-widget-title">
            <span>Sinais de hoje</span>
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
      </main>
    </div>
  );
}
