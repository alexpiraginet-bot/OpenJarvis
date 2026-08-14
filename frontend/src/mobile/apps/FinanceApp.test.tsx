import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { FinancialCandidate } from '../types';
import { FinancialCandidateList, bytesToBase64 } from './FinanceApp';

describe('financial document review', () => {
  it('encodes attachment bytes without changing their contents', () => {
    expect(bytesToBase64(new Uint8Array([0, 1, 2, 253, 254, 255]))).toBe(
      'AAEC/f7/',
    );
  });

  it('shows extracted value, confidence and that confirmation is required', () => {
    const candidates: FinancialCandidate[] = [
      {
        kind: 'expense',
        amount_cents: 8750,
        description: 'POSTO AVENIDA',
        category: 'transporte',
        occurred_on: '2026-08-14',
        confidence: 0.96,
      },
    ];

    const markup = renderToStaticMarkup(
      <FinancialCandidateList candidates={candidates} currency="BRL" />,
    );

    expect(markup).toContain('POSTO AVENIDA');
    expect(markup).toContain('R$ 87,50');
    expect(markup).toContain('96%');
    expect(markup).toContain('revisão');
  });
});
