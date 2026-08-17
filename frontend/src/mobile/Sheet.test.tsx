import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { Sheet } from './ui';

describe('Sheet drag affordance', () => {
  it('exposes a 44pt handle without making the form body draggable', () => {
    const markup = renderToStaticMarkup(
      createElement(
        Sheet,
        {
          title: 'Novo lançamento',
          onClose: vi.fn(),
          children: createElement('input', { name: 'amount' }),
        },
      ),
    );

    expect(markup).toContain('data-drag-handle="true"');
    expect(markup).toContain(
      'aria-label="Arraste para baixo ou toque para fechar Novo lançamento"',
    );
    expect(markup).toContain('name="amount"');
  });
});
