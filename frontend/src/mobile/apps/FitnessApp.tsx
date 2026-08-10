/** Treino — carga da semana, sessões e medidas. */

import { Check, Plus } from 'lucide-react';
import { useState } from 'react';
import {
  completeWorkout,
  createRecord,
  fetchFitnessSummary,
  listRecords,
} from '../api';
import type { Measurement, Workout } from '../types';
import {
  Button,
  Empty,
  Field,
  formatShortDate,
  ListGroup,
  Row,
  Section,
  Sheet,
  Spinner,
  Stat,
  todayIso,
  useLoader,
} from '../ui';

export function FitnessOverview() {
  const { data, error, loading } = useLoader(fetchFitnessSummary);

  if (loading) return <Spinner />;
  if (error) return <div className="oj-error">{error}</div>;
  if (!data) return null;

  return (
    <>
      <div className="oj-stat-row">
        <Stat
          label="Treinos na semana"
          value={`${data.week_completed}/${data.week_planned}`}
          small
        />
        <Stat label="Minutos" value={String(data.week_minutes)} small />
      </div>
      <div className="oj-stat-row">
        <Stat
          label="Volume (kg)"
          value={data.week_volume_kg.toLocaleString('pt-BR')}
          small
        />
        <Stat
          label="Sem treinar"
          value={
            data.days_since_last === null ? '—' : `${data.days_since_last}d`
          }
          tone={
            data.days_since_last !== null && data.days_since_last >= 4
              ? 'warn'
              : undefined
          }
          small
        />
      </div>

      <Section title="Recordes">
        {data.personal_records.length === 0 ? (
          <Empty>Registre séries para ver seus recordes.</Empty>
        ) : (
          <ListGroup>
            {data.personal_records.map((record) => (
              <Row
                key={record.exercise}
                title={record.exercise}
                sub={`${record.reps} repetições`}
                value={`${record.weight_kg} kg`}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {data.latest_measurement && (
        <Section title="Última medida">
          <ListGroup>
            <Row
              title="Peso"
              value={`${data.latest_measurement.weight_kg} kg`}
              sub={formatShortDate(data.latest_measurement.taken_on)}
            />
            {data.latest_measurement.body_fat_pct > 0 && (
              <Row
                title="Gordura corporal"
                value={`${data.latest_measurement.body_fat_pct}%`}
              />
            )}
            {data.latest_measurement.sleep_hours > 0 && (
              <Row
                title="Sono"
                value={`${data.latest_measurement.sleep_hours} h`}
              />
            )}
          </ListGroup>
        </Section>
      )}
    </>
  );
}

export function WorkoutsTab({ onChanged }: { onChanged: () => void }) {
  const workouts = useLoader(() =>
    listRecords<Workout>('workouts', { order_by: 'scheduled_on', limit: 60 }),
  );
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState('');
  const [name, setName] = useState('');
  const [focus, setFocus] = useState('');
  const [scheduledOn, setScheduledOn] = useState(todayIso());
  const [error, setError] = useState('');

  async function handleComplete(workout: Workout) {
    setBusy(workout.id);
    try {
      await completeWorkout(workout.id);
      workouts.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao concluir');
    } finally {
      setBusy('');
    }
  }

  async function handleAdd() {
    if (!name.trim()) {
      setError('Dê um nome ao treino.');
      return;
    }
    setError('');
    try {
      await createRecord<Workout>('workouts', {
        name: name.trim(),
        focus: focus.trim(),
        scheduled_on: scheduledOn,
      });
      setAdding(false);
      setName('');
      setFocus('');
      workouts.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (workouts.loading) return <Spinner />;
  const records = workouts.data?.records ?? [];
  const upcoming = records.filter((w) => !w.completed_at);
  const done = records.filter((w) => w.completed_at);

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Novo treino
      </Button>

      <Section title="Programados">
        {upcoming.length === 0 ? (
          <Empty>Nenhum treino programado.</Empty>
        ) : (
          <ListGroup>
            {upcoming.map((workout) => (
              <Row
                key={workout.id}
                title={workout.name}
                sub={`${formatShortDate(workout.scheduled_on)}${
                  workout.focus ? ` · ${workout.focus}` : ''
                }`}
                trailing={
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy === workout.id}
                    onClick={() => handleComplete(workout)}
                  >
                    {busy === workout.id ? '…' : 'Concluir'}
                  </Button>
                }
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {done.length > 0 && (
        <Section title="Concluídos">
          <ListGroup>
            {done.slice(0, 15).map((workout) => (
              <Row
                key={workout.id}
                title={workout.name}
                sub={formatShortDate(workout.scheduled_on)}
                leading={<Check size={18} color="var(--oj-ok)" />}
                value={workout.duration_min ? `${workout.duration_min} min` : ''}
              />
            ))}
          </ListGroup>
        </Section>
      )}

      {adding && (
        <Sheet title="Novo treino" onClose={() => setAdding(false)}>
          <Field
            label="Nome"
            value={name}
            onChange={setName}
            placeholder="Peito e tríceps"
          />
          <Field label="Foco" value={focus} onChange={setFocus} placeholder="força" />
          <Field
            label="Data"
            value={scheduledOn}
            onChange={setScheduledOn}
            type="date"
          />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}

export function MeasurementsTab() {
  const measurements = useLoader(() =>
    listRecords<Measurement>('measurements', { order_by: 'taken_on', limit: 60 }),
  );
  const [adding, setAdding] = useState(false);
  const [weight, setWeight] = useState('');
  const [bodyFat, setBodyFat] = useState('');
  const [sleep, setSleep] = useState('');
  const [takenOn, setTakenOn] = useState(todayIso());
  const [error, setError] = useState('');

  async function handleAdd() {
    const weightValue = Number.parseFloat(weight.replace(',', '.'));
    if (!Number.isFinite(weightValue) || weightValue <= 0) {
      setError('Informe o peso.');
      return;
    }
    setError('');
    try {
      await createRecord<Measurement>('measurements', {
        taken_on: takenOn,
        weight_kg: weightValue,
        body_fat_pct: Number.parseFloat(bodyFat.replace(',', '.')) || 0,
        sleep_hours: Number.parseFloat(sleep.replace(',', '.')) || 0,
      });
      setAdding(false);
      setWeight('');
      setBodyFat('');
      setSleep('');
      measurements.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (measurements.loading) return <Spinner />;
  const records = measurements.data?.records ?? [];

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Nova medida
      </Button>

      <Section title="Histórico">
        {records.length === 0 ? (
          <Empty>Nenhuma medida registrada.</Empty>
        ) : (
          <ListGroup>
            {records.map((measurement) => (
              <Row
                key={measurement.id}
                title={`${measurement.weight_kg} kg`}
                sub={[
                  formatShortDate(measurement.taken_on),
                  measurement.body_fat_pct
                    ? `${measurement.body_fat_pct}% gordura`
                    : '',
                  measurement.sleep_hours ? `${measurement.sleep_hours}h sono` : '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {adding && (
        <Sheet title="Nova medida" onClose={() => setAdding(false)}>
          <Field label="Peso (kg)" value={weight} onChange={setWeight} placeholder="78,5" />
          <Field
            label="Gordura corporal (%)"
            value={bodyFat}
            onChange={setBodyFat}
            placeholder="18"
          />
          <Field label="Sono (h)" value={sleep} onChange={setSleep} placeholder="7,5" />
          <Field label="Data" value={takenOn} onChange={setTakenOn} type="date" />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}
