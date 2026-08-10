/** Finanças — saldo, contas a pagar, gastos e metas. */

import { Plus } from 'lucide-react';
import { useState } from 'react';
import {
  createRecord,
  fetchFinanceSummary,
  listAccounts,
  listBills,
  listRecords,
  payBill,
} from '../api';
import type { Account, Bill, Goal, Transaction } from '../types';
import {
  Button,
  Empty,
  Field,
  formatMoney,
  formatShortDate,
  ListGroup,
  parseMoney,
  ProgressBar,
  Row,
  Section,
  Sheet,
  Spinner,
  Stat,
  todayIso,
  useLoader,
} from '../ui';

const CATEGORIES = [
  'mercado',
  'transporte',
  'casa',
  'saúde',
  'lazer',
  'educação',
  'restaurante',
  'outros',
];

const CATEGORY_OPTIONS = CATEGORIES.map((value) => ({ value, label: value }));

export function FinanceOverview({ currency }: { currency: string }) {
  const { data, error, loading } = useLoader(fetchFinanceSummary);

  if (loading) return <Spinner />;
  if (error) return <div className="oj-error">{error}</div>;
  if (!data) return null;

  const biggest = data.by_category[0]?.total ?? 0;

  return (
    <>
      <Stat label="Saldo total" value={formatMoney(data.balance_cents, currency)} />
      <div className="oj-stat-row">
        <Stat
          label="Entrou no mês"
          value={formatMoney(data.income_cents, currency)}
          tone="pos"
          small
        />
        <Stat
          label="Saiu no mês"
          value={formatMoney(data.expense_cents, currency)}
          tone="neg"
          small
        />
      </div>

      {data.budgets.length > 0 && (
        <Section title="Orçamentos">
          <ListGroup>
            {data.budgets.map((budget) => (
              <div key={budget.id} className="oj-row">
                <div className="oj-row-body">
                  <div className="oj-row-title">{budget.category}</div>
                  <div className="oj-row-sub">
                    {formatMoney(budget.spent_cents, currency)} de{' '}
                    {formatMoney(budget.limit_cents, currency)}
                  </div>
                  <ProgressBar pct={budget.pct_used} />
                </div>
                <div
                  className={
                    'oj-row-value ' +
                    (budget.remaining_cents < 0 ? 'oj-neg' : 'oj-pos')
                  }
                >
                  {budget.pct_used}%
                </div>
              </div>
            ))}
          </ListGroup>
        </Section>
      )}

      <Section title="Gastos por categoria">
        {data.by_category.length === 0 ? (
          <Empty>Nenhum gasto neste mês.</Empty>
        ) : (
          <ListGroup>
            {data.by_category.map((entry) => (
              <div key={entry.label} className="oj-row">
                <div className="oj-row-body">
                  <div className="oj-row-title">{entry.label}</div>
                  <ProgressBar
                    pct={biggest ? (entry.total / biggest) * 100 : 0}
                    tone="rgba(255,255,255,0.5)"
                  />
                </div>
                <div className="oj-row-value">
                  {formatMoney(entry.total, currency)}
                </div>
              </div>
            ))}
          </ListGroup>
        )}
      </Section>
    </>
  );
}

