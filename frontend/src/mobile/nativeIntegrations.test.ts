import { describe, expect, it, vi } from 'vitest';
import { LifeApiError, type AppAttestProof, type JarvisActionProposal } from './api';
import {
  clearNativeCalendarReceiptCache,
  proposalRequiresStrongAuth,
  requestNativeAppAttestation,
  requestNativeAssertion,
  requestNativeCalendarContext,
  requestNativeCalendarGrant,
  requestNativeCalendarSave,
  requestNativeDeviceIdentity,
  requestNativeHealthBatch,
  requestNativeHealthGrant,
  runFinanceMutation,
  type NativeBridgeHost,
} from './nativeIntegrations';

class FakeNativeHost implements NativeBridgeHost {
  private listeners = new Set<EventListener>();
  readonly postMessage = vi.fn();
  readonly webkit = {
    messageHandlers: {
      jarvisIntegrations: { postMessage: this.postMessage },
    },
  };

  addEventListener(_type: string, listener: EventListener): void {
    this.listeners.add(listener);
  }

  removeEventListener(_type: string, listener: EventListener): void {
    this.listeners.delete(listener);
  }

  emit(detail: Record<string, unknown>): void {
    const event = Object.assign(new Event('jarvis-native-integration'), { detail });
    for (const listener of this.listeners) listener(event);
  }

  get listenerCount(): number {
    return this.listeners.size;
  }
}

const identity = {
  accountId: 'a'.repeat(32),
  deviceId: 'ios-device-1234',
  deviceLabel: 'iPhone',
  keyId: 'k'.repeat(43),
};

