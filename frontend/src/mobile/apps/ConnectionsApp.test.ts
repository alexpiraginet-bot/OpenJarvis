import { describe, expect, it, vi } from 'vitest';
import type {
  IntegrationConnection,
  IntegrationProvider,
} from '../types';
import {
  connectionsHeadline,
  disconnectProvider,
  formatRelativeTime,
  ledTone,
  presentProvider,
  showGenericDisconnect,
} from './ConnectionsApp';

const NOW = new Date('2026-08-10T12:00:00Z');

function provider(
  overrides: Partial<IntegrationProvider> = {},
): IntegrationProvider {
  return {
    id: 'gmail',
    label: 'Gmail',
    category: 'Comunicação',
    description: 'E-mails viram contexto.',
    capabilities: ['Ler e-mails (somente leitura)'],
    scopes: ['https://www.googleapis.com/auth/gmail.readonly'],
    auth: { kind: 'oauth', pkce: true },
    availability: 'available',
    missing_config: [],
    prerequisites: [],
    icon: 'mail',
    tint: 'red',
    connection: null,
    pending_auth: null,
    ...overrides,
  };
}

function connection(
  overrides: Partial<IntegrationConnection> = {},
): IntegrationConnection {
  return {
    status: 'connected',
    account_label: 'alex@exemplo.com',
    granted_scopes: ['https://www.googleapis.com/auth/gmail.readonly'],
    connected_at: '2026-08-10T10:00:00Z',
    last_sync_at: '2026-08-10T11:55:00Z',
    last_sync_status: 'ok',
    last_error: '',
    revoked_at: null,
    updated_at: '2026-08-10T11:55:00Z',
    has_credential: true,
    ...overrides,
  };
}

describe('presentProvider', () => {
  it('uma conexão concluída aparece como conectada, com conta e sync reais', () => {
    const view = presentProvider(provider({ connection: connection() }), NOW);
    expect(view.statusLabel).toBe('Conectada');
    expect(view.tone).toBe('ok');
    expect(view.cta).toBe('none');
    expect(view.detail).toBe('alex@exemplo.com · sincronizada há 5 min');
    expect(view.active).toBe(true);
  });

  it('conectada sem sync nenhuma diz "nunca sincronizada", não inventa data', () => {
    const view = presentProvider(
      provider({ connection: connection({ last_sync_at: null }) }),
      NOW,
    );
    expect(view.detail).toContain('nunca sincronizada');
  });

  it('erro de sync mantém conectada, mas com aviso — sem pedir reautorização', () => {
    const view = presentProvider(
      provider({
        connection: connection({
          last_sync_status: 'error',
          last_error: 'HTTP 503 do provedor',
        }),
      }),
      NOW,
    );
    expect(view.statusLabel).toBe('Conectada');
    expect(view.tone).toBe('warn');
    expect(view.cta).toBe('none');
    expect(view.detail).toContain('HTTP 503 do provedor');
  });

  it('autorização vencida exige reconectar — nunca continua verde', () => {
    const view = presentProvider(
      provider({ connection: connection({ status: 'expired' }) }),
      NOW,
    );
    expect(view.statusLabel).toBe('Autorização vencida');
    expect(view.tone).toBe('critical');
    expect(view.cta).toBe('reconnect');
    expect(view.ctaLabel).toBe('Reconectar');
  });

  it('reconectar some se o servidor perdeu a configuração do app', () => {
    const view = presentProvider(
      provider({
        availability: 'needs_setup',
        missing_config: ['OPENJARVIS_LIFE_GOOGLE_CLIENT_ID'],
        connection: connection({ status: 'expired' }),
      }),
      NOW,
    );
    expect(view.cta).toBe('none');
  });

  it('autorização pendente vence a conexão existente (é o que o usuário pediu)', () => {
    const view = presentProvider(
      provider({
        connection: connection(),
        pending_auth: { expires_at: '2026-08-10T12:10:00Z' },
      }),
      NOW,
    );
    expect(view.statusLabel).toBe('Aguardando autorização');
    expect(view.tone).toBe('warn');
    expect(view.cta).toBe('none');
  });

  it('disponível ganha CTA Conectar; revogada vira "Conectar de novo"', () => {
    expect(presentProvider(provider(), NOW).cta).toBe('connect');
    expect(presentProvider(provider(), NOW).ctaLabel).toBe('Conectar');

    const revoked = presentProvider(
      provider({
        connection: connection({
          status: 'revoked',
          revoked_at: '2026-08-08T12:00:00Z',
        }),
      }),
      NOW,
    );
    expect(revoked.statusLabel).toBe('Desconectada');
    expect(revoked.ctaLabel).toBe('Conectar de novo');
    expect(revoked.detail).toBe('Desconectada há 2 dias');
    expect(revoked.active).toBe(false);
  });

  it('sem app configurado no servidor não existe botão Conectar', () => {
    const view = presentProvider(
      provider({
        availability: 'needs_setup',
        missing_config: ['OPENJARVIS_LIFE_GOOGLE_CLIENT_ID'],
      }),
      NOW,
    );
    expect(view.statusLabel).toBe('Requer configuração');
    expect(view.cta).toBe('none');
  });

  it('Apple Calendar oferece a autorizacao nativa sem fingir OAuth', () => {
    const view = presentProvider(
      provider({ id: 'apple_calendar', availability: 'device_only' }),
      NOW,
    );
    expect(view.statusLabel).toBe('No iPhone');
    expect(view.cta).toBe('device');
    expect(view.ctaLabel).toBe('Autorizar calendário');
  });

  it('Apple Health continua indisponivel enquanto nao existe bridge HealthKit', () => {
    const view = presentProvider(
      provider({ id: 'apple_health', availability: 'device_only' }),
      NOW,
    );
    expect(view.statusLabel).toBe('Ainda não disponível');
    expect(view.detail).toContain('HealthKit');
    expect(view.cta).toBe('none');
  });

  it('dependência externa aparece sem botão e sem promessa falsa', () => {
    const view = presentProvider(
      provider({ id: 'whatsapp', availability: 'coming_soon' }),
      NOW,
    );
    expect(view.statusLabel).toBe('Requer parceiro');
    expect(view.detail).toContain('provedor homologado');
    expect(view.cta).toBe('none');
    expect(view.active).toBe(false);
  });
});

