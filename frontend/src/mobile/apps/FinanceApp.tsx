/** Finanças — saldo, contas a pagar, gastos e metas. */

import { Check, FileSearch, Plus, ScanLine, ShieldCheck, X } from 'lucide-react';
import { useState } from 'react';
import {
  analyzeFinancialDocument,
  cancelAction,
  confirmAction,
  createRecord,
  fetchFinanceSummary,
  listFinancialDocuments,
  listAccounts,
  listBills,
  listRecords,
  payBill,
  type FinancialDocumentAnalysisResult,
  type JarvisActionProposal,
} from '../api';
import type {
  Account,
  Bill,
  FinancialCandidate,
  Goal,
  Transaction,
} from '../types';
import {
  buildStrongAuthProof,
  proposalRequiresStrongAuth,
  runFinanceMutation,
} from '../nativeIntegrations';
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
  Stat,
  todayIso,
  useLoader,
} from '../ui';
import { SkeletonRows, SkeletonScreen } from '../Skeleton';

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

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export function FinancialCandidateList({
  candidates,
  currency,
}: {
  candidates: FinancialCandidate[];
  currency: string;
}) {
  return (
    <div className="oj-finance-candidates">
      {candidates.map((candidate, index) => (
        <div className="oj-finance-candidate" key={`${candidate.occurred_on}-${index}`}>
          <div className="oj-row-body">
            <div className="oj-row-title">{candidate.description || candidate.category}</div>
            <div className="oj-row-sub">
              {candidate.category} · {formatShortDate(candidate.occurred_on)} ·{' '}
              {Math.round(candidate.confidence * 100)}% de confiança
            </div>
            <small>Aguardando revisão antes de entrar no financeiro.</small>
          </div>
          <div className={candidate.kind === 'income' ? 'oj-pos' : 'oj-neg'}>
            {candidate.kind === 'income' ? '+' : '−'}
            {formatMoney(candidate.amount_cents, currency)}
          </div>
        </div>
      ))}
    </div>
  );
}

