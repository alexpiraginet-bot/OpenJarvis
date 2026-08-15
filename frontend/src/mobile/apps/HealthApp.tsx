/** Saúde — dados confirmados, organização e correção manual. */

import {
  Activity,
  Droplets,
  FileText,
  Mic,
  Pill,
  Plus,
  ShieldAlert,
} from 'lucide-react';
import { useState } from 'react';
import {
  createRecord,
  fetchHealthSummary,
  recordHydration,
  saveHealthProfile,
} from '../api';
import type {
  Allergy,
  HealthCondition,
  HealthDocument,
  HealthObservation,
  HealthProfile,
  Medication,
  NutritionLog,
} from '../types';
import {
  Button,
  Empty,
  Field,
  formatShortDate,
  ListGroup,
  Row,
  Section,
  Sheet,
  Stat,
  useLoader,
} from '../ui';
import { SkeletonScreen } from '../Skeleton';

export function parsePositiveHealthNumber(input: string): number | null {
  const value = Number.parseFloat(input.trim().replace(',', '.'));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export interface HealthProfileDraft {
  birthDate: string;
  sexAtBirth: string;
  heightCm: string;
  bloodType: string;
  goals: string;
  emergencyContact: string;
  consentHealthMemory: boolean;
}

export function buildHealthProfileFields(draft: HealthProfileDraft) {
  return {
    birth_date: draft.birthDate || null,
    sex_at_birth: draft.sexAtBirth.trim(),
    height_cm: parsePositiveHealthNumber(draft.heightCm) ?? 0,
    blood_type: draft.bloodType.trim(),
    goals: draft.goals.trim(),
    emergency_contact: draft.emergencyContact.trim(),
    consent_health_memory: draft.consentHealthMemory ? 1 : 0,
  };
}

const OBSERVATION_LABELS: Record<string, string> = {
  peso: 'Peso',
  weight: 'Peso',
  glicose: 'Glicose',
  glucose: 'Glicose',
  pressao: 'Pressão arterial',
  blood_pressure: 'Pressão arterial',
  frequencia_cardiaca: 'Frequência cardíaca',
  heart_rate: 'Frequência cardíaca',
  resting_heart_rate: 'Frequência cardíaca em repouso',
  steps: 'Passos',
  sleep_hours: 'Sono',
  active_energy: 'Energia ativa',
  workout_minutes: 'Minutos de treino',
  temperatura: 'Temperatura',
};

export function formatHealthObservation(
  observation: Pick<HealthObservation, 'kind' | 'value' | 'unit' | 'observed_at'>,
) {
  const kind = observation.kind.trim().toLocaleLowerCase('pt-BR');
  const title =
    OBSERVATION_LABELS[kind] ??
    `${kind.slice(0, 1).toLocaleUpperCase('pt-BR')}${kind.slice(1)}`;
  const value = observation.value.toLocaleString('pt-BR', {
    maximumFractionDigits: 2,
  });
  return {
    title,
    detail: `${value} ${observation.unit.trim()} · ${formatShortDate(
      observation.observed_at,
    )}`,
  };
}

export function HealthSafetyNotice() {
  return (
    <div className="oj-card" role="note" aria-label="Limites do assistente de saúde">
      <div className="oj-card-label">
        <ShieldAlert size={15} /> Uso seguro
      </div>
      <p className="oj-row-sub">
        O Jarvis organiza dados confirmados por você; não diagnostica nem prescreve
        e não substitui profissionais de saúde.
      </p>
      <p className="oj-row-sub">
        Em sintomas graves ou risco imediato, procure atendimento de emergência ou
        ligue para o SAMU 192.
      </p>
    </div>
  );
}

function VoiceFirstHealthCard() {
  return (
    <div className="oj-card">
      <div className="oj-card-label">
        <Mic size={15} /> Voz primeiro
      </div>
      <div className="oj-stat--sm">Fale com o especialista</div>
      <p className="oj-row-sub">
        Use IA no topo e diga: “registre 350 ml de água”, “anote meu peso” ou
        “organize meus exames”. Os formulários abaixo servem para correção manual.
      </p>
    </div>
  );
}

export function HealthOverview() {
  const health = useLoader(fetchHealthSummary);
  if (health.loading) return <SkeletonScreen />;
  if (health.error) return <div className="oj-error">{health.error}</div>;
  if (!health.data) return null;

  const data = health.data;
  return (
    <>
      <VoiceFirstHealthCard />
      <div className="oj-stat-row">
        <Stat label="Água hoje" value={`${data.hydration_today_ml} ml`} small />
        <Stat label="Refeições" value={String(data.nutrition_today_count)} small />
      </div>
      <div className="oj-stat-row">
        <Stat
          label="Dados ativos"
          value={String(
            data.active_conditions.length +
              data.active_medications.length +
              data.allergies.length,
          )}
          small
        />
        <Stat label="Documentos" value={String(data.documents.length)} small />
      </div>

      <Section title="Objetivos pessoais">
        <div className="oj-card">
          <div className="oj-row-title">
            {data.profile?.goals || 'Conte ao Jarvis o que você quer melhorar.'}
          </div>
          <div className="oj-row-sub">
            Metas são pessoais e editáveis; não representam recomendação clínica.
          </div>
        </div>
      </Section>

      <Section title="Últimas métricas">
        {data.latest_observations.length === 0 ? (
          <Empty>Nenhuma métrica confirmada ainda.</Empty>
        ) : (
          <ListGroup>
            {data.latest_observations.map((observation) => {
              const formatted = formatHealthObservation(observation);
              return (
                <Row
                  key={observation.id}
                  title={formatted.title}
                  sub={formatted.detail}
                  leading={<Activity size={18} color="var(--oj-cyan)" />}
                />
              );
            })}
          </ListGroup>
        )}
      </Section>

      <HealthSafetyNotice />
    </>
  );
}

const EMPTY_PROFILE: HealthProfileDraft = {
  birthDate: '',
  sexAtBirth: '',
  heightCm: '',
  bloodType: '',
  goals: '',
  emergencyContact: '',
  consentHealthMemory: false,
};

type HealthFactKind = 'condition' | 'medication' | 'allergy';

export function HealthProfileTab({ onChanged }: { onChanged: () => void }) {
  const health = useLoader(fetchHealthSummary);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<HealthProfileDraft>(EMPTY_PROFILE);
  const [factKind, setFactKind] = useState<HealthFactKind>('condition');
  const [factName, setFactName] = useState('');
  const [factDetail, setFactDetail] = useState('');
  const [addingFact, setAddingFact] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function openProfile(profile: HealthProfile | null) {
    setDraft({
      birthDate: profile?.birth_date ?? '',
      sexAtBirth: profile?.sex_at_birth ?? '',
      heightCm: profile?.height_cm ? String(profile.height_cm).replace('.', ',') : '',
      bloodType: profile?.blood_type ?? '',
      goals: profile?.goals ?? '',
      emergencyContact: profile?.emergency_contact ?? '',
      consentHealthMemory: profile?.consent_health_memory === 1,
    });
    setError('');
    setEditing(true);
  }

  async function saveProfile() {
    if (draft.heightCm.trim() && parsePositiveHealthNumber(draft.heightCm) === null) {
      setError('Informe uma altura válida em centímetros.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await saveHealthProfile(
        health.data?.profile?.id ?? null,
        buildHealthProfileFields(draft),
      );
      setEditing(false);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar o perfil.');
    } finally {
      setBusy(false);
    }
  }

  async function saveFact() {
    if (!factName.trim()) {
      setError('Informe o dado de saúde confirmado.');
      return;
    }
    setBusy(true);
    setError('');
    const confirmedAt = new Date().toISOString();
    try {
      if (factKind === 'condition') {
        await createRecord<HealthCondition>('health_conditions', {
          name: factName.trim(),
          status: 'active',
          notes: factDetail.trim(),
          source: 'manual',
          confirmed_at: confirmedAt,
        });
      } else if (factKind === 'medication') {
        await createRecord<Medication>('medications', {
          name: factName.trim(),
          dose_text: factDetail.trim(),
          status: 'active',
          source: 'manual',
          confirmed_at: confirmedAt,
        });
      } else {
        await createRecord<Allergy>('allergies', {
          substance: factName.trim(),
          reaction: factDetail.trim(),
          severity: 'unknown',
          source: 'manual',
          confirmed_at: confirmedAt,
        });
      }
      setFactName('');
      setFactDetail('');
      setAddingFact(false);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar o dado.');
    } finally {
      setBusy(false);
    }
  }

  if (health.loading) return <SkeletonScreen />;
  if (health.error) return <div className="oj-error">{health.error}</div>;
  const data = health.data;
  if (!data) return null;

  return (
    <>
      {error && <div className="oj-error" role="alert">{error}</div>}
      <Button onClick={() => openProfile(data.profile)}>
        <Plus size={18} /> {data.profile ? 'Corrigir perfil e metas' : 'Criar perfil e metas'}
      </Button>

      <Section title="Perfil confirmado">
        {data.profile ? (
          <ListGroup>
            <Row title="Objetivos" value={data.profile.goals || 'Não definidos'} />
            <Row
              title="Altura"
              value={data.profile.height_cm ? `${data.profile.height_cm} cm` : '—'}
            />
            <Row title="Tipo sanguíneo" value={data.profile.blood_type || '—'} />
            <Row
              title="Memória de saúde"
              value={data.profile.consent_health_memory ? 'Autorizada' : 'Desativada'}
            />
          </ListGroup>
        ) : (
          <Empty>Perfil ainda não preenchido. A IA não presume seus dados.</Empty>
        )}
      </Section>

      <Button variant="ghost" onClick={() => setAddingFact(true)}>
        <Plus size={18} /> Adicionar dado confirmado
      </Button>

      <Section title="Condições, medicamentos e alergias">
        {data.active_conditions.length + data.active_medications.length + data.allergies.length === 0 ? (
          <Empty>Nenhum dado ativo informado.</Empty>
        ) : (
          <ListGroup>
            {data.active_conditions.map((item) => (
              <Row key={item.id} title={item.name} sub={item.notes || 'Condição informada'} />
            ))}
            {data.active_medications.map((item) => (
              <Row
                key={item.id}
                title={item.name}
                sub={[item.dose_text, item.frequency].filter(Boolean).join(' · ') || 'Medicamento informado'}
                leading={<Pill size={18} color="var(--oj-cyan)" />}
              />
            ))}
            {data.allergies.map((item) => (
              <Row key={item.id} title={item.substance} sub={item.reaction || 'Alergia informada'} />
            ))}
          </ListGroup>
        )}
      </Section>

      <HealthSafetyNotice />

      {editing && (
        <Sheet title="Perfil e objetivos" onClose={() => setEditing(false)}>
          <Field label="Data de nascimento" value={draft.birthDate} onChange={(value) => setDraft((current) => ({ ...current, birthDate: value }))} type="date" />
          <Field label="Sexo ao nascer (opcional)" value={draft.sexAtBirth} onChange={(value) => setDraft((current) => ({ ...current, sexAtBirth: value }))} />
          <Field label="Altura (cm)" value={draft.heightCm} onChange={(value) => setDraft((current) => ({ ...current, heightCm: value }))} placeholder="168,5" />
          <Field label="Tipo sanguíneo" value={draft.bloodType} onChange={(value) => setDraft((current) => ({ ...current, bloodType: value }))} placeholder="O+" />
          <Field label="Objetivos pessoais" value={draft.goals} onChange={(value) => setDraft((current) => ({ ...current, goals: value }))} placeholder="Dormir melhor, ganhar condicionamento…" />
          <Field label="Contato de emergência" value={draft.emergencyContact} onChange={(value) => setDraft((current) => ({ ...current, emergencyContact: value }))} placeholder="Nome e telefone" />
          <label className="oj-row">
            <input
              type="checkbox"
              checked={draft.consentHealthMemory}
              onChange={(event) => setDraft((current) => ({ ...current, consentHealthMemory: event.target.checked }))}
            />
            <span className="oj-row-body">
              <span className="oj-row-title">Autorizar memória de saúde</span>
              <span className="oj-row-sub">Permite usar somente seus dados confirmados para personalizar respostas.</span>
            </span>
          </label>
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={saveProfile}>{busy ? 'Salvando…' : 'Salvar correção'}</Button>
        </Sheet>
      )}

      {addingFact && (
        <Sheet title="Dado confirmado" onClose={() => setAddingFact(false)}>
          <Field
            label="Tipo"
            value={factKind}
            onChange={(value) => setFactKind(value as HealthFactKind)}
            options={[
              { value: 'condition', label: 'Condição informada' },
              { value: 'medication', label: 'Medicamento informado' },
              { value: 'allergy', label: 'Alergia informada' },
            ]}
          />
          <Field label={factKind === 'allergy' ? 'Substância' : 'Nome'} value={factName} onChange={setFactName} />
          <Field label={factKind === 'medication' ? 'Dose conforme receita (opcional)' : 'Detalhes (opcional)'} value={factDetail} onChange={setFactDetail} />
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={saveFact}>{busy ? 'Salvando…' : 'Salvar dado confirmado'}</Button>
        </Sheet>
      )}
    </>
  );
}

type RecordSheet = 'water' | 'metric' | 'meal' | null;

export function HealthRecordsTab({ onChanged }: { onChanged: () => void }) {
  const health = useLoader(fetchHealthSummary);
  const [sheet, setSheet] = useState<RecordSheet>(null);
  const [water, setWater] = useState('');
  const [metricKind, setMetricKind] = useState('peso');
  const [metricValue, setMetricValue] = useState('');
  const [metricUnit, setMetricUnit] = useState('kg');
  const [mealType, setMealType] = useState('meal');
  const [mealDescription, setMealDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function addWater(amount: number) {
    setBusy(true);
    setError('');
    try {
      await recordHydration(amount);
      setWater('');
      setSheet(null);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao registrar água.');
    } finally {
      setBusy(false);
    }
  }

  async function addMetric() {
    const value = parsePositiveHealthNumber(metricValue);
    if (value === null || !metricKind.trim() || !metricUnit.trim()) {
      setError('Informe métrica, valor positivo e unidade.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await createRecord<HealthObservation>('health_observations', {
        kind: metricKind.trim(),
        value,
        unit: metricUnit.trim(),
        observed_at: new Date().toISOString(),
        source: 'manual',
      });
      setMetricValue('');
      setSheet(null);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao registrar métrica.');
    } finally {
      setBusy(false);
    }
  }

  async function addMeal() {
    if (!mealDescription.trim()) {
      setError('Descreva a refeição confirmada.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await createRecord<NutritionLog>('nutrition_logs', {
        meal_type: mealType,
        description: mealDescription.trim(),
        occurred_at: new Date().toISOString(),
        source: 'manual',
      });
      setMealDescription('');
      setSheet(null);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao registrar refeição.');
    } finally {
      setBusy(false);
    }
  }

  if (health.loading) return <SkeletonScreen />;
  if (health.error) return <div className="oj-error">{health.error}</div>;
  const data = health.data;
  if (!data) return null;

  return (
    <>
      {error && <div className="oj-error" role="alert">{error}</div>}
      <VoiceFirstHealthCard />
      <div className="oj-stat-row">
        <Stat label="Hidratação hoje" value={`${data.hydration_today_ml} ml`} small />
        <Stat label="Refeições hoje" value={String(data.nutrition_today_count)} small />
      </div>
      <div className="oj-stat-row">
        <Button disabled={busy} onClick={() => addWater(250)}><Droplets size={17} /> +250 ml</Button>
        <Button disabled={busy} variant="ghost" onClick={() => addWater(500)}><Droplets size={17} /> +500 ml</Button>
      </div>
      <div className="oj-section-title">Correção manual</div>
      <div className="oj-health-actions">
        <Button variant="ghost" onClick={() => setSheet('water')}><Plus size={17} /> Outra quantidade de água</Button>
        <Button variant="ghost" onClick={() => setSheet('metric')}><Activity size={17} /> Nova métrica</Button>
        <Button variant="ghost" onClick={() => setSheet('meal')}><Plus size={17} /> Registrar refeição</Button>
      </div>

      <Section title="Métricas confirmadas">
        {data.latest_observations.length === 0 ? (
          <Empty>Nenhuma métrica registrada.</Empty>
        ) : (
          <ListGroup>
            {data.latest_observations.map((observation) => {
              const formatted = formatHealthObservation(observation);
              return <Row key={observation.id} title={formatted.title} sub={formatted.detail} />;
            })}
          </ListGroup>
        )}
      </Section>

      {sheet === 'water' && (
        <Sheet title="Registrar água" onClose={() => setSheet(null)}>
          <Field label="Quantidade (ml)" value={water} onChange={setWater} placeholder="350" />
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={() => {
            const amount = parsePositiveHealthNumber(water);
            if (amount === null) setError('Informe uma quantidade válida em ml.');
            else void addWater(Math.round(amount));
          }}>{busy ? 'Salvando…' : 'Registrar'}</Button>
        </Sheet>
      )}

      {sheet === 'metric' && (
        <Sheet title="Nova métrica" onClose={() => setSheet(null)}>
          <Field label="Métrica" value={metricKind} onChange={setMetricKind} placeholder="peso" />
          <Field label="Valor" value={metricValue} onChange={setMetricValue} placeholder="78,5" />
          <Field label="Unidade" value={metricUnit} onChange={setMetricUnit} placeholder="kg" />
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={addMetric}>{busy ? 'Salvando…' : 'Registrar métrica'}</Button>
        </Sheet>
      )}

      {sheet === 'meal' && (
        <Sheet title="Registrar refeição" onClose={() => setSheet(null)}>
          <Field label="Tipo" value={mealType} onChange={setMealType} options={[
            { value: 'breakfast', label: 'Café da manhã' },
            { value: 'lunch', label: 'Almoço' },
            { value: 'dinner', label: 'Jantar' },
            { value: 'snack', label: 'Lanche' },
            { value: 'meal', label: 'Outra refeição' },
          ]} />
          <Field label="Descrição" value={mealDescription} onChange={setMealDescription} placeholder="Itens que você confirmou" />
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={addMeal}>{busy ? 'Salvando…' : 'Registrar refeição'}</Button>
        </Sheet>
      )}
    </>
  );
}

