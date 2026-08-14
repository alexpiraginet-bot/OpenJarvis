import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  HealthSafetyNotice,
  buildHealthProfileFields,
  formatHealthObservation,
  parsePositiveHealthNumber,
} from './HealthApp';

describe('health manual correction helpers', () => {
  it('accepts pt-BR decimal input but rejects empty, zero and invalid metrics', () => {
    expect(parsePositiveHealthNumber('78,5')).toBe(78.5);
    expect(parsePositiveHealthNumber('0')).toBeNull();
    expect(parsePositiveHealthNumber('')).toBeNull();
    expect(parsePositiveHealthNumber('peso')).toBeNull();
  });

  it('normalizes the user-confirmed health profile without inventing facts', () => {
    expect(
      buildHealthProfileFields({
        birthDate: '',
        sexAtBirth: '  feminino ',
        heightCm: ' 168,5 ',
        bloodType: ' O+ ',
        goals: '  dormir melhor  ',
        emergencyContact: '  Bruna · 27999990000 ',
        consentHealthMemory: true,
      }),
    ).toEqual({
      birth_date: null,
      sex_at_birth: 'feminino',
      height_cm: 168.5,
      blood_type: 'O+',
      goals: 'dormir melhor',
      emergency_contact: 'Bruna · 27999990000',
      consent_health_memory: 1,
    });
  });

  it('formats a confirmed observation with pt-BR value, unit and date', () => {
    expect(
      formatHealthObservation({
        kind: 'peso',
        value: 78.5,
        unit: 'kg',
        observed_at: '2026-08-13T08:30:00-03:00',
      }),
    ).toEqual({ title: 'Peso', detail: '78,5 kg · 13/08' });
  });
});

describe('HealthSafetyNotice', () => {
  it('sets a safe boundary and gives an emergency action', () => {
    const markup = renderToStaticMarkup(<HealthSafetyNotice />);

    expect(markup).toContain('organiza dados confirmados');
    expect(markup).toContain('não diagnostica nem prescreve');
    expect(markup).toContain('SAMU 192');
  });
});
