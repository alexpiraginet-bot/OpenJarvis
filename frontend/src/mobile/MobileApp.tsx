/**
 * The shell — one surface, three layers.
 *
 * The Jarvis core is the home screen; the springboard slides over it; an app
 * window opens over that. Nothing here changes the route, so the whole product
 * behaves like a phone rather than a website: you never leave, you go deeper
 * and come back.
 */

import { useCallback, useEffect, useState } from 'react';
import { AppWindow, type Tab } from './AppWindow';
import { fetchMe, fetchToday, getToken, logout } from './api';
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
import type { AppId, LifeUser, Today } from './types';
import { Button, Spinner } from './ui';
import './mobile.css';

const TITLES: Record<AppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
};

type Layer = 'jarvis' | 'springboard';

export default function MobileApp() {
  const [user, setUser] = useState<LifeUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [layer, setLayer] = useState<Layer>('jarvis');
  const [openApp, setOpenApp] = useState<AppId | null>(null);
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

  const handleOpenApp = useCallback((app: AppId) => {
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
      <div className="oj-mobile">
        <Spinner />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="oj-mobile">
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
    <div className="oj-mobile">
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

      {openApp && (
        <AppWindow
          title={TITLES[openApp]}
          tabs={tabs}
          activeTab={activeTab}
          onTabChange={setTab}
          onClose={closeApp}
        />
      )}
    </div>
  );
}

function buildTabs(app: AppId, currency: string, onChanged: () => void): Tab[] {
  switch (app) {
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
