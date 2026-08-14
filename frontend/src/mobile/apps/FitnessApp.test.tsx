import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { TrainingProfileInput, TrainingStep } from '../types';
import * as FitnessAppModule from './FitnessApp';
import {
  TrainingSteps,
  formatTrainingTarget,
  recommendationCopy,
  sportLabel,
} from './FitnessApp';

function step(fields: Partial<TrainingStep>): TrainingStep {
  return {
    id: 'step-1',
    user_id: 'user-1',
    created_at: '2026-08-14T10:00:00Z',
    session_id: 'session-1',
    step_index: 1,
    kind: 'strength',
    title: 'Agachamento',
    instructions: 'Preserve a técnica.',
    duration_sec: 0,
    distance_m: 0,
    target_pace_min_km: 0,
    target_rpe: 6,
    sets: 3,
    reps: 8,
    rest_sec: 75,
    alternative: 'Sente e levante de um banco.',
    ...fields,
  };
}

describe('adaptive coach presentation', () => {
  it('blocks a plan when the weekly frequency has too few selected days', () => {
    const validate = (
      FitnessAppModule as unknown as {
        trainingProfileScheduleError?: (
          profile: Pick<
            TrainingProfileInput,
            'weekly_days' | 'available_weekdays'
          >,
        ) => string;
      }
    ).trainingProfileScheduleError;

    expect(validate).toBeTypeOf('function');
    if (!validate) return;
    expect(
      validate({ weekly_days: 4, available_weekdays: [1, 3, 5] }),
    ).toBe('Selecione dias suficientes para a frequência semanal.');
    expect(
      validate({ weekly_days: 4, available_weekdays: [1, 2, 3, 5] }),
    ).toBe('');
  });

  it('formats strength and timed prescriptions without hiding the target', () => {
    expect(formatTrainingTarget(step({}))).toBe('3 × 8 · pausa 75s · RPE 6');
    expect(
      formatTrainingTarget(
        step({ sets: 0, reps: 0, duration_sec: 1200, rest_sec: 0, target_rpe: 4 }),
      ),
    ).toBe('20 min · RPE 4');
  });

  it('renders executable instructions and a safer alternative', () => {
    const markup = renderToStaticMarkup(<TrainingSteps steps={[step({})]} />);

    expect(markup).toContain('Agachamento');
    expect(markup).toContain('3 × 8');
    expect(markup).toContain('Preserve a técnica');
    expect(markup).toContain('Alternativa');
  });

  it('translates sports and readiness into direct pt-BR actions', () => {
    expect(sportLabel('canoeing')).toBe('Canoa');
    expect(sportLabel('strength')).toBe('Força');
    expect(recommendationCopy('ready').title).toBe('Pronto para treinar');
    expect(recommendationCopy('stop_and_seek_care').tone).toBe('danger');
  });
});
