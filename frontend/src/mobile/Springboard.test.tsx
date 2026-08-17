import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { Springboard } from './Springboard';
import type { Today } from './types';

const TODAY = {
  date: '2026-08-13',
  greeting: 'Boa noite, Alex',
  currency: 'BRL',
  alerts: [],
  badges: {
    finance: 0,
    fitness: 0,
    routine: 0,
    family: 0,
    work: 0,
    health: 2,
  },
  finance: {
    balance_cents: 0,
    net_cents: 0,
    expense_cents: 0,
    income_cents: 0,
    overdue_count: 0,
    due_soon_count: 0,
  },
  fitness: {
    week_completed: 0,
    week_planned: 0,
    days_since_last: null,
    todays_workout: null,
  },
  routine: { completed: 0, total: 0, pending: [] },
  family: { upcoming: [] },
  work: { open_count: 0, overdue_count: 0, due_today_count: 0 },
  health: {
    profile: null,
    active_conditions: [],
    active_medications: [],
    allergies: [],
    hydration_today_ml: 750,
    nutrition_today_count: 2,
    latest_observations: [],
    documents: [],
  },
} as unknown as Today;

describe('Springboard health surface', () => {
  it('shows Saúde in Visão 360, orbit and specialist council', () => {
    const markup = renderToStaticMarkup(
      <Springboard
        today={TODAY}
        loading={false}
        error=""
        userName="Alex"
        onOpenApp={vi.fn()}
        onAskJarvis={vi.fn()}
        assistantOpen={false}
        assistantPanel={null}
        onLogout={vi.fn()}
      />,
    );

    expect(markup).toContain('750 ml hoje');
    expect(markup).toContain('aria-label="Saúde, 2 pendência(s)"');
    expect(markup).toContain('Especialista de saúde');
    expect(markup).toContain('data-presence="idle"');
    expect(markup).toContain('/assets/aether-neural-core-768.jpg');
    expect(markup).not.toContain('/aether-neural-core.png');
  });

  it('keeps initial Visão 360 synchronization visibly alive', () => {
    const markup = renderToStaticMarkup(
      <Springboard
        today={null}
        loading
        error=""
        userName="Alex"
        onOpenApp={vi.fn()}
        onAskJarvis={vi.fn()}
        assistantOpen={false}
        assistantPanel={null}
        onLogout={vi.fn()}
      />,
    );

    expect(markup).toContain('data-presence="loading"');
    expect(markup).toContain('Sincronizando sua visão 360');
  });

  it('uses the central core as the only Jarvis entry point', () => {
    const markup = renderToStaticMarkup(
      <Springboard
        today={TODAY}
        loading={false}
        error=""
        userName="Alex"
        onOpenApp={vi.fn()}
        onAskJarvis={vi.fn()}
        assistantOpen={false}
        assistantPanel={null}
        onLogout={vi.fn()}
      />,
    );

    expect(markup).toContain('aria-label="Conversar com o Jarvis"');
    expect(markup).not.toContain('aria-label="Abrir painel do Jarvis"');
  });

  it('keeps the apps visible while rendering Jarvis controls inline after the orbit', () => {
    const markup = renderToStaticMarkup(
      <Springboard
        today={TODAY}
        loading={false}
        error=""
        userName="Alex"
        onOpenApp={vi.fn()}
        onAskJarvis={vi.fn()}
        assistantOpen
        assistantPanel={(
          <section id="jarvis-inline-controls" aria-label="Comandos do Jarvis">
            <form aria-label="Conversar por texto com o Jarvis" />
          </section>
        )}
        onLogout={vi.fn()}
      />,
    );

    const orbitIndex = markup.indexOf('Aplicativos conectados ao Jarvis');
    const assistantIndex = markup.indexOf('id="jarvis-inline-controls"');
    const councilIndex = markup.indexOf('Conselho de especialistas');

    expect(orbitIndex).toBeGreaterThan(-1);
    expect(assistantIndex).toBeGreaterThan(orbitIndex);
    expect(councilIndex).toBeGreaterThan(assistantIndex);
    expect(markup).toContain('aria-label="Finanças"');
    expect(markup).toContain('aria-label="Conversar por texto com o Jarvis"');
    expect(markup).not.toContain('role="dialog"');
    expect(markup).not.toContain('aria-modal');
    expect(markup).not.toContain('oj-command-overlay');
  });

  it('makes the horizontal Visão 360 carousel discoverable', () => {
    const markup = renderToStaticMarkup(
      <Springboard
        today={TODAY}
        loading={false}
        error=""
        userName="Alex"
        onOpenApp={vi.fn()}
        onAskJarvis={vi.fn()}
        assistantOpen={false}
        assistantPanel={null}
        onLogout={vi.fn()}
      />,
    );

    expect(markup).toContain('Deslize para ver todas as áreas');
    expect(markup).toContain('aria-label="Áreas da Visão 360"');
    expect(markup).toContain('aria-describedby="oj-life-overview-hint"');
    expect(markup).toContain('tabindex="0"');
  });
});
