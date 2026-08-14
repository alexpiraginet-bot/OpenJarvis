import {
  deviceAttestationStatus,
  fetchMe,
  issueDeviceAttestationChallenge,
  prepareNativeAction,
  registerDeviceAttestation,
  registerDeviceGrant,
  resolveNativeAction,
  LifeApiError,
  type AppAttestProof,
  type ConfirmationMethod,
  type DeviceContext,
  type DeviceAttestationIdentity,
  type JarvisActionProposal,
  type NativeCalendarEventReceipt,
  type NativeDeviceGrant,
  type StrongAuthApproval,
} from './api';

const NATIVE_EVENT = 'jarvis-native-integration';
const DEFAULT_TIMEOUT_MS = 45_000;

type NativeMessage = Record<string, unknown> & {
  action: string;
  requestId: string;
};

interface NativeIntegrationHandler {
  postMessage(message: NativeMessage): void;
}

export interface NativeBridgeHost {
  webkit?: {
    messageHandlers?: {
      jarvisIntegrations?: NativeIntegrationHandler;
    };
  };
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
}

interface NativeIntegrationEventDetail {
  type?: unknown;
  status?: unknown;
  provider?: unknown;
  requestId?: unknown;
  grantedScopes?: unknown;
  deviceId?: unknown;
  deviceLabel?: unknown;
  keyId?: unknown;
  attestationObject?: unknown;
  assertion?: unknown;
  code?: unknown;
  events?: unknown;
  rotated?: unknown;
  receiptHmac?: unknown;
  event?: unknown;
  message?: unknown;
}

interface NativeAttestation {
  keyId: string;
  attestationObject: string;
}

interface NativeAssertion {
  keyId: string;
  assertion: string;
}

interface NativeCalendarSave {
  event: NativeCalendarEventReceipt;
  keyId: string;
  assertion: string;
}

export class NativeBridgeError extends Error {
  constructor(message: string, readonly code = '') {
    super(message);
    this.name = 'NativeBridgeError';
  }
}

function browserHost(): NativeBridgeHost | null {
  return typeof window === 'undefined'
    ? null
    : (window as unknown as NativeBridgeHost);
}

function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `native-${Date.now()}`;
}

function bridge(host: NativeBridgeHost | null): NativeIntegrationHandler {
  const handler = host?.webkit?.messageHandlers?.jarvisIntegrations;
  if (!host || !handler) {
    throw new Error('Abra o app iOS do Jarvis para concluir esta ação.');
  }
  return handler;
}

function messageFrom(detail: NativeIntegrationEventDetail): string {
  return typeof detail.message === 'string' && detail.message.trim()
    ? detail.message
    : 'O iPhone não conseguiu concluir esta ação.';
}

function nativeRequest<T>(
  message: Omit<NativeMessage, 'requestId'>,
  expectedType: string,
  parse: (detail: NativeIntegrationEventDetail) => T | null,
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  let handler: NativeIntegrationHandler;
  try {
    handler = bridge(host);
  } catch (error) {
    return Promise.reject(error);
  }

  return new Promise((resolve, reject) => {
    let timeout: ReturnType<typeof setTimeout>;
    const cleanup = () => {
      clearTimeout(timeout);
      host!.removeEventListener(NATIVE_EVENT, onNativeIntegration);
    };
    const onNativeIntegration: EventListener = (event) => {
      const detail = (event as CustomEvent<NativeIntegrationEventDetail>).detail;
      if (
        !detail ||
        detail.type !== expectedType ||
        detail.requestId !== requestId
      ) {
        return;
      }

      cleanup();
      if (detail.status !== 'ready' && detail.status !== 'granted' && detail.status !== 'saved') {
        reject(
          new NativeBridgeError(
            messageFrom(detail),
            typeof detail.code === 'string' ? detail.code : '',
          ),
        );
        return;
      }
      const parsed = parse(detail);
      if (parsed === null) {
        reject(new Error('O iPhone devolveu uma resposta inválida.'));
        return;
      }
      resolve(parsed);
    };
    timeout = setTimeout(() => {
      cleanup();
      reject(new Error('O iPhone não respondeu à solicitação.'));
    }, timeoutMs);

    host!.addEventListener(NATIVE_EVENT, onNativeIntegration);
    try {
      handler.postMessage({ ...message, requestId } as NativeMessage);
    } catch {
      cleanup();
      reject(new Error('Não foi possível acionar a proteção nativa do iPhone.'));
    }
  });
}

function nonEmptyString(value: unknown, maximum = 65_536): string | null {
  if (typeof value !== 'string') return null;
  const result = value.trim();
  return result && result.length <= maximum ? result : null;
}