describe('native iOS integration bridge', () => {
  it('fails honestly outside the native iOS shell', async () => {
    await expect(
      requestNativeCalendarGrant(null, 'request-calendar-1'),
    ).rejects.toThrow('app iOS');
  });

  it('requests an account-scoped App Attest identity', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeDeviceIdentity(
      'a'.repeat(32),
      host,
      'request-identity-1',
    );
    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'identity',
      accountId: 'a'.repeat(32),
      requestId: 'request-identity-1',
    });
    host.emit({
      type: 'deviceIdentity',
      status: 'ready',
      requestId: 'request-identity-1',
      deviceId: identity.deviceId,
      deviceLabel: identity.deviceLabel,
      keyId: identity.keyId,
    });
    await expect(pending).resolves.toEqual(identity);
  });

  it('returns at most ten bounded calendar events using backend field aliases', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeCalendarContext(host, 'request-calendar-read-1');

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'readCalendarEvents',
      requestId: 'request-calendar-read-1',
    });
    host.emit({
      type: 'calendarEvents',
      status: 'ready',
      requestId: 'request-calendar-read-1',
      events: Array.from({ length: 12 }, (_, index) => ({
        id: `event-${index}`,
        title: `Compromisso ${index}`,
        start_at: `2026-08-${String(index + 14).padStart(2, '0')}T15:00:00-03:00`,
        end_at: `2026-08-${String(index + 14).padStart(2, '0')}T16:00:00-03:00`,
        is_all_day: false,
        location: '',
        calendar_title: 'Pessoal',
      })),
    });

    const context = await pending;
    expect(context.calendar_events).toHaveLength(10);
    expect(context.calendar_events[0]).toEqual({
      id: 'event-0',
      title: 'Compromisso 0',
      start_at: '2026-08-14T15:00:00-03:00',
      end_at: '2026-08-14T16:00:00-03:00',
      is_all_day: false,
      location: '',
      calendar_title: 'Pessoal',
    });
  });

  it('requests account-bound receipt cleanup after resolution or disconnect', async () => {
    const host = new FakeNativeHost();
    const pending = clearNativeCalendarReceiptCache(
      identity.accountId,
      'proposal-calendar-1234',
      host,
      'request-calendar-clear-1',
    );

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'clearCalendarReceipts',
      accountId: identity.accountId,
      proposalId: 'proposal-calendar-1234',
      requestId: 'request-calendar-clear-1',
    });
    host.emit({
      type: 'calendarReceiptsCleared',
      status: 'ready',
      requestId: 'request-calendar-clear-1',
    });

    await expect(pending).resolves.toBeUndefined();
  });

  it('correlates the native response and returns only a real calendar grant', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeCalendarGrant(host, 'request-calendar-2');

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'requestPermission',
      provider: 'apple_calendar',
      requestId: 'request-calendar-2',
    });
    host.emit({
      type: 'deviceGrant',
      status: 'granted',
      provider: 'apple_calendar',
      requestId: 'another-request',
      grantedScopes: ['events.read'],
      ...identity,
    });
    expect(host.listenerCount).toBe(1);

    host.emit({
      type: 'deviceGrant',
      status: 'granted',
      provider: 'apple_calendar',
      requestId: 'request-calendar-2',
      grantedScopes: ['events.read', 'events.write'],
      ...identity,
    });

    await expect(pending).resolves.toEqual({
      grantedScopes: ['events.read', 'events.write'],
      deviceId: identity.deviceId,
      deviceLabel: identity.deviceLabel,
    });
    expect(host.listenerCount).toBe(0);
  });

  it('requests HealthKit permission and accepts only the complete native grant', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeHealthGrant(host, 'request-health-grant-1');

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'requestPermission',
      provider: 'apple_health',
      requestId: 'request-health-grant-1',
    });
    host.emit({
      type: 'deviceGrant',
      status: 'granted',
      provider: 'apple_health',
      requestId: 'request-health-grant-1',
      grantedScopes: [
        'steps.read',
        'sleep.read',
        'heart_rate.read',
        'resting_heart_rate.read',
        'active_energy.read',
        'workouts.read',
      ],
      ...identity,
    });

    await expect(pending).resolves.toEqual({
      grantedScopes: [
        'steps.read',
        'sleep.read',
        'heart_rate.read',
        'resting_heart_rate.read',
        'active_energy.read',
        'workouts.read',
      ],
      deviceId: identity.deviceId,
      deviceLabel: identity.deviceLabel,
    });
  });

  it('returns the opaque native HealthKit batch bound to its digest', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeHealthBatch(host, 'request-health-read-1');

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'readHealthData',
      requestId: 'request-health-read-1',
    });
    host.emit({
      type: 'healthData',
      status: 'ready',
      requestId: 'request-health-read-1',
      deviceId: identity.deviceId,
      payload: 'eyJzYW1wbGVzIjpbXX0',
      resourceId: `health:${'a'.repeat(64)}`,
      sampleCount: 0,
    });

    await expect(pending).resolves.toEqual({
      deviceId: identity.deviceId,
      payload: 'eyJzYW1wbGVzIjpbXX0',
      resourceId: `health:${'a'.repeat(64)}`,
      sampleCount: 0,
    });
  });

  it('signs attestation and assertion bytes without accepting another key', async () => {
    const host = new FakeNativeHost();
    const attestation = requestNativeAppAttestation(
      identity,
      'c'.repeat(43),
      host,
      'request-attest-1',
    );
    host.emit({
      type: 'appAttestation',
      status: 'ready',
      requestId: 'request-attest-1',
      keyId: identity.keyId,
      attestationObject: 'a'.repeat(128),
    });
    await expect(attestation).resolves.toEqual({
      keyId: identity.keyId,
      attestationObject: 'a'.repeat(128),
    });

    const assertion = requestNativeAssertion(
      identity,
      'd'.repeat(64),
      true,
      host,
      'request-assert-1',
    );
    expect(host.postMessage).toHaveBeenLastCalledWith({
      action: 'assert',
      accountId: identity.accountId,
      keyId: identity.keyId,
      clientData: 'd'.repeat(64),
      requireBiometric: true,
      requestId: 'request-assert-1',
    });
    host.emit({
      type: 'appAssertion',
      status: 'ready',
      requestId: 'request-assert-1',
      keyId: 'x'.repeat(43),
      assertion: 'z'.repeat(86),
    });
    await expect(assertion).rejects.toThrow('resposta inválida');
  });

  it('accepts one explicitly rotated key returned by native attestation', async () => {
    const host = new FakeNativeHost();
    const rotatedKey = 'r'.repeat(43);
    const pending = requestNativeAppAttestation(
      identity,
      'c'.repeat(43),
      host,
      'request-attest-rotation',
    );

    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'attest',
      accountId: identity.accountId,
      keyId: identity.keyId,
      challenge: 'c'.repeat(43),
      requestId: 'request-attest-rotation',
    });
    host.emit({
      type: 'appAttestation',
      status: 'ready',
      requestId: 'request-attest-rotation',
      keyId: rotatedKey,
      rotated: true,
      attestationObject: 'a'.repeat(128),
    });

    await expect(pending).resolves.toEqual({
      keyId: rotatedKey,
      attestationObject: 'a'.repeat(128),
    });
  });

  it('rejects a rotated attestation response without a replacement key', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeAppAttestation(
      identity,
      'c'.repeat(43),
      host,
      'request-attest-missing-key',
    );

    host.emit({
      type: 'appAttestation',
      status: 'ready',
      requestId: 'request-attest-missing-key',
      rotated: true,
      attestationObject: 'a'.repeat(128),
    });

    await expect(pending).rejects.toThrow('resposta inválida');
  });

  it('returns the saved EventKit receipt and its claim-bound HMAC', async () => {
    const host = new FakeNativeHost();
    const proposal = {
      id: 'proposal-calendar-1234',
      tool_name: 'calendar_create',
      arguments: {
        title: 'Marketing',
        start_at: '2026-08-14T15:00:00-03:00',
        end_at: '2026-08-14T16:00:00-03:00',
      },
    } as unknown as JarvisActionProposal;
    const pending = requestNativeCalendarSave(
      proposal,
      'claim-token-abcdefghijklmnopqrstuvwxyz',
      identity,
      host,
      'request-save-1',
    );
    expect(host.postMessage).toHaveBeenCalledWith({
      action: 'createCalendarEvent',
      requestId: 'request-save-1',
      proposalId: proposal.id,
      claimToken: 'claim-token-abcdefghijklmnopqrstuvwxyz',
      accountId: identity.accountId,
      keyId: identity.keyId,
      event: proposal.arguments,
    });
    const event = {
      id: 'event-1',
      title: 'Marketing',
      startAt: '2026-08-14T15:00:00-03:00',
      endAt: '2026-08-14T16:00:00-03:00',
      isAllDay: false,
      location: '',
      calendarTitle: 'Pessoal',
    };
    host.emit({
      type: 'calendarEvent',
      status: 'saved',
      requestId: 'request-save-1',
      event,
      keyId: identity.keyId,
      assertion: 'a'.repeat(86),
    });
    await expect(pending).resolves.toEqual({
      event,
      keyId: identity.keyId,
      assertion: 'a'.repeat(86),
    });
  });

  it('surfaces a denied system permission and removes its listener', async () => {
    const host = new FakeNativeHost();
    const pending = requestNativeCalendarGrant(host, 'request-calendar-3');
    host.emit({
      type: 'deviceGrant',
      status: 'denied',
      provider: 'apple_calendar',
      requestId: 'request-calendar-3',
      message: 'Acesso ao Calendario negado no iPhone.',
    });

    await expect(pending).rejects.toThrow('negado no iPhone');
    expect(host.listenerCount).toBe(0);
  });
});

