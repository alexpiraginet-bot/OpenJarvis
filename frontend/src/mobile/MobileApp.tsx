/**
 * The shell — one surface, three layers.
 *
 * The Jarvis core is the home screen; the springboard slides over it; an app
 * window opens over that. Nothing here changes the route, so the whole product
 * behaves like a phone rather than a website: you never leave, you go deeper
 * and come back.
 */

import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { AppWindow, type Tab } from './AppWindow';
import { fetchMe, fetchToday, getToken, logout } from './api';
import {
  ConnectionsAbout,
  ConnectionsTab,
} from './apps/ConnectionsApp';
import {
  BillsTab,
  FinanceOverview,
  GoalsTab,
  TransactionsTab,
} from './apps/FinanceApp';
import { FitnessOverview, MeasurementsTab, WorkoutsTab } from './apps/FitnessApp';
import { PeopleTab, UpcomingTab } from './apps/FamilyApp';
import { HabitsToday, ManageHabits } from './apps/RoutineApp';
import { ProjectsTab, TasksTab } from './apps/WorkApp';
import { JarvisCore } from './JarvisCore';
import { LoginScreen } from './LoginScreen';
import { Springboard } from './Springboard';
import type { LifeUser, ShellAppId, Today } from './types';
import { Button, Spinner } from './ui';
import './mobile.css';

const TITLES: Record<ShellAppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
  connections: 'Conexões',
};

const SPECIALISTS: Record<ShellAppId, string> = {
  finance: 'Diretor financeiro IA',
  fitness: 'Coach de performance IA',
  routine: 'Chefe de gabinete IA',
  family: 'Concierge familiar IA',
  work: 'Assistente executivo IA',
  connections: 'Engenheiro de integrações IA',
};

type Layer = 'jarvis' | 'springboard';

export default function MobileApp() {
  const reduceMotion = useReducedMotion();
  const [user, setUser] = useState<LifeUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [layer, setLayer] = useState<Layer>('jarvis');
  const [openApp, setOpenApp] = useState<ShellAppId | null>(null);
  const [tab, setTab] = useState('');
  const [today, setToday] = useState<Today | null>(null);
  const [loadingToday, setLoadingToday] = useState(false);
  const [error, setError] = useState('');

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
    setOpenApp(app);
    setTab('');
    setLayer('springboard');
  }, []);

  const closeApp = useCallback(() => {
    setOpenApp(null);
    // Coming back from an app is the moment badges are most likely stale.
    refresh();
  }, [refresh]);

  async function handleLogout() {
    await logout();
    setUser(null);
    setToday(null);
    setLayer('jarvis');
    setOpenApp(null);
  }

  if (checking) {
    return (
      <div className="oj-mobile" translate="no">
        <Spinner />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="oj-mobile" translate="no">
        <LoginScreen
          onAuth={(authenticated) => {
            setUser(authenticated);
            setLayer('jarvis');
          }}
        />
      </div>
    );
  }

  const currency = today?.currency ?? user.currency;
  const tabs: Tab[] = openApp ? buildTabs(openApp, currency, refresh) : [];
  const activeTab = tab || tabs[0]?.id || '';

  return (
    <div className="oj-mobile" translate="no">
      <AnimatePresence initial={false} mode="sync">
        <motion.div
          key={layer}
          className="oj-shell-layer"
          initial={
            reduceMotion
              ? false
              : layer === 'springboard'
                ? { opacity: 0, scale: 0.965, y: '7%' }
                : { opacity: 0, scale: 1.025, y: '-4%' }
          }
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={
            reduceMotion
              ? { opacity: 1 }
              : layer === 'springboard'
                ? { opacity: 0, scale: 0.985, y: '4%' }
                : { opacity: 0, scale: 1.02, y: '-3%' }
          }
          transition={
            reduceMotion
              ? { duration: 0 }
              : { type: 'spring', stiffness: 430, damping: 42, mass: 0.82 }
          }
        >
          {layer === 'jarvis' ? (
            <JarvisCore
              today={today}
              onOpenSpringboard={() => setLayer('springboard')}
              onRefresh={refresh}
            />
          ) : (
            <Springboard
              today={today}
              loading={loadingToday}
              error={error}
              userName={user.name}
              onOpenApp={handleOpenApp}
              onAskJarvis={() => setLayer('jarvis')}
              onLogout={handleLogout}
            />
          )}
        </motion.div>
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {openApp && (
          <motion.div
            key={openApp}
            className="oj-app-layer"
            initial={
              reduceMotion
                ? false
                : { opacity: 0, y: '100%', scale: 0.94, borderRadius: 32 }
            }
            animate={{ opacity: 1, y: 0, scale: 1, borderRadius: 0 }}
            exit={
              reduceMotion
                ? { opacity: 0 }
                : { opacity: 0, y: '22%', scale: 0.96, borderRadius: 32 }
            }
            transition={
              reduceMotion
                ? { duration: 0 }
                : { type: 'spring', stiffness: 390, damping: 38, mass: 0.88 }
            }
          >
            <AppWindow
              title={TITLES[openApp]}
              specialist={SPECIALISTS[openApp]}
              tabs={tabs}
              activeTab={activeTab}
              onTabChange={setTab}
              onClose={closeApp}
              onAskJarvis={() => {
                setOpenApp(null);
                setLayer('jarvis');
              }}
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
        { id: 'semana', label: 'Semana', render: () => <FitnessOverview /> },
        {
          id: 'treinos',
          label: 'Treinos',
          render: () => <WorkoutsTab onChanged={onChanged} />,
        },
        { id: 'medidas', label: 'Medidas', render: () => <MeasurementsTab /> },
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
    default:
      return [];
  }
}
