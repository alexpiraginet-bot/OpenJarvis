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

const proof = {
  deviceId: 'ios-device-1234',
  challengeId: 'challenge-1234567890',
  challenge: 'challenge-value-1234567890',
  keyId: 'k'.repeat(43),
  assertion: 'a'.repeat(86),
};

const approval = {
  confirmationMethod: 'explicit' as const,
  proof,
};

describe('Finance strong authentication API', () => {
  it('preserves a structured 409 challenge instead of stringifying it', async () => {
    const detail = {
      message: 'Acoes financeiras exigem autenticacao forte.',
      purpose: 'finance',
      resource_id: 'finance:opaque-server-resource',
      confirmation_method: 'explicit',
    };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { createRecord, LifeApiError } = await import('./api');

    const error = await createRecord(
      'bills',
      { name: 'Luz' },
      undefined,
      'e37dc2c9-d3cd-4761-88f3-1062d774573f',
    ).catch((caught) => caught);

    expect(error).toBeInstanceOf(LifeApiError);
    expect(error.message).toBe(detail.message);
    expect(error.detail).toEqual(detail);
  });

  it('replays record writes with the exact App Attest approval fields', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ record: { id: 'bill-1' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { createRecord } = await import('./api');

    await createRecord(
      'bills',
      { name: 'Luz', amount_cents: 18000 },
      approval,
      'f9735ff3-31bb-475a-a3d8-4cce0e52b5f8',
    );

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/records/bills',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          fields: { name: 'Luz', amount_cents: 18000 },
          operation_id: 'f9735ff3-31bb-475a-a3d8-4cce0e52b5f8',
          confirmed: true,
          confirmation_method: 'explicit',
          strong_auth: {
            device_id: proof.deviceId,
            challenge_id: proof.challengeId,
            challenge: proof.challenge,
            key_id: proof.keyId,
            assertion: proof.assertion,
          },
        }),
      }),
    );
  });

  it('sends strong authentication on update, delete and pay-bill mutations', async () => {
    fetchMock.mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { deleteRecord, payBill, updateRecord } = await import('./api');

    const operationIds = [
      'bfc4d79a-17a9-4df5-8818-f6ce3e55c133',
      '286f64d6-05b5-4e7a-b74b-fbcc58cb3a0d',
      '57e865db-90b8-4fb2-bb39-d8ff5cf41857',
    ];
    await updateRecord(
      'goals',
      'goal-1',
      { name: 'Reserva' },
      approval,
      operationIds[0],
    );
    await deleteRecord('budgets', 'budget-1', approval, operationIds[1]);
    await payBill('bill-1', 'account-1', approval, operationIds[2]);

    for (const [index, call] of fetchMock.mock.calls.entries()) {
      const init = call[1] as RequestInit;
      const body = JSON.parse(String(init.body));
      expect(body).toMatchObject({
        operation_id: operationIds[index],
        confirmed: true,
        confirmation_method: 'explicit',
        strong_auth: {
          device_id: proof.deviceId,
          challenge_id: proof.challengeId,
          key_id: proof.keyId,
        },
      });
    }
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'DELETE' });
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toMatchObject({
      account_id: 'account-1',
    });
  });

  it('analyzes an attachment and lists only the authenticated tenant documents', async () => {
    fetchMock.mockImplementation(async () =>
      new Response(JSON.stringify({ documents: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { analyzeFinancialDocument, listFinancialDocuments } = await import('./api');

    await analyzeFinancialDocument({
      filename: 'extrato.csv',
      contentType: 'text/csv',
      dataBase64: 'RGF0YTtWYWxvcg==',
    });
    await listFinancialDocuments();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/v1/life/finance/documents/analyze',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          filename: 'extrato.csv',
          content_type: 'text/csv',
          data_base64: 'RGF0YTtWYWxvcg==',
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/v1/life/finance/documents',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer life-token' }),
      }),
    );
  });
});
