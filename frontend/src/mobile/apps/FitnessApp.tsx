/** Treino — carga da semana, sessões e medidas. */

import {
  Activity,
  CalendarDays,
  Check,
  ChevronRight,
  Gauge,
  Plus,
  ShieldAlert,
  Sparkles,
  Timer,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import {
  completeCoachSession,
  completeWorkout,
  createRecord,
  fetchCoachOverview,
  fetchCoachSession,
  fetchFitnessSummary,
  generateCoachPlan,
  listRecords,
  saveCoachProfile,
  submitCoachCheckin,
} from '../api';
import type {
  CoachSessionDetail,
  Measurement,
  TrainingCheckin,
  TrainingProfileInput,
  TrainingSession,
  TrainingSport,
  TrainingStep,
  Workout,
} from '../types';
import {
  Button,
  Card,
  Empty,
  Field,
  formatShortDate,
  ListGroup,
  Row,
  Section,
  Sheet,
  Stat,
  todayIso,
  useLoader,
} from '../ui';
import { SkeletonScreen } from '../Skeleton';

const SPORT_LABELS: Record<TrainingSport, string> = {
  running: 'Corrida',
  canoeing: 'Canoa',
  cycling: 'Ciclismo',
  swimming: 'Natação',
  strength: 'Força',
  mobility: 'Mobilidade',
  functional: 'Funcional',
  walking: 'Caminhada',
  hiking: 'Trilha',
  rowing: 'Remo',
};

const SPORT_OPTIONS = Object.entries(SPORT_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const WEEKDAYS = [
  { value: 1, label: 'S' },
  { value: 2, label: 'T' },
  { value: 3, label: 'Q' },
  { value: 4, label: 'Q' },
  { value: 5, label: 'S' },
  { value: 6, label: 'S' },
  { value: 7, label: 'D' },
];

export function trainingProfileScheduleError(
  profile: Pick<
    TrainingProfileInput,
    'weekly_days' | 'available_weekdays'
  >,
): string {
  return profile.available_weekdays.length < profile.weekly_days
    ? 'Selecione dias suficientes para a frequência semanal.'
    : '';
}

export function sportLabel(sport: TrainingSport): string {
  return SPORT_LABELS[sport];
}

type ReadinessCopy = {
  title: string;
  detail: string;
  tone: 'ok' | 'warn' | 'danger';
};

const READINESS_COPY: Record<
  TrainingCheckin['recommendation'],
  ReadinessCopy
> = {
  ready: {
    title: 'Pronto para treinar',
    detail: 'Execute a sessão como prescrita e mantenha o esforço controlado.',
    tone: 'ok',
  },
  reduce_load: {
    title: 'Reduza a carga',
    detail: 'Diminua volume e intensidade hoje. Técnica vem antes da meta.',
    tone: 'warn',
  },
  recovery_only: {
    title: 'Somente recuperação',
    detail: 'Troque a sessão por mobilidade ou movimento muito leve.',
    tone: 'warn',
  },
  stop_and_seek_care: {
    title: 'Não inicie a sessão',
    detail: 'Dor alta exige interrupção e avaliação profissional.',
    tone: 'danger',
  },
};

export function recommendationCopy(
  recommendation: TrainingCheckin['recommendation'],
): ReadinessCopy {
  return READINESS_COPY[recommendation];
}

export function formatTrainingTarget(step: TrainingStep): string {
  const parts: string[] = [];
  if (step.sets > 0 && step.reps > 0) parts.push(`${step.sets} × ${step.reps}`);
  else if (step.duration_sec > 0) {
    const minutes = Math.round(step.duration_sec / 60);
    parts.push(minutes > 0 ? `${minutes} min` : `${step.duration_sec}s`);
  }
  if (step.distance_m > 0) {
    parts.push(
      step.distance_m >= 1000
        ? `${(step.distance_m / 1000).toLocaleString('pt-BR')} km`
        : `${step.distance_m} m`,
    );
  }
  if (step.rest_sec > 0) parts.push(`pausa ${step.rest_sec}s`);
  if (step.target_rpe > 0) parts.push(`RPE ${step.target_rpe}`);
  return parts.join(' · ');
}

export function TrainingSteps({ steps }: { steps: TrainingStep[] }) {
  return (
    <div className="oj-coach-steps">
      {steps.map((step, index) => (
        <article className="oj-coach-step" key={step.id}>
          <div className="oj-coach-step-index">{String(index + 1).padStart(2, '0')}</div>
          <div className="oj-coach-step-copy">
            <div className="oj-coach-step-title">{step.title}</div>
            <div className="oj-coach-step-target">{formatTrainingTarget(step)}</div>
            <p>{step.instructions}</p>
            {step.alternative && (
              <small>
                <strong>Alternativa:</strong> {step.alternative}
              </small>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field
      label={label}
      value={String(value)}
      onChange={(next) => onChange(Math.max(0, Math.min(10, Number(next) || 0)))}
      type="number"
    />
  );
}

function SessionExperience({
  sessionId,
  onChanged,
}: {
  sessionId: string;
  onChanged: () => void;
}) {
  const detail = useLoader(() => fetchCoachSession(sessionId), [sessionId]);
  const [checkin, setCheckin] = useState({
    sleep_quality: 7,
    soreness: 3,
    stress: 4,
    motivation: 7,
    pain: 0,
    notes: '',
  });
  const [completion, setCompletion] = useState({
    completion_pct: 100,
    actual_duration_min: 45,
    rpe: 6,
    energy: 7,
    pain: 0,
    notes: '',
  });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function saveCheckin() {
    setBusy('checkin');
    setError('');
    try {
      await submitCoachCheckin(sessionId, checkin);
      detail.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha no check-in');
    } finally {
      setBusy('');
    }
  }

  async function saveCompletion() {
    setBusy('complete');
    setError('');
    try {
      await completeCoachSession(sessionId, completion);
      detail.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao concluir sessão');
    } finally {
      setBusy('');
    }
  }

  if (detail.loading) return <SkeletonScreen />;
  if (detail.error) return <div className="oj-error">{detail.error}</div>;
  if (!detail.data) return null;
  const data: CoachSessionDetail = detail.data;
  const readiness = data.checkin
    ? recommendationCopy(data.checkin.recommendation)
    : null;

  return (
    <div className="oj-coach-session-detail">
      {error && <div className="oj-error">{error}</div>}
      <div className="oj-coach-session-hero">
        <div className="oj-coach-eyebrow">
          {sportLabel(data.session.sport)} · semana {data.session.week_index}
        </div>
        <h3>{data.session.title}</h3>
        <p>{data.session.objective}</p>
        <div className="oj-coach-pills">
          <span><Timer size={14} /> {data.session.estimated_min} min</span>
          <span><Gauge size={14} /> RPE {data.session.target_rpe}</span>
        </div>
      </div>

      {readiness && (
        <div className={`oj-coach-readiness oj-coach-readiness--${readiness.tone}`}>
          <ShieldAlert size={20} />
          <div><strong>{readiness.title}</strong><span>{readiness.detail}</span></div>
        </div>
      )}

      <Section title="Sessão prescrita">
        <TrainingSteps steps={data.steps} />
      </Section>

      {!data.checkin && data.session.status === 'planned' && (
        <Section title="Check-in antes de começar">
          <div className="oj-coach-metric-grid">
            <NumberField label="Sono 0–10" value={checkin.sleep_quality} onChange={(value) => setCheckin({ ...checkin, sleep_quality: value })} />
            <NumberField label="Dores musculares" value={checkin.soreness} onChange={(value) => setCheckin({ ...checkin, soreness: value })} />
            <NumberField label="Estresse" value={checkin.stress} onChange={(value) => setCheckin({ ...checkin, stress: value })} />
            <NumberField label="Motivação" value={checkin.motivation} onChange={(value) => setCheckin({ ...checkin, motivation: value })} />
            <NumberField label="Dor localizada" value={checkin.pain} onChange={(value) => setCheckin({ ...checkin, pain: value })} />
          </div>
          <Field label="Observação" value={checkin.notes} onChange={(notes) => setCheckin({ ...checkin, notes })} placeholder="Como você está hoje?" />
          <Button disabled={busy === 'checkin'} onClick={saveCheckin}>
            {busy === 'checkin' ? 'Analisando…' : 'Analisar prontidão'}
          </Button>
        </Section>
      )}

      {data.session.status === 'planned' && data.checkin && (
        <Section title="Feedback após a sessão">
          <div className="oj-coach-metric-grid">
            <Field label="Conclusão (%)" value={String(completion.completion_pct)} onChange={(value) => setCompletion({ ...completion, completion_pct: Math.max(0, Math.min(100, Number(value) || 0)) })} type="number" />
            <Field label="Duração (min)" value={String(completion.actual_duration_min)} onChange={(value) => setCompletion({ ...completion, actual_duration_min: Math.max(0, Number(value) || 0) })} type="number" />
            <NumberField label="Esforço RPE" value={completion.rpe} onChange={(value) => setCompletion({ ...completion, rpe: value })} />
            <NumberField label="Energia" value={completion.energy} onChange={(value) => setCompletion({ ...completion, energy: value })} />
            <NumberField label="Dor" value={completion.pain} onChange={(value) => setCompletion({ ...completion, pain: value })} />
          </div>
          <Field label="Observação" value={completion.notes} onChange={(notes) => setCompletion({ ...completion, notes })} placeholder="O que funcionou ou limitou?" />
          <Button disabled={busy === 'complete'} onClick={saveCompletion}>
            {busy === 'complete' ? 'Adaptando…' : 'Concluir e adaptar plano'}
          </Button>
        </Section>
      )}

      {data.feedback && (
        <div className="oj-coach-adaptation">
          <Sparkles size={18} />
          <div>
            <strong>Plano atualizado</strong>
            <span>{data.feedback.adaptation.message ?? 'Feedback registrado.'}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function SessionSheet({
  session,
  onClose,
  onChanged,
}: {
  session: TrainingSession;
  onClose: () => void;
  onChanged: () => void;
}) {
  return (
    <Sheet title={session.title} onClose={onClose}>
      <SessionExperience sessionId={session.id} onChanged={onChanged} />
    </Sheet>
  );
}

export function CoachToday({ onChanged }: { onChanged: () => void }) {
  const coach = useLoader(fetchCoachOverview);
  const [selected, setSelected] = useState<TrainingSession | null>(null);

  if (coach.loading) return <SkeletonScreen />;
  if (coach.error) return <div className="oj-error">{coach.error}</div>;
  if (!coach.data?.profile) {
    return (
      <div className="oj-coach-empty">
        <Activity size={32} />
        <h3>Seu coach precisa conhecer você</h3>
        <p>Defina modalidade, nível, agenda e histórico na aba Perfil.</p>
      </div>
    );
  }
  if (!coach.data.active_plan || !coach.data.next_session) {
    return (
      <div className="oj-coach-empty">
        <CalendarDays size={32} />
        <h3>Perfil pronto. Falta gerar o plano.</h3>
        <p>Use “Criar plano adaptativo” na aba Perfil.</p>
      </div>
    );
  }
  const next = coach.data.next_session;
  const completed = coach.data.sessions.filter((item) => item.status === 'completed').length;

  return (
    <>
      <div className="oj-coach-command-card">
        <div className="oj-coach-orbit"><Activity size={28} /></div>
        <div className="oj-coach-eyebrow">PRÓXIMA PRESCRIÇÃO · {sportLabel(next.sport)}</div>
        <h2>{next.title}</h2>
        <p>{next.objective}</p>
        <div className="oj-coach-pills">
          <span><CalendarDays size={14} /> {formatShortDate(next.scheduled_on)}</span>
          <span><Timer size={14} /> {next.estimated_min} min</span>
          <span><Gauge size={14} /> RPE {next.target_rpe}</span>
        </div>
        <Button onClick={() => setSelected(next)}>Abrir sessão <ChevronRight size={18} /></Button>
      </div>
      <div className="oj-stat-row">
        <Stat label="Concluídas" value={`${completed}/${coach.data.sessions.length}`} small />
        <Stat label="Semana" value={String(next.week_index)} small />
      </div>
      {selected && (
        <SessionSheet session={selected} onClose={() => setSelected(null)} onChanged={() => { coach.reload(); onChanged(); }} />
      )}
    </>
  );
}

export function CoachPlan({ onChanged }: { onChanged: () => void }) {
  const coach = useLoader(fetchCoachOverview);
  const [selected, setSelected] = useState<TrainingSession | null>(null);
  if (coach.loading) return <SkeletonScreen />;
  if (coach.error) return <div className="oj-error">{coach.error}</div>;
  if (!coach.data?.active_plan) return <Empty>Gere seu plano na aba Perfil.</Empty>;

  return (
    <>
      <Card label={`${coach.data.active_plan.weeks} SEMANAS · ${sportLabel(coach.data.active_plan.primary_sport)}`}>
        <div className="oj-coach-plan-title">{coach.data.active_plan.name}</div>
        <div className="oj-row-sub">{formatShortDate(coach.data.active_plan.start_on)} — {formatShortDate(coach.data.active_plan.end_on)}</div>
      </Card>
      <Section title="Agenda prescrita">
        <ListGroup>
          {coach.data.sessions.map((session) => (
            <Row
              key={session.id}
              title={session.title}
              sub={`${formatShortDate(session.scheduled_on)} · ${sportLabel(session.sport)} · ${session.estimated_min} min`}
              value={session.status === 'completed' ? 'Feito' : `RPE ${session.target_rpe}`}
              valueTone={session.status === 'completed' ? 'pos' : undefined}
              onClick={() => setSelected(session)}
            />
          ))}
        </ListGroup>
      </Section>
      {selected && <SessionSheet session={selected} onClose={() => setSelected(null)} onChanged={() => { coach.reload(); onChanged(); }} />}
    </>
  );
}

export function CoachProfile({ onChanged }: { onChanged: () => void }) {
  const coach = useLoader(fetchCoachOverview);
  const [form, setForm] = useState<TrainingProfileInput>({
    primary_sport: 'running',
    secondary_sports: ['strength'],
    primary_goal: 'general_fitness',
    target_distance_km: 0,
    target_date: null,
    level: 'beginner',
    weekly_days: 3,
    available_weekdays: [1, 3, 5],
    session_minutes: 45,
    current_weekly_km: 0,
    longest_recent_run_km: 0,
    equipment: [],
    limitations: '',
  });
  const [equipment, setEquipment] = useState('');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!coach.data?.profile) return;
    const profile = coach.data.profile;
    setForm({
      primary_sport: profile.primary_sport,
      secondary_sports: profile.secondary_sports,
      primary_goal: profile.primary_goal,
      target_distance_km: profile.target_distance_km,
      target_date: profile.target_date,
      level: profile.level,
      weekly_days: profile.weekly_days,
      available_weekdays: profile.available_weekdays,
      session_minutes: profile.session_minutes,
      current_weekly_km: profile.current_weekly_km,
      longest_recent_run_km: profile.longest_recent_run_km,
      equipment: profile.equipment,
      limitations: profile.limitations,
    });
    setEquipment(profile.equipment.join(', '));
  }, [coach.data?.profile]);

  function toggleWeekday(day: number) {
    const selected = form.available_weekdays.includes(day);
    setForm({
      ...form,
      available_weekdays: selected
        ? form.available_weekdays.filter((item) => item !== day)
        : [...form.available_weekdays, day].sort(),
    });
  }

  async function saveProfile() {
    const scheduleError = trainingProfileScheduleError(form);
    if (scheduleError) {
      setError(scheduleError);
      return;
    }
    setBusy('profile');
    setError('');
    try {
      await saveCoachProfile({
        ...form,
        equipment: equipment.split(',').map((item) => item.trim()).filter(Boolean),
      });
      setMessage('Perfil salvo. O motor já pode prescrever seu plano.');
      coach.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar perfil');
    } finally {
      setBusy('');
    }
  }

  async function buildPlan() {
    const scheduleError = trainingProfileScheduleError(form);
    if (scheduleError) {
      setError(scheduleError);
      return;
    }
    setBusy('plan');
    setError('');
    try {
      await saveCoachProfile({
        ...form,
        equipment: equipment.split(',').map((item) => item.trim()).filter(Boolean),
      });
      await generateCoachPlan(todayIso(), 8);
      setMessage('Plano de 8 semanas criado com sessões detalhadas.');
      coach.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao gerar plano');
    } finally {
      setBusy('');
    }
  }

  if (coach.loading) return <SkeletonScreen />;
  // O formulário abaixo nasce dos defaults do useState e só é sobrescrito pelo
  // useEffect quando o perfil chega. Se o loader falhou, esses defaults parecem
  // um perfil preenchido — e "Salvar perfil" grava condicionamento geral, 3 dias
  // e 45 minutos por cima do que o cliente realmente configurou. Não oferecer o
  // formulário é a única saída que não arrisca o dado dele.
  if (coach.error) {
    return (
      <div className="oj-error">
        Não deu para carregar seu perfil de treino: {coach.error}. Recarregue
        antes de editar — salvar agora sobrescreveria o que já está salvo.
      </div>
    );
  }
  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      {message && <div className="oj-success">{message}</div>}
      <div className="oj-coach-profile-grid">
        <Field label="Modalidade principal" value={form.primary_sport} onChange={(value) => setForm({ ...form, primary_sport: value as TrainingSport })} options={SPORT_OPTIONS} />
        <Field label="Objetivo" value={form.primary_goal} onChange={(value) => setForm({ ...form, primary_goal: value as TrainingProfileInput['primary_goal'] })} options={[
          { value: 'general_fitness', label: 'Condicionamento geral' },
          { value: 'endurance', label: 'Resistência / endurance' },
          { value: 'performance', label: 'Performance na modalidade' },
          { value: 'technique', label: 'Técnica e eficiência' },
          { value: 'strength', label: 'Ganho de força' },
          { value: 'hypertrophy', label: 'Hipertrofia sustentável' },
          { value: 'mobility', label: 'Mobilidade e controle' },
          { value: '5k', label: 'Meta 5 km' },
          { value: '10k', label: 'Meta 10 km' },
          { value: 'half_marathon', label: 'Meia maratona' },
        ]} />
        <Field label="Nível" value={form.level} onChange={(value) => setForm({ ...form, level: value as TrainingProfileInput['level'] })} options={[
          { value: 'beginner', label: 'Iniciante' },
          { value: 'intermediate', label: 'Intermediário' },
          { value: 'advanced', label: 'Avançado' },
        ]} />
        <Field label="Sessões por semana" value={String(form.weekly_days)} onChange={(value) => setForm({ ...form, weekly_days: Number(value) || 2 })} type="number" />
        <Field label="Minutos por sessão" value={String(form.session_minutes)} onChange={(value) => setForm({ ...form, session_minutes: Number(value) || 20 })} type="number" />
        <Field label="Volume atual (km/semana)" value={String(form.current_weekly_km)} onChange={(value) => setForm({ ...form, current_weekly_km: Number(value) || 0 })} type="number" />
        <Field label="Maior sessão recente (km)" value={String(form.longest_recent_run_km)} onChange={(value) => setForm({ ...form, longest_recent_run_km: Number(value) || 0 })} type="number" />
        <Field label="Equipamentos" value={equipment} onChange={setEquipment} placeholder="halteres, elástico, ergômetro" />
        <Field label="Limitações e histórico" value={form.limitations} onChange={(limitations) => setForm({ ...form, limitations })} placeholder="Lesões, dores, restrições confirmadas" />
      </div>
      <div className="oj-section-title">Dias disponíveis</div>
      <div className="oj-coach-weekdays">
        {WEEKDAYS.map((day) => (
          <button key={day.value} type="button" className={form.available_weekdays.includes(day.value) ? 'is-active' : ''} onClick={() => toggleWeekday(day.value)} aria-pressed={form.available_weekdays.includes(day.value)}>{day.label}</button>
        ))}
      </div>
      <div className="oj-coach-profile-actions">
        <Button variant="ghost" disabled={Boolean(busy)} onClick={saveProfile}>{busy === 'profile' ? 'Salvando…' : 'Salvar perfil'}</Button>
        <Button disabled={Boolean(busy)} onClick={buildPlan}>{busy === 'plan' ? 'Prescrevendo…' : 'Criar plano adaptativo'}</Button>
      </div>
      <div className="oj-coach-safety"><ShieldAlert size={18} /><span>Dor alta interrompe automaticamente a prescrição. O coach não substitui avaliação médica.</span></div>
    </>
  );
}

export function FitnessOverview() {
  const { data, error, loading } = useLoader(fetchFitnessSummary);

  if (loading) return <SkeletonScreen />;
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

  if (workouts.loading) return <SkeletonScreen />;
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

  if (measurements.loading) return <SkeletonScreen />;
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

export function FitnessProgress() {
  return (
    <>
      <FitnessOverview />
      <MeasurementsTab />
    </>
  );
}
