/** Trabalho — tarefas e projetos. */

import { Check, Plus } from 'lucide-react';
import { useState } from 'react';
import {
  completeTask,
  createRecord,
  fetchWorkSummary,
  listRecords,
} from '../api';
import type { Project, WorkTask } from '../types';
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

export function TasksTab({ onChanged }: { onChanged: () => void }) {
  const summary = useLoader(fetchWorkSummary);
  const tasks = useLoader(() =>
    listRecords<WorkTask>('work_tasks', { order_by: 'created_at', limit: 100 }),
  );
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState('');
  const [title, setTitle] = useState('');
  const [dueOn, setDueOn] = useState('');
  const [priority, setPriority] = useState('normal');
  const [error, setError] = useState('');

  async function handleComplete(task: WorkTask) {
    setBusy(task.id);
    try {
      await completeTask(task.id);
      tasks.reload();
      summary.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao concluir');
    } finally {
      setBusy('');
    }
  }

  async function handleAdd() {
    if (!title.trim()) {
      setError('Descreva a tarefa.');
      return;
    }
    setError('');
    try {
      await createRecord<WorkTask>('work_tasks', {
        title: title.trim(),
        priority,
        due_on: dueOn || null,
      });
      setAdding(false);
      setTitle('');
      setDueOn('');
      tasks.reload();
      summary.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (tasks.loading) return <SkeletonScreen />;
  if (summary.error || tasks.error) return <div className="oj-error">{summary.error || tasks.error}</div>;

  const records = tasks.data?.records ?? [];
  const open = records.filter((task) => task.status !== 'done');
  const done = records.filter((task) => task.status === 'done');
  const overdueIds = new Set((summary.data?.overdue ?? []).map((t) => t.id));
  const todayIds = new Set((summary.data?.due_today ?? []).map((t) => t.id));

  return (
    <>
      {error && <div className="oj-error">{error}</div>}

      <div className="oj-stat-row">
        <Stat
          label="Abertas"
          value={String(summary.data?.open_count ?? open.length)}
          small
        />
        <Stat
          label="Atrasadas"
          value={String(summary.data?.overdue.length ?? 0)}
          tone={summary.data?.overdue.length ? 'neg' : undefined}
          small
        />
      </div>

      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Nova tarefa
      </Button>

      <Section title="Abertas">
        {open.length === 0 ? (
          <Empty>Nada em aberto. Bom trabalho.</Empty>
        ) : (
          <ListGroup>
            {open.map((task) => (
              <div key={task.id} className="oj-row">
                <button
                  type="button"
                  className="oj-check"
                  data-done={false}
                  disabled={busy === task.id}
                  onClick={() => handleComplete(task)}
                  aria-label={`Concluir ${task.title}`}
                />
                <div className="oj-row-body">
                  <div className="oj-row-title">{task.title}</div>
                  <div className="oj-row-sub">
                    {overdueIds.has(task.id)
                      ? `Atrasada desde ${formatShortDate(task.due_on)}`
                      : todayIds.has(task.id)
                        ? 'Vence hoje'
                        : task.due_on
                          ? `Vence ${formatShortDate(task.due_on)}`
                          : task.priority}
                  </div>
                </div>
                {overdueIds.has(task.id) && (
                  <span className="oj-row-value oj-neg">!</span>
                )}
              </div>
            ))}
          </ListGroup>
        )}
      </Section>

      {done.length > 0 && (
        <Section title="Concluídas">
          <ListGroup>
            {done.slice(0, 12).map((task) => (
              <Row
                key={task.id}
                title={task.title}
                leading={<Check size={18} color="var(--oj-ok)" />}
                sub={task.done_at ? formatShortDate(task.done_at) : ''}
              />
            ))}
          </ListGroup>
        </Section>
      )}

      {adding && (
        <Sheet title="Nova tarefa" onClose={() => setAdding(false)}>
          <Field
            label="Tarefa"
            value={title}
            onChange={setTitle}
            placeholder="Enviar proposta"
          />
          <Field label="Prazo" value={dueOn} onChange={setDueOn} type="date" />
          <Field
            label="Prioridade"
            value={priority}
            onChange={setPriority}
            options={[
              { value: 'low', label: 'Baixa' },
              { value: 'normal', label: 'Normal' },
              { value: 'high', label: 'Alta' },
            ]}
          />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}

export function ProjectsTab() {
  const projects = useLoader(() =>
    listRecords<Project>('projects', { limit: 50 }),
  );
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [dueOn, setDueOn] = useState('');
  const [error, setError] = useState('');

  async function handleAdd() {
    if (!name.trim()) {
      setError('Dê um nome ao projeto.');
      return;
    }
    setError('');
    try {
      await createRecord<Project>('projects', {
        name: name.trim(),
        due_on: dueOn || null,
      });
      setAdding(false);
      setName('');
      setDueOn('');
      projects.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (projects.loading) return <SkeletonScreen />;
  if (projects.error) return <div className="oj-error">{projects.error}</div>;
  const records = projects.data?.records ?? [];

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Novo projeto
      </Button>

      <Section title="Projetos">
        {records.length === 0 ? (
          <Empty>Nenhum projeto ainda.</Empty>
        ) : (
          <ListGroup>
            {records.map((project) => (
              <Row
                key={project.id}
                title={project.name}
                sub={[
                  project.status,
                  project.due_on ? `prazo ${formatShortDate(project.due_on)}` : '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {adding && (
        <Sheet title="Novo projeto" onClose={() => setAdding(false)}>
          <Field
            label="Nome"
            value={name}
            onChange={setName}
            placeholder="Lançamento do app"
          />
          <Field label="Prazo" value={dueOn} onChange={setDueOn} type="date" />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}
