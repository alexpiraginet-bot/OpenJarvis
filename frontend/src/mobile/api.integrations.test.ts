import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

describe('Integration sync API', () => {
  it('synchronizes one provider under the authenticated Life account', async () => {
    const result = {
      provider: 'gmail',
      synced: 7,
      last_sync_at: '2026-08-14T12:00:00+00:00',
    };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(result), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { syncIntegration } = await import('./api');

    await expect(syncIntegration('gmail')).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/integrations/gmail/sync',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({}),
        headers: expect.objectContaining({ Authorization: 'Bearer life-token' }),
      }),
    );
  });
});
