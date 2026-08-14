import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchHealthSummary,
  recordHydration,
  saveHealthProfile,
} from './api';

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('localStorage', {
    getItem: () => 'user-token',
    setItem: () => undefined,
    removeItem: () => undefined,
  });
});

afterEach(() => vi.unstubAllGlobals());

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('health API', () => {
  it('loads the tenant-scoped health summary', async () => {
    fetchMock.mockResolvedValue(response({ hydration_today_ml: 500 }));

    await fetchHealthSummary();

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/life/summary/health',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer user-token' }),
      }),
    );
  });

  it('creates a profile only when none exists and patches the existing profile', async () => {
    fetchMock.mockResolvedValue(response({ record: { id: 'profile-1' } }, 201));
    await saveHealthProfile(null, { goals: 'Dormir melhor' });

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/v1/life/records/health_profiles',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ fields: { goals: 'Dormir melhor' } }),
      }),
    );

    fetchMock.mockResolvedValue(response({ record: { id: 'profile-1' } }));
    await saveHealthProfile('profile-1', { goals: 'Correr 5 km' });

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/v1/life/records/health_profiles/profile-1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ fields: { goals: 'Correr 5 km' } }),
      }),
    );
  });

  it('records hydration with an explicit timestamp and manual source', async () => {
    fetchMock.mockResolvedValue(response({ record: { id: 'water-1' } }, 201));

    await recordHydration(350, '2026-08-13T12:30:00.000Z');

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/v1/life/records/hydration_logs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          fields: {
            amount_ml: 350,
            occurred_at: '2026-08-13T12:30:00.000Z',
            source: 'manual',
          },
        }),
      }),
    );
  });
});
