/**
 * Client for the Life API.
 *
 * Paths are relative so the same bundle works in dev (Vite proxies `/v1` to
 * the backend) and in production (FastAPI serves this bundle from its own
 * origin). The bearer token is the *user's*, not the server's — see
 * `openjarvis/life/tenancy.py`.
 */

import type {
  Account,
  AppId,
  AppManifestEntry,
  Badges,
  Bill,
  FamilyMember,
  FamilyUpcoming,
  FinanceSummary,
  FitnessSummary,
  Habit,
  LifeUser,
  RoutineSummary,
  Today,
  WorkSummary,
} from './types';

const TOKEN_KEY = 'oj-life-token';

export class LifeApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'LifeApiError';
  }
}

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private browsing — the session simply won't persist */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`/v1/life${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body — keep the status line */
    }
    // An expired or revoked token must drop the session rather than leave the
    // app retrying forever against a credential the server has forgotten.
    if (response.status === 401) setToken('');
    throw new LifeApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

// -- Identity ---------------------------------------------------------------

export interface AuthResult {
  token: string;
  user: LifeUser;
}

export async function register(input: {
  email: string;
  password: string;
  name?: string;
}): Promise<AuthResult> {
  const result = await post<AuthResult>('/auth/register', input);
  setToken(result.token);
  return result;
}

export async function login(email: string, password: string): Promise<AuthResult> {
  const result = await post<AuthResult>('/auth/login', {
    email,
    password,
    device: navigator.userAgent.slice(0, 60),
  });
  setToken(result.token);
  return result;
}

export async function logout(): Promise<void> {
  try {
    await post('/auth/logout');
  } finally {
    // Clear locally even if the network call failed: the user asked to leave.
    setToken('');
  }
}

export const fetchMe = () => get<{ user: LifeUser }>('/me');

// -- Assistant --------------------------------------------------------------

export interface AskResult {
  answer: string;
  /** `model` when an engine answered, `data` when built from the records. */
  source: 'model' | 'data';
  context: Today;
  proposals?: JarvisActionProposal[];
  usage?: Record<string, number | boolean>;
  budget?: AiBudgetSnapshot;
}

export interface AiBudgetSnapshot {
  month: string;
  cap_microusd: number;
  spent_microusd: number;
  reserved_microusd: number;
  remaining_microusd: number;
}

export interface JarvisActionProposal {
  id: string;
  tool_name: 'life_record' | 'life_complete';
  summary: string;
  status: 'pending' | 'confirmed' | 'canceled' | 'failed' | 'expired';
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string;
  expires_at: string;
}

/** Ask the assistant a question grounded in this client's own life data. */
export const ask = (question: string) => post<AskResult>('/ask', { question });

export const listPendingActions = () =>
  get<{ count: number; proposals: JarvisActionProposal[] }>('/actions/pending');

export type ConfirmationMethod = 'explicit' | 'voice_explicit';

export const confirmAction = (
  proposalId: string,
  confirmationMethod: ConfirmationMethod = 'explicit',
) =>
  post<{ proposal: JarvisActionProposal; replayed: boolean }>(
    `/actions/${proposalId}/confirm`,
    { confirmed: true, confirmation_method: confirmationMethod },
  );

export const cancelAction = (proposalId: string) =>
  post<{ proposal: JarvisActionProposal }>(`/actions/${proposalId}/cancel`);

// -- Home -------------------------------------------------------------------

export const fetchToday = () => get<Today>('/today');

export const fetchApps = () =>
  get<{ apps: AppManifestEntry[]; badges: Badges }>('/apps');

export const fetchFinanceSummary = () => get<FinanceSummary>('/summary/finance');
export const fetchFitnessSummary = () => get<FitnessSummary>('/summary/fitness');
export const fetchRoutineSummary = () => get<RoutineSummary>('/summary/routine');
export const fetchWorkSummary = () => get<WorkSummary>('/summary/work');
export const fetchFamilySummary = () =>
  get<{ upcoming: FamilyUpcoming[] }>('/summary/family');

// -- Records ----------------------------------------------------------------

export type TableName =
  | 'accounts'
  | 'transactions'
  | 'bills'
  | 'budgets'
  | 'goals'
  | 'workouts'
  | 'exercise_sets'
  | 'measurements'
  | 'habits'
  | 'habit_checkins'
  | 'family_members'
  | 'family_events'
  | 'projects'
  | 'work_tasks';

export function listRecords<T>(
  table: TableName,
  params: Record<string, string | number | boolean> = {},
): Promise<{ table: string; count: number; records: T[] }> {
  const query = new URLSearchParams(
    Object.entries(params).map(([key, value]) => [key, String(value)]),
  ).toString();
  return get(`/records/${table}${query ? `?${query}` : ''}`);
}

export const createRecord = <T>(table: TableName, fields: Record<string, unknown>) =>
  post<{ record: T }>(`/records/${table}`, { fields });

export const updateRecord = <T>(
  table: TableName,
  id: string,
  fields: Record<string, unknown>,
) => patch<{ record: T }>(`/records/${table}/${id}`, { fields });

export const deleteRecord = (table: TableName, id: string) =>
  del<{ deleted: string }>(`/records/${table}/${id}`);

// -- Actions ----------------------------------------------------------------

export const payBill = (billId: string, accountId = '') =>
  post<{ bill: Bill; transaction_id: string; next_bill_id: string }>(
    `/actions/pay-bill/${billId}`,
    { account_id: accountId },
  );

export const completeWorkout = (workoutId: string, durationMin = 0) =>
  post<{ workout: unknown }>(`/actions/complete-workout/${workoutId}`, {
    duration_min: durationMin,
  });

export const checkHabit = (habitId: string) =>
  post<{ created: boolean; streak: number }>(`/actions/check-habit/${habitId}`, {});

export const uncheckHabit = (habitId: string) =>
  del<{ removed: boolean; streak: number }>(`/actions/check-habit/${habitId}`);

export const completeTask = (taskId: string) =>
  post<{ task: unknown }>(`/actions/complete-task/${taskId}`);

// -- Convenience loaders used by the app screens ----------------------------

export const listAccounts = () => listRecords<Account>('accounts', { archived: 0 });

export const listBills = () =>
  listRecords<Bill>('bills', { order_by: 'due_on', desc: false, limit: 100 });

export const listHabits = () => listRecords<Habit>('habits', { archived: 0 });

export const listFamily = () =>
  listRecords<FamilyMember>('family_members', { limit: 200 });

/** Apps whose data a mutation invalidates, so screens can refresh precisely. */
export const APP_IDS: AppId[] = ['finance', 'fitness', 'routine', 'family', 'work'];