function parseIdentity(
  detail: NativeIntegrationEventDetail,
  accountId: string,
): DeviceAttestationIdentity | null {
  const deviceId = nonEmptyString(detail.deviceId, 128);
  const deviceLabel = nonEmptyString(detail.deviceLabel, 120);
  const keyId = nonEmptyString(detail.keyId, 128);
  if (!deviceId || deviceId.length < 8 || !deviceLabel || !keyId || keyId.length < 32) {
    return null;
  }
  return { accountId, deviceId, deviceLabel, keyId };
}

function parseGrant(detail: NativeIntegrationEventDetail): NativeDeviceGrant | null {
  if (!Array.isArray(detail.grantedScopes)) return null;
  const deviceId = nonEmptyString(detail.deviceId, 128);
  const deviceLabel = nonEmptyString(detail.deviceLabel, 120);
  const grantedScopes = detail.grantedScopes.filter(
    (scope): scope is string => typeof scope === 'string' && scope.trim().length > 0,
  );
  if (!deviceId || deviceId.length < 8 || !deviceLabel || grantedScopes.length === 0) {
    return null;
  }
  return { grantedScopes, deviceId, deviceLabel };
}

function parseEvent(value: unknown): NativeCalendarEventReceipt | null {
  if (!value || typeof value !== 'object') return null;
  const event = value as Record<string, unknown>;
  const id = nonEmptyString(event.id, 512);
  const title = nonEmptyString(event.title, 160);
  const startAt = nonEmptyString(event.startAt, 64);
  const endAt = nonEmptyString(event.endAt, 64);
  if (!id || !title || !startAt || !endAt || typeof event.isAllDay !== 'boolean') {
    return null;
  }
  return {
    id,
    title,
    startAt,
    endAt,
    isAllDay: event.isAllDay,
    location: typeof event.location === 'string' ? event.location.slice(0, 200) : '',
    calendarTitle:
      typeof event.calendarTitle === 'string'
        ? event.calendarTitle.slice(0, 160)
        : '',
  };
}

