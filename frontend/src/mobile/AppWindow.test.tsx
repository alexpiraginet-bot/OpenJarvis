import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { AppWindow, type Tab } from './AppWindow';

const DENSE_TABS: Tab[] = ['Resumo', 'Documentos', 'A pagar', 'Gastos', 'Metas'].map(
  (label) => ({
    id: label.toLocaleLowerCase('pt-BR').replace(/ /g, '-'),
    label,
    render: () => <div>{label}</div>,
  }),
);

describe('AppWindow responsive tabs', () => {
  it('exposes dense tab sets as an intentional horizontal scroller', () => {
    const markup = renderToStaticMarkup(
      <AppWindow
        title="Finanças"
        specialist="Diretor financeiro IA"
        tabs={DENSE_TABS}
        activeTab="resumo"
        onTabChange={vi.fn()}
        onClose={vi.fn()}
        onAskJarvis={vi.fn()}
      />,
    );

    expect(markup).toContain('data-scrollable="true"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain('deslize horizontalmente para ver todas');
    expect(markup).toContain('class="oj-segmented-hint"');
    expect(markup).toContain('aria-describedby="oj-sections-hint"');
    expect(markup).toContain('data-drag-handle="true"');
    expect(markup).toContain(
      'aria-label="Arraste para baixo para fechar Finanças"',
    );
  });
});