describe('proposalRequiresStrongAuth', () => {
  it('requires the native app for every Finance-owned proposal', () => {
    const proposal = (tool: string, kind: string) =>
      ({ tool_name: tool, arguments: { kind } }) as unknown as JarvisActionProposal;

    expect(proposalRequiresStrongAuth(proposal('life_record', 'expense'))).toBe(true);
    expect(proposalRequiresStrongAuth(proposal('life_record', 'account'))).toBe(true);
    expect(proposalRequiresStrongAuth(proposal('life_complete', 'bill'))).toBe(true);
    expect(proposalRequiresStrongAuth(proposal('life_record', 'habit'))).toBe(false);
  });

  it('uses the exact opaque resource returned by the backend for one retry', async () => {
    const mutation = vi
      .fn()
      .mockRejectedValueOnce(
        new LifeApiError('Confirme no iPhone.', 409, {
          message: 'Confirme no iPhone.',
          purpose: 'finance',
          resource_id: 'finance:opaque-server-resource',
          confirmation_method: 'voice_explicit',
        }),
      )
      .mockResolvedValueOnce({ id: 'transaction-1' });
    const builtProof = { ...identity, challengeId: 'challenge-1', challenge: 'c', assertion: 'a' } as AppAttestProof;
    const proofFactory = vi.fn().mockResolvedValue(builtProof);
    const operationIdFactory = vi
      .fn()
      .mockReturnValue('f9735ff3-31bb-475a-a3d8-4cce0e52b5f8');

    await expect(
      runFinanceMutation(mutation, null, proofFactory, operationIdFactory),
    ).resolves.toEqual({ id: 'transaction-1' });

    expect(proofFactory).toHaveBeenCalledWith(
      'finance',
      'finance:opaque-server-resource',
      'voice_explicit',
      true,
      null,
    );
    expect(operationIdFactory).toHaveBeenCalledOnce();
    expect(mutation).toHaveBeenNthCalledWith(
      1,
      'f9735ff3-31bb-475a-a3d8-4cce0e52b5f8',
      undefined,
    );
    expect(mutation).toHaveBeenNthCalledWith(
      2,
      'f9735ff3-31bb-475a-a3d8-4cce0e52b5f8',
      {
        confirmationMethod: 'voice_explicit',
        proof: builtProof,
      },
    );
  });

  it('creates a new root operation id for each separate user intention', async () => {
    const mutation = vi.fn().mockResolvedValue({ id: 'ok' });
    const operationIdFactory = vi
      .fn()
      .mockReturnValueOnce('1c6af949-57b2-4016-a2ea-02b2d79e0a1a')
      .mockReturnValueOnce('27813098-0281-4c28-a219-0d300dedfccc');

    await runFinanceMutation(mutation, null, vi.fn(), operationIdFactory);
    await runFinanceMutation(mutation, null, vi.fn(), operationIdFactory);

    expect(mutation).toHaveBeenNthCalledWith(
      1,
      '1c6af949-57b2-4016-a2ea-02b2d79e0a1a',
      undefined,
    );
    expect(mutation).toHaveBeenNthCalledWith(
      2,
      '27813098-0281-4c28-a219-0d300dedfccc',
      undefined,
    );
  });

  it('does not reinterpret unrelated conflicts as a Face ID challenge', async () => {
    const error = new LifeApiError('Conflict', 409, { message: 'Conflict' });
    const mutation = vi.fn().mockRejectedValue(error);
    const proofFactory = vi.fn();

    await expect(
      runFinanceMutation(
        mutation,
        null,
        proofFactory,
        () => 'a6d7632d-d556-46ad-9f6f-acf8ea60de8c',
      ),
    ).rejects.toBe(error);
    expect(proofFactory).not.toHaveBeenCalled();
    expect(mutation).toHaveBeenCalledTimes(1);
  });
});