export function requestNativeDeviceIdentity(
  accountId: string,
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<DeviceAttestationIdentity> {
  return nativeRequest(
    { action: 'identity', accountId },
    'deviceIdentity',
    (detail) => parseIdentity(detail, accountId),
    host,
    requestId,
  );
}

export function requestNativeAppAttestation(
  identity: DeviceAttestationIdentity,
  challenge: string,
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<NativeAttestation> {
  return nativeRequest(
    {
      action: 'attest',
      accountId: identity.accountId,
      keyId: identity.keyId,
      challenge,
    },
    'appAttestation',
    (detail) => {
      const keyId = nonEmptyString(detail.keyId, 128);
      const attestationObject = nonEmptyString(detail.attestationObject);
      if (!keyId || !attestationObject) return null;
      const acceptedKey = keyId === identity.keyId || detail.rotated === true;
      return acceptedKey ? { keyId, attestationObject } : null;
    },
    host,
    requestId,
  );
}

export function requestNativeAssertion(
  identity: DeviceAttestationIdentity,
  clientData: string,
  requireBiometric: boolean,
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<NativeAssertion> {
  return nativeRequest(
    {
      action: 'assert',
      accountId: identity.accountId,
      keyId: identity.keyId,
      clientData,
      requireBiometric,
    },
    'appAssertion',
    (detail) => {
      const keyId = nonEmptyString(detail.keyId, 128);
      const assertion = nonEmptyString(detail.assertion, 16_384);
      return keyId === identity.keyId && assertion ? { keyId, assertion } : null;
    },
    host,
    requestId,
  );
}

/** Ask EventKit through the WKWebView bridge and await the correlated result. */
export function requestNativeCalendarGrant(
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<NativeDeviceGrant> {
  return nativeRequest(
    { action: 'requestPermission', provider: 'apple_calendar' },
    'deviceGrant',
    parseGrant,
    host,
    requestId,
  );
}

function parseCalendarContextEvent(value: unknown) {
  if (!value || typeof value !== 'object') return null;
  const event = value as Record<string, unknown>;
  const id = nonEmptyString(event.id, 512);
  const title = nonEmptyString(event.title, 160);
  const startAt = nonEmptyString(event.start_at, 64);
  const endAt = nonEmptyString(event.end_at, 64);
  if (
    !id ||
    !title ||
    !startAt ||
    !endAt ||
    typeof event.is_all_day !== 'boolean' ||
    !Number.isFinite(Date.parse(startAt)) ||
    !Number.isFinite(Date.parse(endAt)) ||
    Date.parse(endAt) <= Date.parse(startAt)
  ) {
    return null;
  }
  return {
    id,
    title,
    start_at: startAt,
    end_at: endAt,
    is_all_day: event.is_all_day,
    location: typeof event.location === 'string' ? event.location.slice(0, 200) : '',
    calendar_title:
      typeof event.calendar_title === 'string'
        ? event.calendar_title.slice(0, 160)
        : '',
  };
}

export function requestNativeCalendarContext(
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<DeviceContext> {
  return nativeRequest(
    { action: 'readCalendarEvents' },
    'calendarEvents',
    (detail) => {
      if (!Array.isArray(detail.events)) return null;
      const parsed = detail.events
        .slice(0, 10)
        .map(parseCalendarContextEvent);
      return parsed.every((event) => event !== null)
        ? { calendar_events: parsed as DeviceContext['calendar_events'] }
        : null;
    },
    host,
    requestId,
  );
}

export function clearNativeCalendarReceiptCache(
  accountId: string,
  proposalId = '',
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<void> {
  return nativeRequest(
    {
      action: 'clearCalendarReceipts',
      accountId,
      ...(proposalId ? { proposalId } : {}),
    },
    'calendarReceiptsCleared',
    () => undefined,
    host,
    requestId,
  );
}

export async function clearCurrentAccountNativeCalendarReceiptCache(
  host: NativeBridgeHost | null = browserHost(),
): Promise<void> {
  const { user } = await fetchMe();
  await clearNativeCalendarReceiptCache(user.id, '', host);
}

export function requestNativeCalendarSave(
  proposal: JarvisActionProposal,
  claimToken: string,
  identity: DeviceAttestationIdentity,
  host: NativeBridgeHost | null = browserHost(),
  requestId = newRequestId(),
): Promise<NativeCalendarSave> {
  return nativeRequest(
    {
      action: 'createCalendarEvent',
      proposalId: proposal.id,
      claimToken,
      accountId: identity.accountId,
      keyId: identity.keyId,
      event: proposal.arguments,
    },
    'calendarEvent',
    (detail) => {
      const event = parseEvent(detail.event);
      const keyId = nonEmptyString(detail.keyId, 128);
      const assertion = nonEmptyString(detail.assertion, 16_384);
      return event && keyId === identity.keyId && assertion
        ? { event, keyId, assertion }
        : null;
    },
    host,
    requestId,
  );
}

export async function ensureNativeDeviceAttestation(
  host: NativeBridgeHost | null = browserHost(),
): Promise<DeviceAttestationIdentity> {
  const { user } = await fetchMe();
  const identity = await requestNativeDeviceIdentity(user.id, host);
  const status = await deviceAttestationStatus(identity);
  if (status.registered) return identity;

  const challenge = await issueDeviceAttestationChallenge(
    'attest',
    identity.deviceId,
    identity.deviceId,
  );
  const attestation = await requestNativeAppAttestation(
    identity,
    challenge.challenge,
    host,
  );
  const attestedIdentity = { ...identity, keyId: attestation.keyId };
  await registerDeviceAttestation(
    attestedIdentity,
    challenge,
    attestation.attestationObject,
  );
  return attestedIdentity;
}

function isInvalidAppAttestKey(error: unknown): boolean {
  return error instanceof NativeBridgeError && error.code === 'app_attest_key_invalid';
}

export async function buildStrongAuthProof(
  purpose: 'device_grant' | 'native_action' | 'finance',
  resourceId: string,
  confirmationMethod: '' | ConfirmationMethod,
  requireBiometric: boolean,
  host: NativeBridgeHost | null = browserHost(),
): Promise<AppAttestProof> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const identity = await ensureNativeDeviceAttestation(host);
    const challenge = await issueDeviceAttestationChallenge(
      purpose,
      identity.deviceId,
      resourceId,
      confirmationMethod,
    );
    if (!challenge.client_data) {
      throw new Error('O servidor não vinculou a autenticação a esta ação.');
    }
    try {
      const signed = await requestNativeAssertion(
        identity,
        challenge.client_data,
        requireBiometric,
        host,
      );
      return {
        accountId: identity.accountId,
        deviceId: identity.deviceId,
        challengeId: challenge.challenge_id,
        challenge: challenge.challenge,
        keyId: signed.keyId,
        assertion: signed.assertion,
      };
    } catch (error) {
      if (attempt === 0 && isInvalidAppAttestKey(error)) continue;
      throw error;
    }
  }
  throw new Error('A identidade segura deste iPhone não pôde ser renovada.');
}

interface FinanceChallengeDetail {
  message: string;
  purpose: 'finance';
  resource_id: string;
  confirmation_method: ConfirmationMethod;
}

type StrongAuthProofFactory = typeof buildStrongAuthProof;
type OperationIdFactory = () => string;

export function newFinanceOperationId(): string {
  const operationId = globalThis.crypto?.randomUUID?.();
  if (!operationId) {
    throw new Error('Este navegador não oferece um gerador seguro para a operação.');
  }
  return operationId;
}

function financeChallenge(error: unknown): FinanceChallengeDetail | null {
  if (!(error instanceof LifeApiError) || error.status !== 409) return null;
  if (!error.detail || typeof error.detail !== 'object') return null;
  const detail = error.detail as Record<string, unknown>;
  if (
    detail.purpose !== 'finance' ||
    typeof detail.resource_id !== 'string' ||
    !detail.resource_id ||
    (detail.confirmation_method !== 'explicit' &&
      detail.confirmation_method !== 'voice_explicit')
  ) {
    return null;
  }
  return {
    message: typeof detail.message === 'string' ? detail.message : error.message,
    purpose: 'finance',
    resource_id: detail.resource_id,
    confirmation_method: detail.confirmation_method,
  };
}

/** Retry a Finance mutation once with the exact challenge bound by the server. */
export async function runFinanceMutation<T>(
  mutation: (operationId: string, approval?: StrongAuthApproval) => Promise<T>,
  host: NativeBridgeHost | null = browserHost(),
  proofFactory: StrongAuthProofFactory = buildStrongAuthProof,
  operationIdFactory: OperationIdFactory = newFinanceOperationId,
): Promise<T> {
  const operationId = operationIdFactory();
  try {
    return await mutation(operationId, undefined);
  } catch (error) {
    const challenge = financeChallenge(error);
    if (!challenge) throw error;
    const proof = await proofFactory(
      'finance',
      challenge.resource_id,
      challenge.confirmation_method,
      true,
      host,
    );
    return mutation(operationId, {
      confirmationMethod: challenge.confirmation_method,
      proof,
    });
  }
}

export async function connectNativeCalendar(
  provider = 'apple_calendar',
  host: NativeBridgeHost | null = browserHost(),
): Promise<void> {
  const grant = await requestNativeCalendarGrant(host);
  const identity = await ensureNativeDeviceAttestation(host);
  if (grant.deviceId !== identity.deviceId) {
    throw new Error('A autorização veio de outra instalação do Jarvis.');
  }
  const proof = await buildStrongAuthProof(
    'device_grant',
    provider,
    '',
    false,
    host,
  );
  await registerDeviceGrant(provider, grant, proof);
}

export async function executeNativeCalendarProposal(
  proposal: JarvisActionProposal,
  confirmationMethod: ConfirmationMethod,
  host: NativeBridgeHost | null = browserHost(),
): Promise<{ proposal: JarvisActionProposal; replayed: boolean }> {
  const proof = await buildStrongAuthProof(
    'native_action',
    proposal.id,
    confirmationMethod,
    false,
    host,
  );
  const prepared = await prepareNativeAction(proposal.id, confirmationMethod, proof);
  if (prepared.proposal.status === 'confirmed') {
    if (proof.accountId) {
      try {
        await clearNativeCalendarReceiptCache(proof.accountId, proposal.id, host);
      } catch {
        // The server already resolved the action. Native receipts expire on
        // their own; cleanup failure must not make a confirmed action look failed.
      }
    }
    return { proposal: prepared.proposal, replayed: true };
  }
  if (!proof.accountId) {
    throw new Error('A identidade segura deste iPhone está incompleta.');
  }
  let identity: DeviceAttestationIdentity = {
    accountId: proof.accountId,
    deviceId: proof.deviceId,
    deviceLabel: '',
    keyId: proof.keyId,
  };
  let saved: NativeCalendarSave;
  try {
    saved = await requestNativeCalendarSave(
      prepared.proposal,
      prepared.claim_token,
      identity,
      host,
    );
  } catch (error) {
    if (!isInvalidAppAttestKey(error)) throw error;
    identity = await ensureNativeDeviceAttestation(host);
    saved = await requestNativeCalendarSave(
      prepared.proposal,
      prepared.claim_token,
      identity,
      host,
    );
  }
  const resolved = await resolveNativeAction(
    proposal.id,
    identity.deviceId,
    prepared.claim_token,
    { keyId: saved.keyId, assertion: saved.assertion },
    saved.event,
  );
  try {
    await clearNativeCalendarReceiptCache(identity.accountId, proposal.id, host);
  } catch {
    // The durable receipt has a short TTL. Keep the resolved server result even
    // if the local best-effort privacy cleanup cannot be acknowledged.
  }
  return resolved;
}

const FINANCE_RECORD_KINDS = new Set([
  'account',
  'bill',
  'budget',
  'expense',
  'goal',
  'income',
]);

export function proposalRequiresStrongAuth(
  proposal: JarvisActionProposal,
): boolean {
  const kind = proposal.arguments.kind;
  if (typeof kind !== 'string') return false;
  if (proposal.tool_name === 'life_record') return FINANCE_RECORD_KINDS.has(kind);
  return proposal.tool_name === 'life_complete' && kind === 'bill';
}
