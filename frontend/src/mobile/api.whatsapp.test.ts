import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WhatsAppBriefingPreference } from './types';

const fetchMock = vi.fn<typeof fetch>();

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string) { return this.store.get(key) ?? null; }
  setItem(key: string, value: string) { this.store.set(key, value); }
  removeItem(key: string) { this.store.delete(key); }
}

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
  localStorage.setItem('oj-life-token', 'life-token');
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

describe('WhatsApp Life channel API', () => {
  it('turns an unlinked 404 into a disconnected state', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Canal não vinculado.' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { fetchWhatsAppChannel } = await import('./api');

    await expect(fetchWhatsAppChannel()).resolves.toEqual({
      status: 'disconnected',
    });
  });

  it('starts official linking without putting a code in the client payload', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'pending',
          expires_at: '2026-08-13T12:10:00+00:00',
        }),
        { status: 202, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const { linkWhatsApp } = await import('./api');

    await linkWhatsApp('+5527999990001');

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/channels/whatsapp/link',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ phone: '+5527999990001' }),
        headers: expect.objectContaining({ Authorization: 'Bearer life-token' }),
      }),
    );
  });

  it('revokes only the authenticated user channel', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ status: 'revoked' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { revokeWhatsApp } = await import('./api');

    await expect(revokeWhatsApp()).resolves.toEqual({ status: 'revoked' });
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/channels/whatsapp',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('loads the authenticated user daily briefing preferences', async () => {
    const briefing = {
      enabled: true,
      time: '07:30',
      sections: ['priorities', 'calendar', 'news'],
      news_topics: ['mobilidade', 'carros por assinatura'],
      delivery_days: [1, 2, 3, 4, 5],
      custom_instructions: 'Priorize o Brasil.',
    } satisfies WhatsAppBriefingPreference;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ briefing }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { fetchWhatsAppBriefing } = await import('./api');

    await expect(fetchWhatsAppBriefing()).resolves.toEqual(briefing);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/channels/whatsapp/briefing',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer life-token' }),
      }),
    );
  });

  it('saves all personalized briefing controls without dropping empty topics', async () => {
    const briefing = {
      enabled: false,
      time: '08:15',
      sections: ['finance', 'fitness'],
      news_topics: [],
      delivery_days: [0, 6],
      custom_instructions: '',
    } satisfies WhatsAppBriefingPreference;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ briefing }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { saveWhatsAppBriefing } = await import('./api');

    await expect(saveWhatsAppBriefing(briefing)).resolves.toEqual(briefing);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/channels/whatsapp/briefing',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify(briefing),
      }),
    );
  });
});

describe('Native integration API', () => {
  it('registers the authenticated installation grant without a credential', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          provider: 'apple_calendar',
          connection: {
            status: 'connected',
            granted_scopes: ['events.read', 'events.write'],
            has_credential: false,
          },
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const { registerDeviceGrant } = await import('./api');

    await registerDeviceGrant(
      'apple_calendar',
      {
        grantedScopes: ['events.read', 'events.write'],
        deviceId: 'ios-device-1234',
        deviceLabel: 'iPhone',
      },
      {
        deviceId: 'ios-device-1234',
        challengeId: 'challenge-device-grant-1234',
        challenge: 'c'.repeat(43),
        keyId: 'k'.repeat(43),
        assertion: 'a'.repeat(86),
      },
    );

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/integrations/apple_calendar/device-grant',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          granted_scopes: ['events.read', 'events.write'],
          device_id: 'ios-device-1234',
          device_label: 'iPhone',
          app_attest: {
            challenge_id: 'challenge-device-grant-1234',
            challenge: 'c'.repeat(43),
            key_id: 'k'.repeat(43),
            assertion: 'a'.repeat(86),
          },
        }),
        headers: expect.objectContaining({ Authorization: 'Bearer life-token' }),
      }),
    );
  });

  it('uploads an opaque attested Apple Health batch without a HealthKit credential', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          provider: 'apple_health',
          synced: 2,
          last_sync_at: '2026-08-14T12:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const { syncAppleHealth } = await import('./api');

    await syncAppleHealth(
      'ios-device-1234',
      'eyJzYW1wbGVzIjpbXX0',
      {
        deviceId: 'ios-device-1234',
        challengeId: 'challenge-health-sync-1234',
        challenge: 'c'.repeat(43),
        keyId: 'k'.repeat(43),
        assertion: 'a'.repeat(86),
      },
    );

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/integrations/apple_health/device-sync',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          device_id: 'ios-device-1234',
          payload: 'eyJzYW1wbGVzIjpbXX0',
          app_attest: {
            challenge_id: 'challenge-health-sync-1234',
            challenge: 'c'.repeat(43),
            key_id: 'k'.repeat(43),
            assertion: 'a'.repeat(86),
          },
        }),
      }),
    );
  });
});
