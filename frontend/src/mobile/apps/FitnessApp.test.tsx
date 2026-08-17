import { Children, isValidElement } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { TrainingStep } from '../types';
import {
  FitnessNumericField,
  TrainingSteps,
  formatTrainingTarget,
  recommendationCopy,
  sportLabel,
  trainingCheckinInputFromDraft,
  trainingCompletionInputFromDraft,
  trainingProfileInputFromDraft,
  trainingProfileScheduleError,
} from './FitnessApp';

type NumericInputElement = ReactElement<{
  onBlur: () => void;
  onChange: (event: { target: { value: string } }) => void;
  value: string;
}>;

function findNumericInput(node: ReactNode): NumericInputElement {
  if (isValidElement(node)) {
    if (node.type === 'input') return node as NumericInputElement;
    const props = node.props as { children?: ReactNode };
    for (const child of Children.toArray(props.children)) {
      try {
        return findNumericInput(child);
      } catch {
        // Continue until the actual input is found.
      }
    }
  }
  throw new Error('numeric input not found');
}

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
  it('keeps an empty numeric draft until the user finishes editing', () => {
    let draft = '7';
    const renderField = () =>
      FitnessNumericField({
        fallback: 0,
        integer: true,
        label: 'Energia',
        max: 10,
        min: 0,
        onChange: (value) => {
          draft = value;
        },
        value: draft,
      });

    findNumericInput(renderField()).props.onChange({ target: { value: '' } });
    expect(draft).toBe('');

    findNumericInput(renderField()).props.onBlur();
    expect(draft).toBe('0');
  });

  it('accepts a pt-BR decimal comma and normalizes it only on blur', () => {
    let draft = '';
    const renderField = () =>
      FitnessNumericField({
        fallback: 0,
        label: 'Volume atual',
        max: 250,
        min: 0,
        onChange: (value) => {
          draft = value;
        },
        value: draft,
      });

    findNumericInput(renderField()).props.onChange({ target: { value: '12,5' } });
    expect(draft).toBe('12,5');

    findNumericInput(renderField()).props.onBlur();
    expect(draft).toBe('12,5');
  });

  it('builds bounded integer coach payloads when the forms are submitted', () => {
    expect(
      trainingCheckinInputFromDraft({
        motivation: '-2',
        notes: 'recuperação',
        pain: '9',
        sleep_quality: '12',
        soreness: '',
        stress: '4,6',
      }),
    ).toEqual({
      motivation: 0,
      notes: 'recuperação',
      pain: 9,
      sleep_quality: 10,
      soreness: 0,
      stress: 5,
    });
    expect(
      trainingCompletionInputFromDraft({
        actual_duration_min: '301',
        completion_pct: '101',
        energy: '8',
        notes: 'feito',
        pain: '-1',
        rpe: '7,5',
      }),
    ).toEqual({
      actual_duration_min: 300,
      completion_pct: 100,
      energy: 8,
      notes: 'feito',
      pain: 0,
      rpe: 8,
    });
  });

  it('builds a valid profile payload from integer and pt-BR decimal drafts', () => {
    expect(
      trainingProfileInputFromDraft({
        available_weekdays: [1, 3],
        current_weekly_km: '12,5',
        equipment: [],
        level: 'beginner',
        limitations: '',
        longest_recent_run_km: '101',
        primary_goal: 'general_fitness',
        primary_sport: 'running',
        secondary_sports: ['strength'],
        session_minutes: '5',
        target_date: null,
        target_distance_km: 0,
        weekly_days: '',
      }),
    ).toEqual({
      available_weekdays: [1, 3],
      current_weekly_km: 12.5,
      equipment: [],
      level: 'beginner',
      limitations: '',
      longest_recent_run_km: 100,
      primary_goal: 'general_fitness',
      primary_sport: 'running',
      secondary_sports: ['strength'],
      session_minutes: 20,
      target_date: null,
      target_distance_km: 0,
      weekly_days: 2,
    });
  });

  it('blocks a plan when the weekly frequency has too few selected days', () => {
    expect(
      trainingProfileScheduleError({
        weekly_days: 4,
        available_weekdays: [1, 3, 5],
      }),
    ).toBe('Selecione dias suficientes para a frequência semanal.');
    expect(
      trainingProfileScheduleError({
        weekly_days: 4,
        available_weekdays: [1, 2, 3, 5],
      }),
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
