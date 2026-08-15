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
  vi.useRealTimers();
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

/** Um fetch que nunca responde — a rede de celular que engasga e não volta. */
function hangUntilAborted() {
  fetchMock.mockImplementation(
    (_input, init) =>
      new Promise((_resolve, reject) => {
        const signal = (init as RequestInit | undefined)?.signal;
        signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        );
      }),
  );
}

describe('teto de tempo das chamadas ao Life API', () => {
  it('desiste de uma requisição pendurada e explica o motivo', async () => {
    vi.useFakeTimers();
    hangUntilAborted();
    const { listAccounts } = await import('./api');

    const pending = listAccounts();
    const assertion = expect(pending).rejects.toThrow(/demorou demais/i);
    await vi.advanceTimersByTimeAsync(20_000);
    await assertion;
  });

  it('sempre entrega um sinal ao fetch, senão não há como desistir', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ records: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { listAccounts } = await import('./api');

    await listAccounts();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.signal?.aborted).toBe(false);
  });

  it('não deixa o cronômetro armado depois de uma resposta boa', async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ records: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { listAccounts } = await import('./api');

    await listAccounts();
    // Se o clearTimeout sumisse, este avanço dispararia um abort órfão.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(vi.getTimerCount()).toBe(0);
  });

});
