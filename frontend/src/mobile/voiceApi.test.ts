import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchMock = vi.fn<typeof fetch>();

class MemoryStorage {
  private store = new Map<string, string>();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }
}

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

describe('synthesizeJarvisVoice', () => {
  it('sends the user bearer and returns playable audio', async () => {
    localStorage.setItem('oj-life-token', 'life-user-token');
    fetchMock.mockResolvedValue(
      new Response(new Blob(['mp3-data'], { type: 'audio/mpeg' }), {
        status: 200,
        headers: { 'Content-Type': 'audio/mpeg' },
      }),
    );
    const { synthesizeJarvisVoice } = await import('./voiceApi');

    const result = await synthesizeJarvisVoice('  Bom dia, Alex.  ');

    expect(result.type).toBe('audio/mpeg');
    expect(fetchMock).toHaveBeenCalledWith('/v1/life/voice/speech', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer life-user-token',
      },
      body: JSON.stringify({ text: 'Bom dia, Alex.' }),
      signal: undefined,
    });
  });

  it('clears an expired user session', async () => {
    localStorage.setItem('oj-life-token', 'expired-token');
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid or expired token' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { synthesizeJarvisVoice } = await import('./voiceApi');

    await expect(synthesizeJarvisVoice('Teste')).rejects.toMatchObject({
      status: 401,
    });
    expect(localStorage.getItem('oj-life-token')).toBeNull();
  });

  it('rejects a non-audio success response', async () => {
    fetchMock.mockResolvedValue(
      new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { synthesizeJarvisVoice } = await import('./voiceApi');

    await expect(synthesizeJarvisVoice('Teste')).rejects.toMatchObject({
      status: 502,
    });
  });
});

describe('requestRealtimeVoiceToken', () => {
  it('requests a tenant-authenticated ephemeral session without exposing the server key', async () => {
    localStorage.setItem('oj-life-token', 'life-user-token');
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          client_secret: 'ek_test_ephemeral',
          expires_at: 1_800_000_000,
          model: 'gpt-realtime-2.1',
          voice: 'cedar',
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
    const { requestRealtimeVoiceToken } = await import('./voiceApi');

    const result = await requestRealtimeVoiceToken();

    expect(result).toEqual({
      clientSecret: 'ek_test_ephemeral',
      expiresAt: 1_800_000_000,
      model: 'gpt-realtime-2.1',
      voice: 'cedar',
    });
    expect(fetchMock).toHaveBeenCalledWith('/v1/life/voice/realtime/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer life-user-token',
      },
      body: '{}',
      signal: undefined,
    });
  });

  it('rejects an incomplete provider response', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ model: 'gpt-realtime-2.1' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { requestRealtimeVoiceToken } = await import('./voiceApi');

    await expect(requestRealtimeVoiceToken()).rejects.toMatchObject({
      status: 502,
    });
  });
});