describe('showGenericDisconnect', () => {
  it('hides the duplicate action for WhatsApp but keeps it for other providers', () => {
    expect(
      showGenericDisconnect(
        provider({ id: 'whatsapp', connection: connection() }),
      ),
    ).toBe(false);
    expect(showGenericDisconnect(provider({ connection: connection() }))).toBe(
      true,
    );
  });
});

describe('disconnectProvider', () => {
  it('clears account-bound native calendar receipts after server revocation', async () => {
    const disconnect = vi.fn().mockResolvedValue({ result: 'revoked' });
    const clearNativeReceipts = vi.fn().mockResolvedValue(undefined);

    await disconnectProvider(
      provider({ id: 'apple_calendar' }),
      disconnect,
      clearNativeReceipts,
    );

    expect(disconnect).toHaveBeenCalledWith('apple_calendar');
    expect(clearNativeReceipts).toHaveBeenCalledOnce();
    expect(disconnect.mock.invocationCallOrder[0]).toBeLessThan(
      clearNativeReceipts.mock.invocationCallOrder[0],
    );
  });

  it('does not clear idempotency receipts when server revocation fails', async () => {
    const error = new Error('HTTP 500');
    const disconnect = vi.fn().mockRejectedValue(error);
    const clearNativeReceipts = vi.fn();

    await expect(
      disconnectProvider(
        provider({ id: 'apple_calendar' }),
        disconnect,
        clearNativeReceipts,
      ),
    ).rejects.toBe(error);

    expect(clearNativeReceipts).not.toHaveBeenCalled();
  });
});

describe('formatRelativeTime', () => {
  it('cobre as faixas de tempo em pt-BR', () => {
    expect(formatRelativeTime('2026-08-10T11:59:30Z', NOW)).toBe('agora há pouco');
    expect(formatRelativeTime('2026-08-10T11:15:00Z', NOW)).toBe('há 45 min');
    expect(formatRelativeTime('2026-08-10T09:00:00Z', NOW)).toBe('há 3 h');
    expect(formatRelativeTime('2026-08-09T11:00:00Z', NOW)).toBe('ontem');
    expect(formatRelativeTime('2026-08-07T12:00:00Z', NOW)).toBe('há 3 dias');
    expect(formatRelativeTime('2026-07-20T12:00:00Z', NOW)).toBe('em 20/07');
  });

  it('nulo e lixo viram vazio, nunca "Invalid Date"', () => {
    expect(formatRelativeTime(null, NOW)).toBe('');
    expect(formatRelativeTime('não-é-data', NOW)).toBe('');
  });

  it('relógio adiantado no cliente não produz tempo negativo', () => {
    expect(formatRelativeTime('2026-08-10T12:05:00Z', NOW)).toBe('agora há pouco');
  });
});

describe('connectionsHeadline', () => {
  it('vazio vira convite, não zero', () => {
    expect(connectionsHeadline({ connected: 0, attention: 0, pending: 0 })).toBe(
      'Conecte Gmail, Agenda, Strava e mais',
    );
  });

  it('sem nada conectável, o convite vira a verdade sobre configuração', () => {
    expect(
      connectionsHeadline({ connected: 0, attention: 0, pending: 0 }, 0),
    ).toBe('Catálogo pronto — aguardando configuração do servidor');
    expect(
      connectionsHeadline({ connected: 0, attention: 0, pending: 0 }, 3),
    ).toBe('Conecte Gmail, Agenda, Strava e mais');
  });

  it('compõe os números reais com plural correto', () => {
    expect(connectionsHeadline({ connected: 1, attention: 0, pending: 0 })).toBe(
      '1 conectada',
    );
    expect(connectionsHeadline({ connected: 2, attention: 1, pending: 1 })).toBe(
      '2 conectadas · 1 precisa de atenção · 1 aguardando autorização',
    );
  });
});

describe('ledTone', () => {
  it('mapeia cada estado para o LED honesto', () => {
    expect(ledTone(provider())).toBe('off');
    expect(ledTone(provider({ connection: connection() }))).toBe('ok');
    expect(
      ledTone(provider({ connection: connection({ last_sync_status: 'error' }) })),
    ).toBe('warn');
    expect(
      ledTone(provider({ connection: connection({ status: 'expired' }) })),
    ).toBe('critical');
    expect(
      ledTone(provider({ pending_auth: { expires_at: '2026-08-10T12:10:00Z' } })),
    ).toBe('warn');
    expect(
      ledTone(provider({ connection: connection({ status: 'revoked' }) })),
    ).toBe('off');
  });
});