export function FinancialDocumentsTab({
  currency,
  onChanged,
}: {
  currency: string;
  onChanged: () => void;
}) {
  const documents = useLoader(listFinancialDocuments);
  const [analysis, setAnalysis] = useState<FinancialDocumentAnalysisResult | null>(
    null,
  );
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function analyze(file: File) {
    if (file.size > 10 * 1024 * 1024) {
      setError('O arquivo deve ter no máximo 10 MB.');
      return;
    }
    setBusy('analyze');
    setError('');
    try {
      const dataBase64 = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
      const result = await analyzeFinancialDocument({
        filename: file.name,
        contentType: file.type || 'application/octet-stream',
        dataBase64,
      });
      setAnalysis(result);
      documents.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao analisar documento');
    } finally {
      setBusy('');
    }
  }

  function replaceProposal(updated: JarvisActionProposal) {
    setAnalysis((current) =>
      current
        ? {
            ...current,
            proposals: current.proposals.map((proposal) =>
              proposal.id === updated.id ? updated : proposal,
            ),
          }
        : current,
    );
  }

  async function confirm(proposal: JarvisActionProposal) {
    setBusy(proposal.id);
    setError('');
    try {
      const proof = proposalRequiresStrongAuth(proposal)
        ? await buildStrongAuthProof('finance', proposal.id, 'explicit', true)
        : undefined;
      const result = await confirmAction(proposal.id, 'explicit', proof);
      replaceProposal(result.proposal);
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao confirmar lançamento');
    } finally {
      setBusy('');
    }
  }

  async function reject(proposal: JarvisActionProposal) {
    setBusy(proposal.id);
    setError('');
    try {
      const result = await cancelAction(proposal.id);
      replaceProposal(result.proposal);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao rejeitar lançamento');
    } finally {
      setBusy('');
    }
  }

  return (
    <>
      {error && <div className="oj-error">{error}</div>}
      <div className="oj-finance-document-hero">
        <div className="oj-finance-document-icon"><ScanLine size={26} /></div>
        <div>
          <div className="oj-card-label">DIRETOR FINANCEIRO IA</div>
          <h3>Comprovantes e extratos</h3>
          <p>Envie foto, PDF, CSV ou OFX. O Jarvis identifica e pede sua confirmação antes de lançar.</p>
        </div>
        <label className={`oj-btn ${busy === 'analyze' ? 'is-disabled' : ''}`}>
          <FileSearch size={18} /> {busy === 'analyze' ? 'Analisando…' : 'Escolher arquivo'}
          <input
            hidden
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf,text/csv,.csv,.ofx,.qfx"
            disabled={busy === 'analyze'}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void analyze(file);
              event.currentTarget.value = '';
            }}
          />
        </label>
        <div className="oj-finance-privacy">
          <ShieldCheck size={16} /> Imagens e PDFs são processados pela IA. O arquivo
          bruto não é salvo; ficam apenas resumo, hash e propostas auditáveis.
        </div>
      </div>

      {analysis && (
        <Section title={analysis.replayed ? 'Análise recuperada' : 'Revise antes de lançar'}>
          <FinancialCandidateList
            candidates={analysis.document.analysis.candidates}
            currency={currency}
          />
          <div className="oj-finance-proposal-actions">
            {analysis.proposals.map((proposal, index) => (
              <div className="oj-finance-proposal" key={proposal.id}>
                <span>{proposal.summary}</span>
                {proposal.status === 'pending' ? (
                  <div>
                    <button type="button" aria-label={`Rejeitar item ${index + 1}`} disabled={busy === proposal.id} onClick={() => void reject(proposal)}><X size={17} /></button>
                    <button type="button" aria-label={`Confirmar item ${index + 1}`} disabled={busy === proposal.id} onClick={() => void confirm(proposal)}><Check size={17} /></button>
                  </div>
                ) : (
                  <strong>{proposal.status === 'confirmed' ? 'Lançado' : 'Rejeitado'}</strong>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Documentos recentes">
        {documents.loading ? (
          <SkeletonRows count={2} />
        ) : documents.error ? (
          // Sem isto uma falha de rede cai no ramo de lista vazia e diz ao
          // cliente que ele nunca enviou documento nenhum.
          <div className="oj-error">{documents.error}</div>
        ) : (documents.data?.documents.length ?? 0) === 0 ? (
          <Empty>Nenhum documento analisado.</Empty>
        ) : (
          <ListGroup>
            {documents.data?.documents.map((document) => (
              <Row
                key={document.id}
                title={document.filename}
                sub={`${document.analysis.candidates.length} item(ns) · ${document.document_kind === 'statement' ? 'extrato' : 'comprovante'}`}
                value="Revisado"
              />
            ))}
          </ListGroup>
        )}
      </Section>
    </>
  );
}

export function FinanceOverview({ currency }: { currency: string }) {
  const { data, error, loading } = useLoader(fetchFinanceSummary);

  if (loading) return <SkeletonScreen />;
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
    // Sem a conta bancária resolvida o backend marca a conta como paga e lança
    // a despesa, mas `service.add_transaction` só mexe no saldo `if account_id`
    // — a conta sumiria da lista com o saldo intacto. Errar aqui é pior que
    // recusar, porque o usuário acredita no número que sobra na tela.
    if (accounts.error) {
      setError(
        'Não deu para carregar suas contas. Recarregue antes de pagar — assim o saldo não seria debitado.',
      );
      return;
    }
    setBusy(bill.id);
    setError('');
    try {
      await runFinanceMutation((operationId, approval) =>
        payBill(bill.id, defaultAccount, approval, operationId),
      );
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
      await runFinanceMutation((operationId, approval) =>
        createRecord<Bill>(
          'bills',
          {
            name: name.trim(),
            amount_cents: cents,
            due_on: dueOn,
            recurrence,
          },
          approval,
          operationId,
        ),
      );
      setAdding(false);
      setName('');
      setAmount('');
      bills.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  // Esperar as contas também: a tela usa `defaultAccount` para debitar o saldo,
  // e renderizar antes de o loader resolver deixa uma janela em que "Pagar"
  // funciona pela metade.
  if (bills.loading || accounts.loading) return <SkeletonScreen />;
  if (bills.error || accounts.error) return <div className="oj-error">{bills.error || accounts.error}</div>;

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
    // Mesmo motivo do pagamento de contas: sem conta resolvida o lançamento
    // entra no extrato e no "saiu no mês", mas não sai do saldo.
    if (accounts.error) {
      setError(
        'Não deu para carregar suas contas. Recarregue antes de lançar — assim o saldo não seria atualizado.',
      );
      return;
    }
    const cents = parseMoney(amount);
    if (cents <= 0) {
      setError('Informe um valor.');
      return;
    }
    setError('');
    try {
      await runFinanceMutation((operationId, approval) =>
        createRecord<Transaction>(
          'transactions',
          {
            amount_cents: cents,
            kind,
            category,
            description: description.trim(),
            occurred_on: occurredOn,
            account_id: accounts.data?.records?.[0]?.id ?? '',
          },
          approval,
          operationId,
        ),
      );
      setAdding(false);
      setAmount('');
      setDescription('');
      transactions.reload();
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao salvar');
    }
  }

  if (transactions.loading || accounts.loading) return <SkeletonScreen />;
  if (transactions.error || accounts.error) return <div className="oj-error">{transactions.error || accounts.error}</div>;
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

  if (goals.loading) return <SkeletonScreen />;
  if (goals.error || accounts.error) return <div className="oj-error">{goals.error || accounts.error}</div>;
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
