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
  CoachOverview,
  CoachSessionDetail,
  FamilyMember,
  FamilyUpcoming,
  FinanceSummary,
  FinancialDocument,
  FitnessSummary,
  Habit,
  HealthProfile,
  HealthSummary,
  HydrationLog,
  IntegrationConnection,
  IntegrationsOverview,
  LifeUser,
  RoutineSummary,
  Today,
  TrainingCheckin,
  TrainingCheckinInput,
  TrainingCompletionInput,
  TrainingFeedback,
  TrainingPlan,
  TrainingProfile,
  TrainingProfileInput,
  WhatsAppChannelStatus,
  WhatsAppBriefingPreference,
  WhatsAppLinkPending,
  WorkSummary,
} from './types';

const TOKEN_KEY = 'oj-life-token';

export class LifeApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown = message,
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
    let detail: unknown = `Erro ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail !== undefined) detail = body.detail;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    const message =
      typeof detail === 'string'
        ? detail
        : detail &&
            typeof detail === 'object' &&
            typeof (detail as { message?: unknown }).message === 'string'
          ? (detail as { message: string }).message
          : `Erro ${response.status}`;
    // An expired or revoked token must drop the session rather than leave the
    // app retrying forever against a credential the server has forgotten.
    if (response.status === 401) setToken('');
    throw new LifeApiError(message, response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) });
const del = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: 'DELETE',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

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
  conversation_id?: string;
  revision?: number;
  history?: DialogueMessage[];
}

export interface DialogueMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface DialogueSession {
  id: string;
  user_id: string;
  revision: number;
  history: DialogueMessage[];
  pending_intent: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
}

export interface AskDialogueCursor {
  conversationId: string;
  turnId: string;
  expectedRevision: number;
  specialist?: JarvisSpecialist;
}

export type JarvisSpecialist =
  | 'finance'
  | 'performance'
  | 'health'
  | 'family'
  | 'executive';

export interface DeviceCalendarEventContext {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  is_all_day: boolean;
  location: string;
  calendar_title: string;
}

export interface DeviceContext {
  calendar_events: DeviceCalendarEventContext[];
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
  tool_name: 'life_record' | 'life_complete' | 'calendar_create';
  summary: string;
  status: 'pending' | 'executing' | 'confirmed' | 'canceled' | 'failed' | 'expired';
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string;
  expires_at: string;
}

/** Ask the assistant a question grounded in this client's own life data. */
export const ask = (
  question: string,
  deviceContext?: DeviceContext,
  dialogue?: AskDialogueCursor,
) =>
  post<AskResult>('/ask', {
    question,
    ...(deviceContext ? { device_context: deviceContext } : {}),
    ...(dialogue
      ? {
          conversation_id: dialogue.conversationId,
          turn_id: dialogue.turnId,
          expected_revision: dialogue.expectedRevision,
          ...(dialogue.specialist ? { specialist: dialogue.specialist } : {}),
        }
      : {}),
  });

export const fetchRecentDialogue = () =>
  request<{ session: DialogueSession | null }>('/dialogue/recent', {
    cache: 'no-store',
  });

export const listPendingActions = () =>
  get<{ count: number; proposals: JarvisActionProposal[] }>('/actions/pending');

export type ConfirmationMethod = 'explicit' | 'voice_explicit';

export interface AppAttestProof {
  /** Native-only routing metadata; never serialized to the backend. */
  accountId?: string;
  deviceId: string;
  challengeId: string;
  challenge: string;
  keyId: string;
  assertion: string;
}

export interface StrongAuthApproval {
  confirmationMethod: ConfirmationMethod;
  proof: AppAttestProof;
}

function privilegedMutationFields(approval?: StrongAuthApproval) {
  if (!approval) return {};
  return {
    confirmed: true,
    confirmation_method: approval.confirmationMethod,
    strong_auth: {
      device_id: approval.proof.deviceId,
      challenge_id: approval.proof.challengeId,
      challenge: approval.proof.challenge,
      key_id: approval.proof.keyId,
      assertion: approval.proof.assertion,
    },
  };
}

export const confirmAction = (
  proposalId: string,
  confirmationMethod: ConfirmationMethod = 'explicit',
  strongAuth?: AppAttestProof,
) =>
  post<{ proposal: JarvisActionProposal; replayed: boolean }>(
    `/actions/${proposalId}/confirm`,
    {
      confirmed: true,
      confirmation_method: confirmationMethod,
      ...(strongAuth
        ? {
            strong_auth: {
              device_id: strongAuth.deviceId,
              challenge_id: strongAuth.challengeId,
              challenge: strongAuth.challenge,
              key_id: strongAuth.keyId,
              assertion: strongAuth.assertion,
            },
          }
        : {}),
    },
  );

export const prepareNativeAction = (
  proposalId: string,
  confirmationMethod: ConfirmationMethod,
  proof: AppAttestProof,
) =>
  post<{
    proposal: JarvisActionProposal;
    replayed: boolean;
    claim: { device_id: string; expires_at: string } | null;
    claim_token: string;
  }>(`/actions/${proposalId}/prepare-native`, {
    confirmed: true,
    device_id: proof.deviceId,
    confirmation_method: confirmationMethod,
    app_attest: {
      challenge_id: proof.challengeId,
      challenge: proof.challenge,
      key_id: proof.keyId,
      assertion: proof.assertion,
    },
  });

export interface NativeCalendarEventReceipt {
  id: string;
  title: string;
  startAt: string;
  endAt: string;
  isAllDay: boolean;
  location: string;
  calendarTitle: string;
}

export const resolveNativeAction = (
  proposalId: string,
  deviceId: string,
  claimToken: string,
  proof: Pick<AppAttestProof, 'keyId' | 'assertion'>,
  event: NativeCalendarEventReceipt,
) =>
  post<{ proposal: JarvisActionProposal; replayed: boolean }>(
    `/actions/${proposalId}/resolve-native`,
    {
      confirmed: true,
      success: true,
      device_id: deviceId,
      claim_token: claimToken,
      app_attest: {
        key_id: proof.keyId,
        assertion: proof.assertion,
      },
      result: { event },
    },
  );

export const cancelAction = (proposalId: string) =>
  post<{ proposal: JarvisActionProposal }>(`/actions/${proposalId}/cancel`);

// -- Home -------------------------------------------------------------------

export const fetchToday = () => get<Today>('/today');

export const fetchApps = () =>
  get<{ apps: AppManifestEntry[]; badges: Badges }>('/apps');

export const fetchFinanceSummary = () => get<FinanceSummary>('/summary/finance');
export const listFinancialDocuments = () =>
  get<{ documents: FinancialDocument[] }>('/finance/documents');
export interface FinancialDocumentAnalysisResult {
  document: FinancialDocument;
  proposals: JarvisActionProposal[];
  replayed: boolean;
}
export const analyzeFinancialDocument = (input: {
  filename: string;
  contentType: string;
  dataBase64: string;
}) =>
  post<FinancialDocumentAnalysisResult>('/finance/documents/analyze', {
    filename: input.filename,
    content_type: input.contentType,
    data_base64: input.dataBase64,
  });
export const fetchFitnessSummary = () => get<FitnessSummary>('/summary/fitness');
export const fetchCoachOverview = () => get<CoachOverview>('/coach');
export const fetchCoachSession = (sessionId: string) =>
  get<CoachSessionDetail>(`/coach/sessions/${sessionId}`);
export const saveCoachProfile = (profile: TrainingProfileInput) =>
  put<{ profile: TrainingProfile }>('/coach/profile', profile);
export const generateCoachPlan = (startOn: string, weeks: number) =>
  post<{ plan: TrainingPlan }>('/coach/plans', {
    start_on: startOn,
    weeks,
  });
export const submitCoachCheckin = (
  sessionId: string,
  input: TrainingCheckinInput,
) =>
  post<{ checkin: TrainingCheckin }>(
    `/coach/sessions/${sessionId}/check-in`,
    input,
  );
export const completeCoachSession = (
  sessionId: string,
  input: TrainingCompletionInput,
) =>
  post<{
    replayed: boolean;
    feedback: TrainingFeedback;
    adaptation: TrainingFeedback['adaptation'];
  }>(`/coach/sessions/${sessionId}/complete`, input);
export const fetchRoutineSummary = () => get<RoutineSummary>('/summary/routine');
export const fetchWorkSummary = () => get<WorkSummary>('/summary/work');
export const fetchFamilySummary = () =>
  get<{ upcoming: FamilyUpcoming[] }>('/summary/family');
export const fetchHealthSummary = () => get<HealthSummary>('/summary/health');

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
  | 'work_tasks'
  | 'health_profiles'
  | 'health_conditions'
  | 'medications'
  | 'allergies'
  | 'health_observations'
  | 'hydration_logs'
  | 'nutrition_logs'
  | 'health_documents';

export function listRecords<T>(
  table: TableName,
  params: Record<string, string | number | boolean> = {},
): Promise<{ table: string; count: number; records: T[] }> {
  const query = new URLSearchParams(
    Object.entries(params).map(([key, value]) => [key, String(value)]),
  ).toString();
  return get(`/records/${table}${query ? `?${query}` : ''}`);
}

export const createRecord = <T>(
  table: TableName,
  fields: Record<string, unknown>,
  approval?: StrongAuthApproval,
  operationId = '',
) =>
  post<{ record: T }>(`/records/${table}`, {
    fields,
    ...(operationId ? { operation_id: operationId } : {}),
    ...privilegedMutationFields(approval),
  });

export const updateRecord = <T>(
  table: TableName,
  id: string,
  fields: Record<string, unknown>,
  approval?: StrongAuthApproval,
  operationId = '',
) =>
  patch<{ record: T }>(`/records/${table}/${id}`, {
    fields,
    ...(operationId ? { operation_id: operationId } : {}),
    ...privilegedMutationFields(approval),
  });

export const deleteRecord = (
  table: TableName,
  id: string,
  approval?: StrongAuthApproval,
  operationId = '',
) =>
  del<{ deleted: string }>(
    `/records/${table}/${id}`,
    operationId || approval
      ? {
          ...(operationId ? { operation_id: operationId } : {}),
          ...privilegedMutationFields(approval),
        }
      : undefined,
  );

/** Create the single health profile or patch its existing tenant-owned row. */
export const saveHealthProfile = (
  profileId: string | null,
  fields: Record<string, unknown>,
) =>
  profileId
    ? updateRecord<HealthProfile>('health_profiles', profileId, fields)
    : createRecord<HealthProfile>('health_profiles', fields);

/** Record one manual water intake at an explicit instant. */
export const recordHydration = (
  amountMl: number,
  occurredAt = new Date().toISOString(),
) =>
  createRecord<HydrationLog>('hydration_logs', {
    amount_ml: amountMl,
    occurred_at: occurredAt,
    source: 'manual',
  });

// -- Actions ----------------------------------------------------------------

export const payBill = (
  billId: string,
  accountId = '',
  approval?: StrongAuthApproval,
  operationId = '',
) =>
  post<{ bill: Bill; transaction_id: string; next_bill_id: string }>(
    `/actions/pay-bill/${billId}`,
    {
      account_id: accountId,
      ...(operationId ? { operation_id: operationId } : {}),
      ...privilegedMutationFields(approval),
    },
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

// -- Integrações -------------------------------------------------------------

export const fetchIntegrations = () =>
  request<IntegrationsOverview>('/integrations', { cache: 'no-store' });

/**
 * Inicia a conexão. O backend devolve a URL real de autorização do provedor;
 * nada fica "conectado" até o callback concluir a troca — o app só deve
 * redirecionar e, na volta, reler o catálogo.
 */
export const connectIntegration = (provider: string) =>
  post<{ provider: string; authorize_url: string; state: string; expires_at: string }>(
    `/integrations/${provider}/connect`,
  );

export interface IntegrationSyncResult {
  provider: string;
  synced: number;
  last_sync_at: string;
}

/** Import bounded provider data into the authenticated client's Jarvis context. */
export const syncIntegration = (provider: string) =>
  post<IntegrationSyncResult>(`/integrations/${provider}/sync`);

export interface NativeDeviceGrant {
  grantedScopes: string[];
  deviceId: string;
  deviceLabel: string;
}

export interface DeviceAttestationIdentity {
  accountId: string;
  deviceId: string;
  deviceLabel: string;
  keyId: string;
}

export interface DeviceAttestationChallenge {
  challenge_id: string;
  challenge: string;
  expires_at: string;
  client_data?: string;
}

export const issueDeviceAttestationChallenge = (
  purpose: 'attest' | 'device_grant' | 'native_action' | 'finance',
  deviceId: string,
  resourceId = '',
  confirmationMethod: '' | ConfirmationMethod = '',
) =>
  post<DeviceAttestationChallenge>('/device-attestation/challenge', {
    purpose,
    device_id: deviceId,
    resource_id: resourceId,
    confirmation_method: confirmationMethod,
  });

export const deviceAttestationStatus = (identity: DeviceAttestationIdentity) =>
  post<{ registered: boolean }>('/device-attestation/status', {
    device_id: identity.deviceId,
    key_id: identity.keyId,
  });

export const registerDeviceAttestation = (
  identity: DeviceAttestationIdentity,
  challenge: DeviceAttestationChallenge,
  attestationObject: string,
) =>
  post<{ device_id: string; key_id: string; attested: boolean }>(
    '/device-attestation/register',
    {
      challenge_id: challenge.challenge_id,
      challenge: challenge.challenge,
      device_id: identity.deviceId,
      device_label: identity.deviceLabel,
      key_id: identity.keyId,
      attestation_object: attestationObject,
    },
  );

/** Persist the permission already granted by the native iPhone system sheet. */
export const registerDeviceGrant = (
  provider: string,
  grant: NativeDeviceGrant,
  proof: AppAttestProof,
) =>
  post<{ provider: string; connection: IntegrationConnection }>(
    `/integrations/${provider}/device-grant`,
    {
      granted_scopes: grant.grantedScopes,
      device_id: grant.deviceId,
      device_label: grant.deviceLabel,
      app_attest: {
        challenge_id: proof.challengeId,
        challenge: proof.challenge,
        key_id: proof.keyId,
        assertion: proof.assertion,
      },
    },
  );

/** Cancela a autorização pendente e/ou revoga a conexão do provedor. */
export const disconnectIntegration = (provider: string) =>
  del<{ provider: string; result: 'revoked' | 'canceled' }>(
    `/integrations/${provider}`,
  );

export async function fetchWhatsAppChannel(): Promise<WhatsAppChannelStatus> {
  try {
    return await get<WhatsAppChannelStatus>('/channels/whatsapp');
  } catch (error) {
    if (error instanceof LifeApiError && error.status === 404) {
      return { status: 'disconnected' };
    }
    throw error;
  }
}

export const linkWhatsApp = (phone: string) =>
  post<WhatsAppLinkPending>('/channels/whatsapp/link', { phone });

export const revokeWhatsApp = () =>
  del<{ status: 'revoked' }>('/channels/whatsapp');

export async function fetchWhatsAppBriefing(): Promise<WhatsAppBriefingPreference> {
  const result = await get<{ briefing: WhatsAppBriefingPreference }>(
    '/channels/whatsapp/briefing',
  );
  return result.briefing;
}

export async function saveWhatsAppBriefing(
  briefing: WhatsAppBriefingPreference,
): Promise<WhatsAppBriefingPreference> {
  const result = await put<{ briefing: WhatsAppBriefingPreference }>(
    '/channels/whatsapp/briefing',
    briefing,
  );
  return result.briefing;
}

// -- Convenience loaders used by the app screens ----------------------------

export const listAccounts = () => listRecords<Account>('accounts', { archived: 0 });

export const listBills = () =>
  listRecords<Bill>('bills', { order_by: 'due_on', desc: false, limit: 100 });

export const listHabits = () => listRecords<Habit>('habits', { archived: 0 });

export const listFamily = () =>
  listRecords<FamilyMember>('family_members', { limit: 200 });

/** Apps whose data a mutation invalidates, so screens can refresh precisely. */
export const APP_IDS: AppId[] = [
  'finance',
  'fitness',
  'routine',
  'family',
  'work',
  'health',
];
