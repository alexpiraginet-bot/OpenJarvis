import { describe, expect, it } from 'vitest';
import {
  confirmedProposalSucceeded,
  corePixelAlpha,
  voiceProposalDecision,
  voiceProposalIndex,
} from './JarvisCore';
import type { JarvisActionProposal } from './api';

function proposal(
  overrides: Partial<JarvisActionProposal>,
): JarvisActionProposal {
  return {
    id: 'proposal-1',
    tool_name: 'life_complete',
    summary: 'Pagar energia - R$ 148,90',
    status: 'confirmed',
    arguments: {},
    result: { success: true },
    created_at: '2026-08-10T10:00:00Z',
    expires_at: '2026-08-10T10:10:00Z',
    ...overrides,
  };
}

describe('confirmedProposalSucceeded', () => {
  it('accepts a confirmed proposal with a successful result', () => {
    expect(confirmedProposalSucceeded(proposal({}))).toBe(true);
  });

  it('rejects a failed backend result even when the proposal is confirmed', () => {
    expect(
      confirmedProposalSucceeded(
        proposal({ result: { success: false, error: 'ledger write failed' } }),
      ),
    ).toBe(false);
  });

  it('rejects any proposal that did not reach confirmed status', () => {
    expect(confirmedProposalSucceeded(proposal({ status: 'failed' }))).toBe(false);
  });
});

describe('corePixelAlpha', () => {
  it('removes the opaque black source background', () => {
    expect(corePixelAlpha(0, 0, 0)).toBe(0);
  });

  it('keeps bright neural filaments opaque', () => {
    expect(corePixelAlpha(28, 154, 255)).toBe(255);
  });

  it('never increases the source alpha', () => {
    expect(corePixelAlpha(255, 255, 255, 96)).toBe(96);
  });
});

describe('voiceProposalDecision', () => {
  it.each(['sim', 'Confirmo!', 'pode confirmar', 'sim, confirme'])(
    'accepts an explicit voice confirmation: %s',
    (phrase) => {
      expect(voiceProposalDecision(phrase)).toBe('confirm');
    },
  );

  it.each(['confirmar a primeira', 'confirme o segundo', 'confirmar última'])(
    'accepts a voice confirmation that selects one pending action: %s',
    (phrase) => {
      expect(voiceProposalDecision(phrase)).toBe('confirm');
    },
  );

  it.each(['não', 'cancele', 'não confirmar', 'pode cancelar'])(
    'accepts an explicit voice cancellation: %s',
    (phrase) => {
      expect(voiceProposalDecision(phrase)).toBe('cancel');
    },
  );

  it.each([
    'registre uma despesa de 20 reais',
    'confirmar a reunião de amanhã com a equipe',
    'pode criar minha rotina',
  ])('does not mistake a new request for proposal approval: %s', (phrase) => {
    expect(voiceProposalDecision(phrase)).toBeNull();
  });
});

describe('voiceProposalIndex', () => {
  it('selects a visible proposal by spoken ordinal', () => {
    expect(voiceProposalIndex('confirmar a primeira', 3)).toBe(0);
    expect(voiceProposalIndex('confirme o segundo', 3)).toBe(1);
    expect(voiceProposalIndex('cancelar a última', 3)).toBe(2);
  });

  it('rejects an ordinal that is not present', () => {
    expect(voiceProposalIndex('confirmar a terceira', 2)).toBeNull();
  });
});
