import {
  ask,
  fetchRecentDialogue,
  type AskDialogueCursor,
  type AskResult,
  type DeviceContext,
  type DialogueMessage,
  type DialogueSession,
} from './api';
import type { ShellAppId } from './types';

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

interface DialogueSessionDependencies {
  askQuestion: (
    question: string,
    deviceContext?: DeviceContext,
    cursor?: AskDialogueCursor,
  ) => Promise<AskResult>;
  fetchRecent: () => Promise<{ session: DialogueSession | null }>;
  storage: StorageLike;
  createId: () => string;
}

export interface DialogueSnapshot {
  conversationId: string | null;
  revision: number;
  history: DialogueMessage[];
  contextApp: ShellAppId | null;
}

interface StoredCursor {
  conversationId: string;
  revision: number;
}

const SPECIALIST_BY_APP: Record<
  ShellAppId,
  NonNullable<AskDialogueCursor['specialist']>
> = {
  finance: 'finance',
  fitness: 'performance',
  routine: 'executive',
  family: 'family',
  work: 'executive',
  health: 'health',
  connections: 'executive',
};

function browserStorage(): StorageLike {
  try {
    return localStorage;
  } catch {
    return {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    };
  }
}

function createUuid(): string {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex
    .slice(6, 8)
    .join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}

function isStoredCursor(value: unknown): value is StoredCursor {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<StoredCursor>;
  return (
    typeof candidate.conversationId === 'string' &&
    candidate.conversationId.length > 0 &&
    Number.isInteger(candidate.revision) &&
    Number(candidate.revision) >= 0
  );
}

export class DialogueSessionController {
  private readonly storageKey: string;
  private readonly dependencies: DialogueSessionDependencies;
  private snapshot: DialogueSnapshot = {
    conversationId: null,
    revision: 0,
    history: [],
    contextApp: null,
  };
  private initialized = false;
  private restorePromise: Promise<DialogueSnapshot> | null = null;
  private sequence: Promise<void> = Promise.resolve();

  constructor(
    private readonly userId: string,
    dependencies: Partial<DialogueSessionDependencies> = {},
  ) {
    this.storageKey = `oj-life-dialogue:${encodeURIComponent(userId)}`;
    this.dependencies = {
      askQuestion: dependencies.askQuestion ?? ask,
      fetchRecent: dependencies.fetchRecent ?? fetchRecentDialogue,
      storage: dependencies.storage ?? browserStorage(),
      createId: dependencies.createId ?? createUuid,
    };
  }

  async restore(): Promise<DialogueSnapshot> {
    if (this.restorePromise) return this.restorePromise;
    this.restorePromise = this.restoreFromServer();
    return this.restorePromise;
  }

  beginSpecialistContext(app: ShellAppId): void {
    this.snapshot = {
      conversationId: null,
      revision: 0,
      history: [],
      contextApp: app,
    };
    this.initialized = true;
    this.restorePromise = Promise.resolve(this.current());
    this.removeStoredCursor();
  }

  submit(question: string, deviceContext?: DeviceContext): Promise<AskResult> {
    const trimmed = question.trim();
    if (!trimmed) return Promise.reject(new Error('Escreva uma mensagem para o Jarvis.'));

    const task = this.sequence.then(() => this.runTurn(trimmed, deviceContext));
    this.sequence = task.then(
      () => undefined,
      () => undefined,
    );
    return task;
  }

  current(): DialogueSnapshot {
    return {
      ...this.snapshot,
      history: [...this.snapshot.history],
    };
  }

  private async restoreFromServer(): Promise<DialogueSnapshot> {
    try {
      const { session } = await this.dependencies.fetchRecent();
      if (session?.user_id === this.userId) {
        this.snapshot = {
          conversationId: session.id,
          revision: session.revision,
          history: [...session.history],
          contextApp: null,
        };
        this.persistCursor();
      } else {
        this.snapshot = {
          conversationId: null,
          revision: 0,
          history: [],
          contextApp: null,
        };
        this.removeStoredCursor();
      }
    } catch {
      const local = this.readStoredCursor();
      if (local) {
        this.snapshot = {
          conversationId: local.conversationId,
          revision: local.revision,
          history: [],
          contextApp: null,
        };
      }
    }
    this.initialized = true;
    return this.current();
  }

  private async runTurn(
    question: string,
    deviceContext?: DeviceContext,
  ): Promise<AskResult> {
    if (!this.initialized) await this.restore();

    const conversationId =
      this.snapshot.conversationId ?? this.dependencies.createId();
    const turnId = this.dependencies.createId();
    const result = await this.dependencies.askQuestion(question, deviceContext, {
      conversationId,
      turnId,
      expectedRevision: this.snapshot.revision,
      ...(this.snapshot.contextApp
        ? { specialist: SPECIALIST_BY_APP[this.snapshot.contextApp] }
        : {}),
    });

    this.snapshot = {
      ...this.snapshot,
      conversationId: result.conversation_id ?? conversationId,
      revision: result.revision ?? this.snapshot.revision + 1,
      history:
        result.history ?? [
          ...this.snapshot.history,
          { role: 'user', content: question } as DialogueMessage,
          { role: 'assistant', content: result.answer } as DialogueMessage,
        ],
    };
    this.persistCursor();
    return result;
  }

  private readStoredCursor(): StoredCursor | null {
    try {
      const encoded = this.dependencies.storage.getItem(this.storageKey);
      if (!encoded) return null;
      const parsed: unknown = JSON.parse(encoded);
      return isStoredCursor(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }

  private persistCursor(): void {
    if (!this.snapshot.conversationId) return;
    try {
      this.dependencies.storage.setItem(
        this.storageKey,
        JSON.stringify({
          conversationId: this.snapshot.conversationId,
          revision: this.snapshot.revision,
        } satisfies StoredCursor),
      );
    } catch {
      // Server persistence remains authoritative when device storage is full.
    }
  }

  private removeStoredCursor(): void {
    try {
      this.dependencies.storage.removeItem(this.storageKey);
    } catch {
      // A blocked storage API must not block Jarvis itself.
    }
  }
}
