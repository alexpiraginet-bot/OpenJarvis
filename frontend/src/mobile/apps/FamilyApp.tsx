/** Família — pessoas, aniversários e eventos. */

import { Cake, Plus } from 'lucide-react';
import { useState } from 'react';
import { createRecord, fetchFamilySummary, listFamily } from '../api';
import type { FamilyMember } from '../types';
import {
  Button,
  Empty,
  Field,
  formatShortDate,
  ListGroup,
  Row,
  Section,
  Sheet,
  useLoader,
} from '../ui';
import { SkeletonScreen } from '../Skeleton';

/** "hoje" / "amanhã" / "em 5 dias" — how a person actually says it. */
function whenLabel(daysAway: number): string {
  if (daysAway === 0) return 'hoje';
  if (daysAway === 1) return 'amanhã';
  return `em ${daysAway} dias`;
}

export function UpcomingTab() {
  const upcoming = useLoader(fetchFamilySummary);

  if (upcoming.loading) return <SkeletonScreen />;
  if (upcoming.error) return <div className="oj-error">{upcoming.error}</div>;

  const events = upcoming.data?.upcoming ?? [];

  return (
    <Section title="Próximos 30 dias">
      {events.length === 0 ? (
        <Empty>Nada marcado. Cadastre aniversários na aba Pessoas.</Empty>
      ) : (
        <ListGroup>
          {events.map((event) => (
            <Row
              key={`${event.kind}-${event.member_id}-${event.date}`}
              title={event.title}
              sub={`${formatShortDate(event.date)} · ${whenLabel(event.days_away)}${
                event.turning ? ` · faz ${event.turning}` : ''
              }`}
              leading={
                event.kind === 'birthday' ? (
                  <Cake size={18} color="var(--oj-warning)" />
                ) : undefined
              }
              valueTone={event.days_away <= 1 ? 'warn' : undefined}
              value={event.days_away <= 1 ? '!' : ''}
            />
          ))}
        </ListGroup>
      )}
    </Section>
  );
}

export function PeopleTab({ onChanged }: { onChanged: () => void }) {
  const members = useLoader(listFamily);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [relation, setRelation] = useState('');
  const [birthday, setBirthday] = useState('');
  const [phone, setPhone] = useState('');
  const [error, setError] = useState('');

  async function handleAdd() {
    if (!name.trim()) {
      setError('Informe o nome.');
      return;
    }
    setError('');
    try {
      await createRecord<FamilyMember>('family_members', {
        name: name.trim(),
        relation: relation.trim(),
        // The column is nullable; an empty string would be read back as a
        // birthday and quietly fail to parse.
        birthday: birthday || null,
        phone: phone.trim(),
      });
      setAdding(false);
      setName('');
      setRelation('');
      setBirthday('');
      setPhone('');
      members.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (members.loading) return <SkeletonScreen />;
  if (members.error) return <div className="oj-error">{members.error}</div>;
  const records = members.data?.records ?? [];

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Nova pessoa
      </Button>

      <Section title="Pessoas">
        {records.length === 0 ? (
          <Empty>Ninguém cadastrado ainda.</Empty>
        ) : (
          <ListGroup>
            {records.map((member) => (
              <Row
                key={member.id}
                title={member.name}
                sub={[
                  member.relation,
                  member.birthday ? `nasc. ${formatShortDate(member.birthday)}` : '',
                  member.phone,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {adding && (
        <Sheet title="Nova pessoa" onClose={() => setAdding(false)}>
          <Field label="Nome" value={name} onChange={setName} placeholder="Maria" />
          <Field
            label="Parentesco"
            value={relation}
            onChange={setRelation}
            placeholder="mãe"
          />
          <Field
            label="Aniversário"
            value={birthday}
            onChange={setBirthday}
            type="date"
          />
          <Field
            label="Telefone"
            value={phone}
            onChange={setPhone}
            placeholder="(11) 90000-0000"
          />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}
