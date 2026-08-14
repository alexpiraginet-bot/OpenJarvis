import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  completeCoachSession,
  fetchCoachOverview,
  fetchCoachSession,
  generateCoachPlan,
  saveCoachProfile,
  submitCoachCheckin,
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
  fetchMock.mockImplementation(async () =>
    new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe('adaptive coach API', () => {
  it('loads the overview and one detailed session', async () => {
    await fetchCoachOverview();
    await fetchCoachSession('session-1');

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/v1/life/coach',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer user-token' }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/v1/life/coach/sessions/session-1',
      expect.any(Object),
    );
  });

  it('saves a personal profile and generates a bounded plan', async () => {
    await saveCoachProfile({
      primary_sport: 'running',
      secondary_sports: ['strength'],
      primary_goal: '10k',
      target_distance_km: 10,
      target_date: '2026-12-06',
      level: 'intermediate',
      weekly_days: 4,
      available_weekdays: [1, 3, 5, 6],
      session_minutes: 50,
      current_weekly_km: 18,
      longest_recent_run_km: 8,
      equipment: ['halteres'],
      limitations: '',
    });
    await generateCoachPlan('2026-08-17', 8);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/v1/life/coach/profile',
      expect.objectContaining({ method: 'PUT' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/v1/life/coach/plans',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ start_on: '2026-08-17', weeks: 8 }),
      }),
    );
  });

  it('sends readiness and completion feedback to the prescribed session', async () => {
    await submitCoachCheckin('session-1', {
      sleep_quality: 7,
      soreness: 3,
      stress: 4,
      motivation: 8,
      pain: 1,
      notes: '',
    });
    await completeCoachSession('session-1', {
      completion_pct: 100,
      actual_duration_min: 46,
      rpe: 6,
      energy: 8,
      pain: 1,
      notes: 'Bem controlado',
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/v1/life/coach/sessions/session-1/check-in',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/v1/life/coach/sessions/session-1/complete',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
