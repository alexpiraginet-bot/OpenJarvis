import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import {
  JarvisCore,
  askWithNativeCalendarContext,
  calendarConnectionAllowsContext,
  confirmedProposalSucceeded,
  corePixelAlpha,
  voiceProposalDecision,
  voiceProposalIndex,
} from './JarvisCore';
import { ask, fetchIntegrations, type JarvisActionProposal } from './api';

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

describe('askWithNativeCalendarContext', () => {
  it('bypasses the HTTP cache when checking the live server grant', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ providers: [], summary: {} }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('localStorage', { getItem: () => '' });

    try {
      await fetchIntegrations();
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/life/integrations',
        expect.objectContaining({ cache: 'no-store' }),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('allows EventKit only for a connected backend grant with read scope', () => {
    const connected = {
      status: 'connected' as const,
      account_label: 'iPhone',
      granted_scopes: ['events.read', 'events.write'],
      connected_at: '2026-08-13T12:00:00Z',
      last_sync_at: null,
      last_sync_status: '' as const,
      last_error: '',
      revoked_at: null,
      updated_at: '2026-08-13T12:00:00Z',
      has_credential: false,
    };

    expect(calendarConnectionAllowsContext(connected)).toBe(true);
    expect(calendarConnectionAllowsContext({ ...connected, status: 'revoked' })).toBe(
      false,
    );
    expect(calendarConnectionAllowsContext({
      ...connected,
      granted_scopes: ['events.write'],
    })).toBe(false);
    expect(calendarConnectionAllowsContext(null)).toBe(false);
  });

  it('sends the bounded native calendar context with the assistant question', async () => {
    const context = {
      calendar_events: [
        {
          id: 'event-1',
          title: 'Marketing',
          start_at: '2026-08-14T15:00:00-03:00',
          end_at: '2026-08-14T16:00:00-03:00',
          is_all_day: false,
          location: '',
          calendar_title: 'Pessoal',
        },
      ],
    };
    const readContext = vi.fn().mockResolvedValue(context);
    const connectionIsActive = vi.fn().mockResolvedValue(true);
    const askQuestion = vi.fn().mockResolvedValue({ answer: 'Você tem Marketing.' });

    await expect(
      askWithNativeCalendarContext(
        'O que tenho amanhã?',
        askQuestion,
        readContext,
        connectionIsActive,
      ),
    ).resolves.toEqual({ answer: 'Você tem Marketing.' });
    expect(connectionIsActive).toHaveBeenCalledOnce();
    expect(askQuestion).toHaveBeenCalledWith('O que tenho amanhã?', context);
  });

  it('does not read or transmit EventKit after the backend connection is revoked', async () => {
    const readContext = vi.fn();
    const connectionIsActive = vi.fn().mockResolvedValue(false);
    const askQuestion = vi.fn().mockResolvedValue({ answer: 'Resposta sem agenda.' });

    await askWithNativeCalendarContext(
      'Meu resumo',
      askQuestion,
      readContext,
      connectionIsActive,
    );

    expect(readContext).not.toHaveBeenCalled();
    expect(askQuestion).toHaveBeenCalledWith('Meu resumo', undefined);
  });

  it('fails closed without reading EventKit when server connection lookup fails', async () => {
    const readContext = vi.fn();
    const connectionIsActive = vi.fn().mockRejectedValue(new Error('HTTP 500'));
    const askQuestion = vi.fn().mockResolvedValue({ answer: 'Resposta sem agenda.' });

    await askWithNativeCalendarContext(
      'Meu resumo',
      askQuestion,
      readContext,
      connectionIsActive,
    );

    expect(readContext).not.toHaveBeenCalled();
    expect(askQuestion).toHaveBeenCalledWith('Meu resumo', undefined);
  });

  it('keeps the assistant available outside the native iOS shell', async () => {
    const readContext = vi.fn().mockRejectedValue(new Error('sem bridge'));
    const connectionIsActive = vi.fn().mockResolvedValue(true);
    const askQuestion = vi.fn().mockResolvedValue({ answer: 'Resposta sem agenda.' });

    await askWithNativeCalendarContext(
      'Meu resumo',
      askQuestion,
      readContext,
      connectionIsActive,
    );

    expect(askQuestion).toHaveBeenCalledWith('Meu resumo', undefined);
  });

  it('serializes native events under the backend device_context contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: 'ok', source: 'model', context: {} }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('localStorage', {
      getItem: () => '',
      setItem: () => undefined,
      removeItem: () => undefined,
    });
    const context = {
      calendar_events: [
        {
          id: 'event-1',
          title: 'Marketing',
          start_at: '2026-08-14T15:00:00-03:00',
          end_at: '2026-08-14T16:00:00-03:00',
          is_all_day: false,
          location: '',
          calendar_title: 'Pessoal',
        },
      ],
    };

    try {
      await ask('O que tenho amanhã?', context);
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/life/ask',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            question: 'O que tenho amanhã?',
            device_context: context,
          }),
        }),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('inline Jarvis panel', () => {
  it('offers typed and voice input without becoming a modal or duplicating the core', () => {
    const markup = renderToStaticMarkup(
      createElement(JarvisCore, {
        today: null,
        userId: 'user-alex',
        contextApp: 'finance',
        variant: 'embedded',
        onClose: vi.fn(),
        onRefresh: vi.fn(),
      }),
    );

    expect(markup).not.toContain('role="dialog"');
    expect(markup).not.toContain('aria-modal');
    expect(markup).not.toContain('oj-hud-stage');
    expect(markup).not.toContain('oj-hud-top');
    expect(markup).toContain('aria-label="Comandos do Jarvis"');
    expect(markup).toContain('aria-label="Conversar por texto com o Jarvis"');
    expect(markup).toContain('aria-label="Histórico da conversa"');
    expect(markup).toContain('name="jarvis-message"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('Contexto: Finanças');
  });
});
