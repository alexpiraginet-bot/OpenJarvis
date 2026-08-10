/** Rotina — hábitos do dia e sequências. */

import { Check, Plus } from 'lucide-react';
import { useState } from 'react';
import { checkHabit, createRecord, fetchRoutineSummary, uncheckHabit } from '../api';
import type { Habit } from '../types';
import {
  Button,
  Empty,
  Field,
  ListGroup,
  ProgressBar,
  Section,
  Sheet,
  Spinner,
  useLoader,
} from '../ui';

export function HabitsToday({ onChanged }: { onChanged: () => void }) {
  const routine = useLoader(fetchRoutineSummary);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function toggle(habit: Habit) {
    setBusy(habit.id);
    setError('');
    try {
      if (habit.done_today) await uncheckHabit(habit.id);
      else await checkHabit(habit.id);
      routine.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao marcar');
    } finally {
      setBusy('');
    }
  }

  if (routine.loading) return <Spinner />;
  if (routine.error) return <div className="oj-error">{routine.error}</div>;

  const habits = routine.data?.habits ?? [];
  const completed = routine.data?.completed ?? 0;
  const total = routine.data?.total ?? 0;

  return (
    <>
      {error && <div className="oj-error">{error}</div>}

      <div className="oj-card">
        <div className="oj-card-label">Progresso de hoje</div>
        <div className="oj-stat">
          {completed}/{total}
        </div>
        <ProgressBar pct={total ? (completed / total) * 100 : 0} tone="var(--oj-ok)" />
      </div>

      <Section title="Hábitos">
        {habits.length === 0 ? (
          <Empty>Nenhum hábito ainda. Crie o primeiro na aba ao lado.</Empty>
        ) : (
          <ListGroup>
            {habits.map((habit) => (
              <div key={habit.id} className="oj-row">
                <button
                  type="button"
                  className="oj-check"
                  data-done={habit.done_today}
                  disabled={busy === habit.id}
                  onClick={() => toggle(habit)}
                  aria-label={
                    habit.done_today
                      ? `Desmarcar ${habit.name}`
                      : `Marcar ${habit.name} como feito`
                  }
                >
                  {habit.done_today && <Check size={16} color="#fff" />}
                </button>
                <div className="oj-row-body">
                  <div className="oj-row-title">{habit.name}</div>
                  <div className="oj-row-sub">{habit.cadence}</div>
                </div>
                {habit.streak > 0 && (
                  <span className="oj-streak">{habit.streak}d</span>
                )}
              </div>
            ))}
          </ListGroup>
        )}
      </Section>
    </>
  );
}

export function ManageHabits({ onChanged }: { onChanged: () => void }) {
  const routine = useLoader(fetchRoutineSummary);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [cadence, setCadence] = useState('daily');
  const [error, setError] = useState('');

  async function handleAdd() {
    if (!name.trim()) {
      setError('Dê um nome ao hábito.');
      return;
    }
    setError('');
    try {
      await createRecord<Habit>('habits', { name: name.trim(), cadence });
      setAdding(false);
      setName('');
      routine.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (routine.loading) return <Spinner />;
  const habits = routine.data?.habits ?? [];
  const best = [...habits].sort((a, b) => b.streak - a.streak)[0];

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Novo hábito
      </Button>

      {best && best.streak > 0 && (
        <div className="oj-card">
          <div className="oj-card-label">Maior sequência</div>
          <div className="oj-stat--sm">
            {best.name} · {best.streak} dias
          </div>
        </div>
      )}

      <Section title="Todos os hábitos">
        {habits.length === 0 ? (
          <Empty>Nada cadastrado.</Empty>
        ) : (
          <ListGroup>
            {habits.map((habit) => (
              <div key={habit.id} className="oj-row">
                <div className="oj-row-body">
                  <div className="oj-row-title">{habit.name}</div>
                  <div className="oj-row-sub">
                    {habit.cadence} · sequência de {habit.streak} dia(s)
                  </div>
                </div>
              </div>
            ))}
          </ListGroup>
        )}
      </Section>

      {adding && (
        <Sheet title="Novo hábito" onClose={() => setAdding(false)}>
          <Field
            label="Nome"
            value={name}
            onChange={setName}
            placeholder="Ler 20 minutos"
          />
          <Field
            label="Frequência"
            value={cadence}
            onChange={setCadence}
            options={[
              { value: 'daily', label: 'Diário' },
              { value: 'weekly', label: 'Semanal' },
            ]}
          />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}
