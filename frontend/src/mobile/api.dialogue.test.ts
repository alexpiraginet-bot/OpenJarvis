import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ask, fetchRecentDialogue } from './api';

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('localStorage', {
    getItem: () => 'tenant-token',
    setItem: () => undefined,
    removeItem: () => undefined,
  });
});

afterEach(() => vi.unstubAllGlobals());

describe('dialogue API contract', () => {
  it('sends the durable conversation cursor with each typed or spoken turn', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          answer: 'Qual e o titulo da reuniao?',
          source: 'model',
          context: {},
          conversation_id: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
          revision: 1,
          history: [],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    await ask('Marcar reuniao amanha as 15 horas', undefined, {
      conversationId: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
      turnId: '9e0a385a-5c60-42e7-ab32-616bab20476d',
      expectedRevision: 0,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/ask',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          question: 'Marcar reuniao amanha as 15 horas',
          conversation_id: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
          turn_id: '9e0a385a-5c60-42e7-ab32-616bab20476d',
          expected_revision: 0,
        }),
      }),
    );
  });

  it('sends specialist separately and never alters the user message', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ answer: 'ok', source: 'model', context: {} }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    await ask('O que devo priorizar?', undefined, {
      conversationId: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
      turnId: '9e0a385a-5c60-42e7-ab32-616bab20476d',
      expectedRevision: 0,
      specialist: 'finance',
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body).toMatchObject({
      question: 'O que devo priorizar?',
      specialist: 'finance',
    });
    expect(body.question).not.toContain('Especialista ativo');
  });

  it('restores cross-device dialogue without accepting an HTTP cache', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ session: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await fetchRecentDialogue();

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/dialogue/recent',
      expect.objectContaining({ cache: 'no-store' }),
    );
  });
});
