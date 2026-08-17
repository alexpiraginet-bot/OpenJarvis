/**
 * The shell — one surface, three layers.
 *
 * The springboard is the home screen and the Jarvis controls expand inside it.
 * App windows are the only layer above that surface. Nothing here changes the
 * route, so the whole product behaves like a phone rather than a website: you
 * never leave, you go deeper and come back.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  AnimatePresence,
  motion,
  useDragControls,
  useReducedMotion,
} from 'motion/react';
import { AppWindow, type Tab } from './AppWindow';
import { fetchMe, fetchToday, getToken, logout } from './api';
import {
  ConnectionsAbout,
  ConnectionsTab,
} from './apps/ConnectionsApp';
import {
  BillsTab,
  FinancialDocumentsTab,
  FinanceOverview,
  GoalsTab,
  TransactionsTab,
} from './apps/FinanceApp';
import {
  CoachPlan,
  CoachProfile,
  CoachToday,
  FitnessProgress,
} from './apps/FitnessApp';
import { PeopleTab, UpcomingTab } from './apps/FamilyApp';
import {
  HealthDocumentsTab,
  HealthOverview,
  HealthProfileTab,
  HealthRecordsTab,
} from './apps/HealthApp';
import { HabitsToday, ManageHabits } from './apps/RoutineApp';
import { ProjectsTab, TasksTab } from './apps/WorkApp';
import { JarvisCore } from './JarvisCore';
import { LoginScreen } from './LoginScreen';
import { buildAppPresentationMotion } from './appMotion';
import { shouldDismissVerticalDrag } from './dragDismiss';
import { initialMobileShellState, mobileShellReducer } from './mobileShell';
import { Springboard } from './Springboard';
import type { LifeUser, ShellAppId, Today } from './types';
import { Button, Spinner } from './ui';
import { bindMobileVisualViewport } from './visualViewport';
import './mobile.css';

const TITLES: Record<ShellAppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
  health: 'Saúde',
  connections: 'Conexões',
};

const SPECIALISTS: Record<ShellAppId, string> = {
  finance: 'Diretor financeiro IA',
  fitness: 'Coach de performance IA',
  routine: 'Chefe de gabinete IA',
  family: 'Concierge familiar IA',
  work: 'Assistente executivo IA',
  health: 'Especialista de saúde IA',
  connections: 'Engenheiro de integrações IA',
};

export default function MobileApp() {
  const reduceMotion = useReducedMotion();
  const appDragControls = useDragControls();
  const mobileRootRef = useRef<HTMLDivElement | null>(null);
  const [user, setUser] = useState<LifeUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [shell, dispatchShell] = useReducer(
    mobileShellReducer,
    initialMobileShellState,
  );
  const [tab, setTab] = useState('');
  const [today, setToday] = useState<Today | null>(null);
  const [loadingToday, setLoadingToday] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!mobileRootRef.current) return;
    return bindMobileVisualViewport(mobileRootRef.current);
  }, []);

  // Restore the session from the stored token before showing anything, so a
  // returning client never sees a login flash.
  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    let active = true;
    fetchMe()
      .then((result) => {
        if (active) setUser(result.user);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(() => {
    if (!getToken()) return;
    setLoadingToday(true);
    fetchToday()
      .then((result) => {
        setToday(result);
        setError('');
      })
      .catch((exc: unknown) => {
        setError(exc instanceof Error ? exc.message : 'Falha ao carregar');
      })
      .finally(() => setLoadingToday(false));
  }, []);

  useEffect(() => {
    if (user) refresh();
  }, [user, refresh]);

  const handleOpenApp = useCallback((app: ShellAppId) => {
    dispatchShell({ type: 'open_app', app });
    setTab('');
  }, []);

  const closeApp = useCallback(() => {
    dispatchShell({ type: 'close_app' });
    // Coming back from an app is the moment badges are most likely stale.
    refresh();
  }, [refresh]);

  async function handleLogout() {
    await logout();
    setUser(null);
    setToday(null);
    dispatchShell({ type: 'reset' });
  }

  if (checking) {
    return (
      <div ref={mobileRootRef} className="oj-mobile" translate="no">
        <Spinner state="boot" label="Inicializando seu Jarvis" />
      </div>
    );
  }

  if (!user) {
    return (
      <div ref={mobileRootRef} className="oj-mobile" translate="no">
        <LoginScreen
          onAuth={(authenticated) => {
            setUser(authenticated);
            dispatchShell({ type: 'reset' });
          }}
        />
      </div>
    );
  }

  const currency = today?.currency ?? user.currency;
  const tabs: Tab[] = shell.openApp
    ? buildTabs(shell.openApp, currency, refresh)
    : [];
  const activeTab = tab || tabs[0]?.id || '';
  const appPresentation = buildAppPresentationMotion(
    Boolean(reduceMotion),
    Boolean(shell.openApp),
  );

  return (
    <div ref={mobileRootRef} className="oj-mobile" translate="no">
      <motion.div
        className="oj-shell-layer"
        data-app-open={Boolean(shell.openApp)}
        aria-hidden={Boolean(shell.openApp)}
        animate={appPresentation.shell.animate}
        transition={appPresentation.shell.transition}
      >
        <Springboard
          today={today}
          loading={loadingToday}
          error={error}
          userName={user.name}
          onOpenApp={handleOpenApp}
          onAskJarvis={() => dispatchShell({ type: 'open_assistant' })}
          assistantOpen={shell.assistantOpen}
          assistantPanel={
            shell.assistantOpen ? (
              <motion.div
                className="oj-jarvis-inline-content"
                initial={reduceMotion ? false : { opacity: 0, y: 18, scale: 0.985 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={
                  reduceMotion
                    ? { duration: 0 }
                    : { type: 'spring', stiffness: 430, damping: 40, mass: 0.78 }
                }
              >
                <JarvisCore
                  today={today}
                  userId={user.id}
                  contextApp={shell.assistantContext}
                  variant="embedded"
                  onClose={() => dispatchShell({ type: 'close_assistant' })}
                  onRefresh={refresh}
                />
              </motion.div>
            ) : null
          }
          onLogout={handleLogout}
        />
      </motion.div>

      <AnimatePresence initial={false}>
        {shell.openApp && (
          <motion.div
            key={shell.openApp}
            className="oj-app-layer"
            role="dialog"
            aria-modal="true"
            aria-label={`${TITLES[shell.openApp]} — ${SPECIALISTS[shell.openApp]}`}
            initial={appPresentation.sheet.initial}
            animate={appPresentation.sheet.animate}
            exit={appPresentation.sheet.exit}
            transition={appPresentation.sheet.transition}
            drag="y"
            dragControls={appDragControls}
            dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.54 }}
            dragMomentum={false}
            onDragEnd={(_event, info) => {
              const viewportHeight =
                window.visualViewport?.height ?? window.innerHeight;
              if (
                shouldDismissVerticalDrag({
                  offsetY: info.offset.y,
                  velocityY: info.velocity.y,
                  viewportHeight,
                })
              ) {
                closeApp();
              }
            }}
          >
            <AppWindow
              title={TITLES[shell.openApp]}
              specialist={SPECIALISTS[shell.openApp]}
              tabs={tabs}
              activeTab={activeTab}
              onTabChange={setTab}
              onClose={closeApp}
              onDragHandlePointerDown={(event) => appDragControls.start(event)}
              onAskJarvis={() =>
                dispatchShell({
                  type: 'open_assistant',
                  context: shell.openApp ?? undefined,
                })
              }
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function buildTabs(
  app: ShellAppId,
  currency: string,
  onChanged: () => void,
): Tab[] {
  switch (app) {
    case 'connections':
      return [
        {
          id: 'conexoes',
          label: 'Conexões',
          render: () => <ConnectionsTab onChanged={onChanged} />,
        },
        {
          id: 'como-funciona',
          label: 'Como funciona',
          render: () => <ConnectionsAbout />,
        },
      ];
    case 'finance':
      return [
        {
          id: 'resumo',
          label: 'Resumo',
          render: () => <FinanceOverview currency={currency} />,
        },
        {
          id: 'documentos',
          label: 'Documentos',
          render: () => (
            <FinancialDocumentsTab currency={currency} onChanged={onChanged} />
          ),
        },
        {
          id: 'contas',
          label: 'A pagar',
          render: () => <BillsTab currency={currency} onChanged={onChanged} />,
        },
        {
          id: 'gastos',
          label: 'Gastos',
          render: () => (
            <TransactionsTab currency={currency} onChanged={onChanged} />
          ),
        },
        {
          id: 'metas',
          label: 'Metas',
          render: () => <GoalsTab currency={currency} />,
        },
      ];
    case 'fitness':
      return [
        {
          id: 'hoje',
          label: 'Hoje',
          render: () => <CoachToday onChanged={onChanged} />,
        },
        {
          id: 'plano',
          label: 'Plano',
          render: () => <CoachPlan onChanged={onChanged} />,
        },
        { id: 'evolucao', label: 'Evolução', render: () => <FitnessProgress /> },
        {
          id: 'perfil',
          label: 'Perfil',
          render: () => <CoachProfile onChanged={onChanged} />,
        },
      ];
    case 'routine':
      return [
        {
          id: 'hoje',
          label: 'Hoje',
          render: () => <HabitsToday onChanged={onChanged} />,
        },
        {
          id: 'habitos',
          label: 'Hábitos',
          render: () => <ManageHabits onChanged={onChanged} />,
        },
      ];
    case 'family':
      return [
        { id: 'proximos', label: 'Próximos', render: () => <UpcomingTab /> },
        {
          id: 'pessoas',
          label: 'Pessoas',
          render: () => <PeopleTab onChanged={onChanged} />,
        },
      ];
    case 'work':
      return [
        {
          id: 'tarefas',
          label: 'Tarefas',
          render: () => <TasksTab onChanged={onChanged} />,
        },
        { id: 'projetos', label: 'Projetos', render: () => <ProjectsTab /> },
      ];
    case 'health':
      return [
        { id: 'resumo', label: 'Resumo', render: () => <HealthOverview /> },
        {
          id: 'perfil',
          label: 'Perfil',
          render: () => <HealthProfileTab onChanged={onChanged} />,
        },
        {
          id: 'registros',
          label: 'Registros',
          render: () => <HealthRecordsTab onChanged={onChanged} />,
        },
        {
          id: 'documentos',
          label: 'Exames',
          render: () => <HealthDocumentsTab onChanged={onChanged} />,
        },
      ];
    default:
      return [];
  }
}
