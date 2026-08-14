import { describe, expect, it, vi } from 'vitest';
import type { AskResult, DialogueSession } from './api';
import { DialogueSessionController } from './dialogueSession';

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }

  removeItem(key: string) {
    this.values.delete(key);
  }
}

function result(
  conversationId: string,
  revision: number,
  answer: string,
): AskResult {
  return {
    answer,
    source: 'model',
    context: {} as AskResult['context'],
    conversation_id: conversationId,
    revision,
    history: [
      { role: 'user', content: revision === 1 ? 'Marcar reuniao' : 'Marketing' },
      { role: 'assistant', content: answer },
    ],
  };
}

function serverSession(
  userId: string,
  id: string,
  revision: number,
): DialogueSession {
  return {
    id,
    user_id: userId,
    revision,
    history: [
      { role: 'user', content: 'Marcar reuniao amanha as 15 horas' },
      { role: 'assistant', content: 'Qual e o titulo da reuniao?' },
    ],
    pending_intent: { type: 'calendar_create' },
    created_at: '2026-08-13T10:00:00Z',
    updated_at: '2026-08-13T10:01:00Z',
    expires_at: '2026-09-12T10:01:00Z',
  };
}

describe('DialogueSessionController', () => {
  it('serializes Marcar reuniao -> Marketing with one conversation and a fresh turn id', async () => {
    const ids = [
      '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
      '9e0a385a-5c60-42e7-ab32-616bab20476d',
      '0f6d2285-e898-4cb7-9349-90999999f085',
    ];
    const askQuestion = vi
      .fn()
      .mockResolvedValueOnce(result(ids[0], 1, 'Qual e o titulo da reuniao?'))
      .mockResolvedValueOnce(result(ids[0], 2, 'Reuniao Marketing pronta para confirmar.'));
    const controller = new DialogueSessionController('user-alex', {
      askQuestion,
      fetchRecent: vi.fn().mockResolvedValue({ session: null }),
      storage: new MemoryStorage(),
      createId: () => ids.shift()!,
    });

    const first = controller.submit('Marcar reuniao amanha as 15 horas');
    const second = controller.submit('Marketing');
    await Promise.all([first, second]);

    expect(askQuestion).toHaveBeenNthCalledWith(
      1,
      'Marcar reuniao amanha as 15 horas',
      undefined,
      {
        conversationId: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
        turnId: '9e0a385a-5c60-42e7-ab32-616bab20476d',
        expectedRevision: 0,
      },
    );
    expect(askQuestion).toHaveBeenNthCalledWith(2, 'Marketing', undefined, {
      conversationId: '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
      turnId: '0f6d2285-e898-4cb7-9349-90999999f085',
      expectedRevision: 1,
    });
  });

  it('restores the latest authenticated-user history from the server on another device', async () => {
    const latest = serverSession(
      'user-alex',
      '0f118c69-3a8c-4de8-a135-f5a1346fcc48',
      1,
    );
    const controller = new DialogueSessionController('user-alex', {
      askQuestion: vi.fn(),
      fetchRecent: vi.fn().mockResolvedValue({ session: latest }),
      storage: new MemoryStorage(),
      createId: vi.fn(),
    });

    await expect(controller.restore()).resolves.toMatchObject({
      conversationId: latest.id,
      revision: 1,
      history: latest.history,
    });
  });

  it('rejects a mismatched server tenant and never reads another user storage key', async () => {
    const storage = new MemoryStorage();
    storage.setItem(
      'oj-life-dialogue:user-alex',
      JSON.stringify({ conversationId: 'alex-conversation', revision: 7 }),
    );
    const controller = new DialogueSessionController('user-bruna', {
      askQuestion: vi.fn(),
      fetchRecent: vi.fn().mockResolvedValue({
        session: serverSession('user-alex', 'server-alex-conversation', 8),
      }),
      storage,
      createId: vi.fn(),
    });

    await expect(controller.restore()).resolves.toMatchObject({
      conversationId: null,
      revision: 0,
      history: [],
    });
    expect(storage.getItem('oj-life-dialogue:user-alex')).not.toBeNull();
    expect(storage.getItem('oj-life-dialogue:user-bruna')).toBeNull();
  });

  it('keeps specialist context separate without mutating conversation text', async () => {
    const ids = [
      '4e4004c8-2e4b-49d1-ad64-12f87ca99464',
      '680ddc2a-a096-4bd9-940b-143d5e9f3842',
      '14bf481a-b326-4bc0-b71d-4fc134d1c6e6',
    ];
    const askQuestion = vi
      .fn()
      .mockResolvedValueOnce(result(ids[0], 1, 'Vou analisar suas prioridades.'))
      .mockResolvedValueOnce(result(ids[0], 2, 'Comece pela reserva.'));
    const controller = new DialogueSessionController('user-alex', {
      askQuestion,
      fetchRecent: vi.fn().mockResolvedValue({ session: null }),
      storage: new MemoryStorage(),
      createId: () => ids.shift()!,
    });

    controller.beginSpecialistContext('finance');
    await controller.submit('O que devo priorizar?');
    await controller.submit('E depois?');

    expect(askQuestion.mock.calls[0]?.[0]).toBe('O que devo priorizar?');
    expect(askQuestion.mock.calls[0]?.[2]).toMatchObject({ specialist: 'finance' });
    expect(askQuestion.mock.calls[1]?.[0]).toBe('E depois?');
    expect(askQuestion.mock.calls[1]?.[2]).toMatchObject({ specialist: 'finance' });
  });
});
