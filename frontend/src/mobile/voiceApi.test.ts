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