export function BillsTab({
  currency,
  onChanged,
}: {
  currency: string;
  onChanged: () => void;
}) {
  const bills = useLoader(listBills);
  const accounts = useLoader(listAccounts);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState('');
  const [name, setName] = useState('');
  const [amount, setAmount] = useState('');
  const [dueOn, setDueOn] = useState(todayIso());
  const [recurrence, setRecurrence] = useState('monthly');
  const [error, setError] = useState('');

  const pending = (bills.data?.records ?? []).filter((b) => b.status !== 'paid');
  const paid = (bills.data?.records ?? []).filter((b) => b.status === 'paid');
  const defaultAccount = accounts.data?.records?.[0]?.id ?? '';

  async function handlePay(bill: Bill) {
    setBusy(bill.id);
    setError('');
    try {
      await payBill(bill.id, defaultAccount);
      bills.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao pagar');
    } finally {
      setBusy('');
    }
  }

  async function handleAdd() {
    const cents = parseMoney(amount);
    if (!name.trim() || cents <= 0) {
      setError('Informe nome e valor.');
      return;
    }
    setError('');
    try {
      await createRecord<Bill>('bills', {
        name: name.trim(),
        amount_cents: cents,
        due_on: dueOn,
        recurrence,
      });
      setAdding(false);
      setName('');
      setAmount('');
      bills.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (bills.loading) return <Spinner />;

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Nova conta
      </Button>

      <Section title="A pagar">
        {pending.length === 0 ? (
          <Empty>Nenhuma conta em aberto.</Empty>
        ) : (
          <ListGroup>
            {pending.map((bill) => (
              <Row
                key={bill.id}
                title={bill.name}
                sub={
                  (bill.status === 'overdue' ? 'Vencida em ' : 'Vence ') +
                  formatShortDate(bill.due_on)
                }
                value={formatMoney(bill.amount_cents, currency)}
                valueTone={bill.status === 'overdue' ? 'neg' : undefined}
                trailing={
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy === bill.id}
                    onClick={() => handlePay(bill)}
                  >
                    {busy === bill.id ? '…' : 'Pagar'}
                  </Button>
                }
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {paid.length > 0 && (
        <Section title="Pagas">
          <ListGroup>
            {paid.slice(0, 10).map((bill) => (
              <Row
                key={bill.id}
                title={bill.name}
                sub={`Pago em ${formatShortDate(bill.paid_on)}`}
                value={formatMoney(bill.amount_cents, currency)}
              />
            ))}
          </ListGroup>
        </Section>
      )}

      {adding && (
        <Sheet title="Nova conta" onClose={() => setAdding(false)}>
          <Field label="Nome" value={name} onChange={setName} placeholder="Luz" />
          <Field
            label="Valor"
            value={amount}
            onChange={setAmount}
            placeholder="180,00"
            type="text"
          />
          <Field label="Vencimento" value={dueOn} onChange={setDueOn} type="date" />
          <Field
            label="Repetição"
            value={recurrence}
            onChange={setRecurrence}
            options={[
              { value: 'none', label: 'Única' },
              { value: 'weekly', label: 'Semanal' },
              { value: 'monthly', label: 'Mensal' },
              { value: 'yearly', label: 'Anual' },
            ]}
          />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}

export function TransactionsTab({
  currency,
  onChanged,
}: {
  currency: string;
  onChanged: () => void;
}) {
  const transactions = useLoader(() =>
    listRecords<Transaction>('transactions', {
      order_by: 'occurred_on',
      limit: 100,
    }),
  );
  const accounts = useLoader(listAccounts);
  const [adding, setAdding] = useState(false);
  const [amount, setAmount] = useState('');
  const [category, setCategory] = useState('mercado');
  const [description, setDescription] = useState('');
  const [kind, setKind] = useState('expense');
  const [occurredOn, setOccurredOn] = useState(todayIso());
  const [error, setError] = useState('');

  async function handleAdd() {
    const cents = parseMoney(amount);
    if (cents <= 0) {
      setError('Informe um valor.');
      return;
    }
    setError('');
    try {
      await createRecord<Transaction>('transactions', {
        amount_cents: cents,
        kind,
        category,
        description: description.trim(),
        occurred_on: occurredOn,
        account_id: accounts.data?.records?.[0]?.id ?? '',
      });
      setAdding(false);
      setAmount('');
      setDescription('');
      transactions.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (transactions.loading) return <Spinner />;
  const records = transactions.data?.records ?? [];

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <Button onClick={() => setAdding(true)}>
        <Plus size={18} /> Novo lançamento
      </Button>

      <Section title="Lançamentos">
        {records.length === 0 ? (
          <Empty>Nada lançado ainda.</Empty>
        ) : (
          <ListGroup>
            {records.map((entry) => (
              <Row
                key={entry.id}
                title={entry.description || entry.category}
                sub={`${entry.category} · ${formatShortDate(entry.occurred_on)}`}
                value={
                  (entry.kind === 'income' ? '+' : '−') +
                  formatMoney(entry.amount_cents, currency)
                }
                valueTone={entry.kind === 'income' ? 'pos' : 'neg'}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {adding && (
        <Sheet title="Novo lançamento" onClose={() => setAdding(false)}>
          <Field
            label="Tipo"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'expense', label: 'Gasto' },
              { value: 'income', label: 'Entrada' },
            ]}
          />
          <Field label="Valor" value={amount} onChange={setAmount} placeholder="45,90" />
          <Field
            label="Categoria"
            value={category}
            onChange={setCategory}
            options={CATEGORY_OPTIONS}
          />
          <Field
            label="Descrição"
            value={description}
            onChange={setDescription}
            placeholder="Feira da semana"
          />
          <Field label="Data" value={occurredOn} onChange={setOccurredOn} type="date" />
          <Button onClick={handleAdd}>Salvar</Button>
        </Sheet>
      )}
    </>
  );
}

export function GoalsTab({ currency }: { currency: string }) {
  const goals = useLoader(() => listRecords<Goal>('goals', { limit: 50 }));
  const accounts = useLoader(listAccounts);

  if (goals.loading) return <Spinner />;
  const records = goals.data?.records ?? [];
  const accountRecords: Account[] = accounts.data?.records ?? [];

  return (
    <>
      <Section title="Contas">
        {accountRecords.length === 0 ? (
          <Empty>Nenhuma conta cadastrada.</Empty>
        ) : (
          <ListGroup>
            {accountRecords.map((account) => (
              <Row
                key={account.id}
                title={account.name}
                sub={account.institution || account.kind}
                value={formatMoney(account.balance_cents, currency)}
                valueTone={account.balance_cents < 0 ? 'neg' : undefined}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      <Section title="Metas">
        {records.length === 0 ? (
          <Empty>Nenhuma meta ainda.</Empty>
        ) : (
          <ListGroup>
            {records.map((goal) => (
              <div key={goal.id} className="oj-row">
                <div className="oj-row-body">
                  <div className="oj-row-title">{goal.name}</div>
                  <div className="oj-row-sub">
                    {formatMoney(goal.saved_cents, currency)} de{' '}
                    {formatMoney(goal.target_cents, currency)}
                  </div>
                  <ProgressBar
                    pct={
                      goal.target_cents
                        ? (goal.saved_cents / goal.target_cents) * 100
                        : 0
                    }
                    tone="var(--oj-ok)"
                  />
                </div>
              </div>
            ))}
          </ListGroup>
        )}
      </Section>
    </>
  );
}