export function HealthDocumentsTab({ onChanged }: { onChanged: () => void }) {
  const health = useLoader(fetchHealthSummary);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [kind, setKind] = useState('exam');
  const [date, setDate] = useState('');
  const [provider, setProvider] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function saveDocument() {
    if (!name.trim()) {
      setError('Informe o nome do documento.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await createRecord<HealthDocument>('health_documents', {
        name: name.trim(),
        kind,
        document_date: date || null,
        provider: provider.trim(),
        status: 'registered',
        notes: notes.trim(),
        source: 'manual',
      });
      setName('');
      setDate('');
      setProvider('');
      setNotes('');
      setAdding(false);
      health.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao registrar documento.');
    } finally {
      setBusy(false);
    }
  }

  if (health.loading) return <SkeletonScreen />;
  if (health.error) return <div className="oj-error">{health.error}</div>;
  const documents = health.data?.documents ?? [];

  return (
    <>
      {error && <div className="oj-error" role="alert">{error}</div>}
      <div className="oj-card" role="note">
        <div className="oj-card-label"><FileText size={15} /> Cofre documental</div>
        <div className="oj-row-title">Metadados organizados com segurança</div>
        <p className="oj-row-sub">
          Este build registra nome, data, origem e suas notas. Ele não envia o arquivo
          bruto nem interpreta resultado clínico sem armazenamento criptografado próprio.
        </p>
      </div>
      <Button onClick={() => setAdding(true)}><Plus size={18} /> Registrar exame ou documento</Button>

      <Section title="Histórico documental">
        {documents.length === 0 ? (
          <Empty>Nenhum exame ou documento cadastrado.</Empty>
        ) : (
          <ListGroup>
            {documents.map((document) => (
              <Row
                key={document.id}
                title={document.name}
                sub={[
                  document.kind,
                  formatShortDate(document.document_date),
                  document.provider,
                  document.notes,
                ].filter(Boolean).join(' · ')}
                leading={<FileText size={18} color="var(--oj-cyan)" />}
              />
            ))}
          </ListGroup>
        )}
      </Section>
      <HealthSafetyNotice />

      {adding && (
        <Sheet title="Documento de saúde" onClose={() => setAdding(false)}>
          <Field label="Nome" value={name} onChange={setName} placeholder="Hemograma completo" />
          <Field label="Tipo" value={kind} onChange={setKind} options={[
            { value: 'exam', label: 'Exame' },
            { value: 'report', label: 'Laudo' },
            { value: 'prescription', label: 'Receita' },
            { value: 'vaccination', label: 'Vacina' },
            { value: 'other', label: 'Outro' },
          ]} />
          <Field label="Data" value={date} onChange={setDate} type="date" />
          <Field label="Profissional ou laboratório" value={provider} onChange={setProvider} />
          <Field label="Notas confirmadas por você" value={notes} onChange={setNotes} />
          {error && <div className="oj-error" role="alert">{error}</div>}
          <Button disabled={busy} onClick={saveDocument}>{busy ? 'Salvando…' : 'Registrar metadados'}</Button>
        </Sheet>
      )}
    </>
  );
}
