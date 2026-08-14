/**
 * Central de Conexões — o hub de integrações, com o estado verdadeiro.
 *
 * Regra número um desta tela: nunca prometer o que o backend não confirmou.
 * "Conectada" só aparece quando existe uma conexão concluída; iniciar o OAuth
 * mostra "Aguardando autorização" com expiração; provedor sem app configurado
 * no servidor diz isso com todas as letras — botão "Conectar" desabilitado
 * seria mentira, então ele simplesmente não existe nesses estados.
 *
 * A lógica de apresentação é pura e exportada (`presentProvider`,
 * `formatRelativeTime`, `connectionsHeadline`, `ledTone`) para os testes de
 * `ConnectionsApp.test.ts` cobrirem cada estado sem montar DOM.
 */

import {
  Activity,
  Briefcase,
  Calendar,
  Check,
  ChevronRight,
  HeartPulse,
  Landmark,
  Mail,
  MessageCircle,
  Plug,
  RefreshCw,
  Satellite,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { useEffect, useState } from 'react';
import {
  connectIntegration,
  disconnectIntegration,
  fetchIntegrations,
  fetchWhatsAppBriefing,
  fetchWhatsAppChannel,
  linkWhatsApp,
  revokeWhatsApp,
  saveWhatsAppBriefing,
  syncIntegration,
} from '../api';
import {
  clearCurrentAccountNativeCalendarReceiptCache,
  connectNativeCalendar,
} from '../nativeIntegrations';
import type {
  IntegrationProvider,
  IntegrationsSummary,
  WhatsAppBriefingPreference,
  WhatsAppBriefingSection,
} from '../types';
import { Button, Empty, ListGroup, Row, Section, Sheet, useLoader } from '../ui';

const ICONS: Record<string, ComponentType<{ size?: number }>> = {
  mail: Mail,
  calendar: Calendar,
  briefcase: Briefcase,
  activity: Activity,
  'heart-pulse': HeartPulse,
  'message-circle': MessageCircle,
  landmark: Landmark,
};

function iconFor(name: string): ComponentType<{ size?: number }> {
  return ICONS[name] ?? Plug;
}

// -- Apresentação pura (coberta por testes) ----------------------------------

export type StatusTone = 'ok' | 'warn' | 'critical' | 'muted' | 'accent';

export interface ProviderPresentation {
  /** Palavra do chip de status — sempre acompanhada do ponto colorido. */
  statusLabel: string;
  tone: StatusTone;
  cta: 'connect' | 'reconnect' | 'device' | 'none';
  ctaLabel: string;
  /** Sublinha do cartão; vazia = usar a descrição do provedor. */
  detail: string;
  /** Pertence à seção "Conectadas" (algo vivo ou em andamento). */
  active: boolean;
}

/** Tempo relativo curto em pt-BR ("há 5 min", "ontem"); '' para nulo. */
export function formatRelativeTime(iso: string | null, now?: Date): string {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const reference = now ?? new Date();
  const minutes = Math.max(
    0,
    Math.floor((reference.getTime() - then.getTime()) / 60000),
  );
  if (minutes < 2) return 'agora há pouco';
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'ontem';
  if (days < 7) return `há ${days} dias`;
  const [, month, day] = iso.slice(0, 10).split('-');
  return day && month ? `em ${day}/${month}` : '';
}

/**
 * Traduz as duas camadas de estado do backend num único rótulo honesto.
 *
 * A ordem importa: uma autorização em voo vence tudo (é o que o usuário
 * acabou de pedir), depois a conexão existente, e só então o catálogo.
 */
export function presentProvider(
  provider: IntegrationProvider,
  now?: Date,
): ProviderPresentation {
  const connection = provider.connection;
  const canConnect = provider.availability === 'available';

  if (provider.pending_auth) {
    return {
      statusLabel: 'Aguardando autorização',
      tone: 'warn',
      cta: 'none',
      ctaLabel: '',
      detail: 'Conclua a autorização no navegador.',
      active: true,
    };
  }

  if (connection && connection.status === 'connected') {
    if (connection.last_sync_status === 'error') {
      return {
        statusLabel: 'Conectada',
        tone: 'warn',
        cta: 'none',
        ctaLabel: '',
        detail: connection.last_error
          ? `Falha na última sincronização: ${connection.last_error}`
          : 'Falha na última sincronização.',
        active: true,
      };
    }
    const account = connection.account_label;
    const sync = connection.last_sync_at
      ? `sincronizada ${formatRelativeTime(connection.last_sync_at, now)}`
      : 'nunca sincronizada';
    return {
      statusLabel: 'Conectada',
      tone: 'ok',
      cta: 'none',
      ctaLabel: '',
      detail: account ? `${account} · ${sync}` : sync,
      active: true,
    };
  }

  if (connection && (connection.status === 'expired' || connection.status === 'error')) {
    return {
      statusLabel:
        connection.status === 'expired'
          ? 'Autorização vencida'
          : 'Erro de autorização',
      tone: 'critical',
      cta: canConnect ? 'reconnect' : 'none',
      ctaLabel: canConnect ? 'Reconectar' : '',
      detail: canConnect
        ? connection.last_error || 'Reconecte para voltar a sincronizar.'
        : 'Reconexão indisponível: o servidor perdeu a configuração deste app.',
      active: true,
    };
  }

  // Sem conexão viva (nunca houve, ou foi revogada): o catálogo manda.
  const revokedDetail =
    connection && connection.status === 'revoked'
      ? `Desconectada ${formatRelativeTime(connection.revoked_at, now)}`.trim()
      : '';
  switch (provider.availability) {
    case 'available':
      return {
        statusLabel: revokedDetail ? 'Desconectada' : 'Disponível',
        tone: 'accent',
        cta: 'connect',
        ctaLabel: revokedDetail ? 'Conectar de novo' : 'Conectar',
        detail: revokedDetail,
        active: false,
      };
    case 'needs_setup':
      return {
        statusLabel: 'Requer configuração',
        tone: 'muted',
        cta: 'none',
        ctaLabel: '',
        detail: 'O servidor ainda não tem o app deste provedor configurado.',
        active: false,
      };
    case 'device_only':
      if (provider.id === 'apple_calendar') {
        return {
          statusLabel: 'No iPhone',
          tone: 'accent',
          cta: 'device',
          ctaLabel: 'Autorizar calendário',
          detail: 'Disponível no app iOS; ainda não conectado neste aparelho.',
          active: false,
        };
      }
      return {
        statusLabel: 'Ainda não disponível',
        tone: 'muted',
        cta: 'none',
        ctaLabel: '',
        detail: 'A ponte nativa HealthKit ainda não existe neste build.',
        active: false,
      };
    default:
      return {
        statusLabel: 'Requer parceiro',
        tone: 'muted',
        cta: 'none',
        ctaLabel: '',
        detail:
          revokedDetail ||
          'Ativação depende de provedor homologado e configuração externa.',
        active: false,
      };
  }
}

/** Cor do LED de cada provedor no painel do Springboard. */
export function ledTone(
  provider: IntegrationProvider,
): 'ok' | 'warn' | 'critical' | 'off' {
  if (provider.pending_auth) return 'warn';
  const connection = provider.connection;
  if (!connection) return 'off';
  if (connection.status === 'connected') {
    return connection.last_sync_status === 'error' ? 'warn' : 'ok';
  }
  if (connection.status === 'error' || connection.status === 'expired') {
    return 'critical';
  }
  return 'off';
}

/**
 * Manchete do painel — sempre derivada do resumo real do backend.
 *
 * `availableCount` evita o convite mentiroso: sem nada conectado E sem nenhum
 * provedor conectável neste servidor, "Conecte Gmail..." seria uma promessa
 * que o toque seguinte desmentiria.
 */
export function connectionsHeadline(
  summary: IntegrationsSummary,
  availableCount?: number,
): string {
  const parts: string[] = [];
  if (summary.connected > 0) {
    parts.push(
      summary.connected === 1
        ? '1 conectada'
        : `${summary.connected} conectadas`,
    );
  }
  if (summary.attention > 0) {
    parts.push(
      summary.attention === 1
        ? '1 precisa de atenção'
        : `${summary.attention} precisam de atenção`,
    );
  }
  if (summary.pending > 0) {
    parts.push(
      summary.pending === 1
        ? '1 aguardando autorização'
        : `${summary.pending} aguardando autorização`,
    );
  }
  if (parts.length === 0) {
    return availableCount === 0
      ? 'Catálogo pronto — aguardando configuração do servidor'
      : 'Conecte Gmail, Agenda, Strava e mais';
  }
  return parts.join(' · ');
}

/** WhatsApp owns its revoke flow inside WhatsAppActivation. */
export function showGenericDisconnect(provider: IntegrationProvider): boolean {
  return (
    provider.id !== 'whatsapp' &&
    provider.connection !== null &&
    provider.connection.status !== 'revoked'
  );
}

export function canSyncProvider(provider: IntegrationProvider): boolean {
  return Boolean(
    provider.auth.kind === 'oauth' &&
      provider.connection?.status === 'connected' &&
      provider.connection.has_credential,
  );
}

export interface IntegrationCallbackState {
  provider: string;
  status: 'connected' | 'error';
}

/** Read only the bounded callback signal emitted by our own OAuth endpoint. */
export function parseIntegrationCallback(
  search: string,
): IntegrationCallbackState | null {
  const params = new URLSearchParams(search);
  const provider = params.get('integration') ?? '';
  const status = params.get('status') ?? '';
  if (!/^[a-z][a-z0-9_]{1,39}$/.test(provider)) return null;
  if (status !== 'connected' && status !== 'error') return null;
  return { provider, status };
}

export async function disconnectProvider(
  provider: IntegrationProvider,
  disconnect: typeof disconnectIntegration = disconnectIntegration,
  clearNativeReceipts: () => Promise<void> =
    clearCurrentAccountNativeCalendarReceiptCache,
): Promise<void> {
  await disconnect(provider.id);
  if (provider.id !== 'apple_calendar') return;
  try {
    await clearNativeReceipts();
  } catch {
    // The server revocation is authoritative. Receipts also self-expire, so a
    // local cleanup failure must not misreport the connection as still active.
  }
}

// -- Painel do Springboard ----------------------------------------------------

export function ConnectionsPanel({
  onOpen,
  refreshSignal,
}: {
  onOpen: () => void;
  /**
   * O Springboard fica montado atrás da janela de app, então este painel não
   * remonta ao voltar de uma mutação — qualquer valor que mude no retorno
   * (ex.: o objeto `today` re-buscado) serve de sinal para re-buscar aqui.
   */
  refreshSignal?: unknown;
}) {
  const overview = useLoader(fetchIntegrations, [refreshSignal]);
  const providers = overview.data?.providers ?? [];
  const summary = overview.data?.summary ?? null;
  const availableCount = providers.filter(
    (provider) => provider.availability === 'available',
  ).length;

  // Erro aqui não pode virar contagem falsa: o painel degrada para um
  // atalho neutro e a tela interna mostra o erro de verdade.
  const headline = overview.loading
    ? 'Verificando conexões…'
    : summary
      ? connectionsHeadline(summary, availableCount)
      : 'Abrir central';

  return (
    <section className="oj-specialist-panel" aria-label="Central de Conexões">
      <div className="oj-panel-heading">
        <span>
          <Satellite size={15} /> Central de Conexões
        </span>
        {providers.length > 0 && (
          <span className="oj-conn-leds" aria-hidden="true">
            {providers.map((provider) => (
              <span
                key={provider.id}
                className={`oj-conn-led oj-conn-led--${ledTone(provider)}`}
              />
            ))}
          </span>
        )}
      </div>
      <div className="oj-specialist-list oj-conn-panel-list">
        <button
          type="button"
          className="oj-specialist-row oj-conn-panel-row"
          onClick={onOpen}
        >
          <span className="oj-specialist-icon">
            <Plug size={17} />
          </span>
          <span className="oj-conn-panel-body">
            <strong>Contas e serviços</strong>
            <small className="oj-conn-panel-sub">{headline}</small>
          </span>
          <ChevronRight size={15} />
        </button>
      </div>
    </section>
  );
}

// -- Tela principal -----------------------------------------------------------

export function ConnectionsTab({ onChanged }: { onChanged?: () => void }) {
  const overview = useLoader(fetchIntegrations);
  const [selectedId, setSelectedId] = useState('');
  const [busy, setBusy] = useState('');
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  const providers = overview.data?.providers ?? [];
  const selected = providers.find((provider) => provider.id === selectedId) ?? null;

  useEffect(() => {
    const callback = parseIntegrationCallback(window.location.search);
    if (!callback) return;
    setSelectedId(callback.provider);
    if (callback.status === 'connected') {
      setNotice('Conexão autorizada. Sincronize agora para atualizar o Jarvis.');
    } else {
      setError('A autorização não foi concluída. Tente conectar novamente.');
    }
    const url = new URL(window.location.href);
    url.searchParams.delete('integration');
    url.searchParams.delete('status');
    window.history.replaceState(
      window.history.state,
      '',
      `${url.pathname}${url.search}${url.hash}`,
    );
  }, []);

  function closeSheet() {
    setSelectedId('');
    setConfirmingDisconnect(false);
  }

  async function handleConnect(provider: IntegrationProvider) {
    setBusy(provider.id);
    setBusyAction('connect');
    setError('');
    setNotice('');
    try {
      if (provider.auth.kind === 'device') {
        await connectNativeCalendar(provider.id);
        overview.reload();
        onChanged?.();
        setBusy('');
        setBusyAction('');
        return;
      }
      const intent = await connectIntegration(provider.id);
      // Redireciona para o consent oficial do provedor. "Conectada" só
      // aparece quando o backend confirmar a troca — na volta, o catálogo
      // é relido e mostra o estado que for verdade.
      window.location.assign(intent.authorize_url);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao iniciar conexão');
      setBusy('');
      setBusyAction('');
    }
  }

  async function handleSync(provider: IntegrationProvider) {
    setBusy(provider.id);
    setBusyAction('sync');
    setError('');
    setNotice('');
    try {
      const result = await syncIntegration(provider.id);
      setNotice(
        result.synced === 1
          ? '1 item atualizado no cérebro do Jarvis.'
          : `${result.synced} itens atualizados no cérebro do Jarvis.`,
      );
      overview.reload();
      onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao sincronizar');
    } finally {
      setBusy('');
      setBusyAction('');
    }
  }

  async function handleDisconnect(provider: IntegrationProvider) {
    setBusy(provider.id);
    setBusyAction('disconnect');
    setError('');
    setNotice('');
    try {
      await disconnectProvider(provider);
      closeSheet();
      overview.reload();
      onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Falha ao desconectar');
    } finally {
      setBusy('');
      setBusyAction('');
    }
  }

  if (overview.loading) {
    // Skeleton, não estado inventado: nada de "Disponível" piscando antes
    // da verdade chegar do servidor.
    return (
      <div className="oj-conn-skeletons" aria-hidden="true">
        <div className="oj-conn-skeleton" />
        <div className="oj-conn-skeleton" />
        <div className="oj-conn-skeleton" />
      </div>
    );
  }
  if (overview.error) return <div className="oj-error">{overview.error}</div>;

  const active = providers.filter((provider) => presentProvider(provider).active);
  const available = providers.filter(
    (provider) => !presentProvider(provider).active,
  );
  const summary = overview.data?.summary;

  return (
    <>
      {error && !selected && <div className="oj-error" role="alert">{error}</div>}

      {summary && (
        <div className="oj-card oj-conn-summary">
          <div className="oj-conn-leds oj-conn-leds--lg" aria-hidden="true">
            {providers.map((provider) => (
              <span
                key={provider.id}
                className={`oj-conn-led oj-conn-led--${ledTone(provider)}`}
              />
            ))}
          </div>
          <div className="oj-conn-summary-text">
            {connectionsHeadline(
              summary,
              providers.filter((p) => p.availability === 'available').length,
            )}
          </div>
        </div>
      )}

      {active.length > 0 && (
        <Section title="Suas conexões">
          <ListGroup>
            {active.map((provider) => (
              <ProviderRow
                key={provider.id}
                provider={provider}
                onOpen={() => setSelectedId(provider.id)}
              />
            ))}
          </ListGroup>
        </Section>
      )}

      <Section title="Catálogo">
        {available.length === 0 ? (
          <Empty>Todos os provedores do catálogo estão em uso.</Empty>
        ) : (
          <ListGroup>
            {available.map((provider) => (
              <ProviderRow
                key={provider.id}
                provider={provider}
                onOpen={() => setSelectedId(provider.id)}
              />
            ))}
          </ListGroup>
        )}
      </Section>

      {selected && (
        <Sheet title={selected.label} onClose={closeSheet}>
          <ProviderDetail
            provider={selected}
            busy={busy === selected.id}
            busyAction={busy === selected.id ? busyAction : ''}
            error={error}
            notice={notice}
            confirmingDisconnect={confirmingDisconnect}
            onConnect={() => handleConnect(selected)}
            onSync={() => handleSync(selected)}
            onDisconnect={() => handleDisconnect(selected)}
            onToggleConfirm={setConfirmingDisconnect}
          />
        </Sheet>
      )}
    </>
  );
}

function ProviderRow({
  provider,
  onOpen,
}: {
  provider: IntegrationProvider;
  onOpen: () => void;
}) {
  const view = presentProvider(provider);
  const Icon = iconFor(provider.icon);
  return (
    <Row
      title={provider.label}
      sub={view.detail || provider.description}
      leading={
        <span className={`oj-conn-icon oj-conn-icon--${provider.tint}`}>
          <Icon size={19} />
        </span>
      }
      trailing={
        <span className={`oj-conn-chip oj-conn-chip--${view.tone}`}>
          <span className="oj-conn-chip-dot" />
          {view.statusLabel}
        </span>
      }
      onClick={onOpen}
    />
  );
}

function ProviderDetail({
  provider,
  busy,
  busyAction,
  error,
  notice,
  confirmingDisconnect,
  onConnect,
  onSync,
  onDisconnect,
  onToggleConfirm,
}: {
  provider: IntegrationProvider;
  busy: boolean;
  busyAction: string;
  error: string;
  notice: string;
  confirmingDisconnect: boolean;
  onConnect: () => void;
  onSync: () => void;
  onDisconnect: () => void;
  onToggleConfirm: (value: boolean) => void;
}) {
  const view = presentProvider(provider);
  const connection = provider.connection;
  const Icon = iconFor(provider.icon);
  const liveConnection = connection && connection.status !== 'revoked';

  return (
    <div className="oj-conn-detail">
      <div className="oj-conn-head">
        <span className={`oj-conn-icon oj-conn-icon--${provider.tint}`}>
          <Icon size={22} />
        </span>
        <div className="oj-conn-head-body">
          <div className="oj-conn-head-title">{provider.label}</div>
          <div className="oj-conn-head-cat">{provider.category}</div>
        </div>
        <span className={`oj-conn-chip oj-conn-chip--${view.tone}`}>
          <span className="oj-conn-chip-dot" />
          {view.statusLabel}
        </span>
      </div>

      <p className="oj-conn-desc">{provider.description}</p>

      {error && <div className="oj-conn-error-note" role="alert">{error}</div>}
      {notice && <div className="oj-conn-success-note" role="status">{notice}</div>}

      <div className="oj-conn-block">
        <div className="oj-card-label">O que ele faz</div>
        <ul className="oj-conn-caps">
          {provider.capabilities.map((capability) => (
            <li key={capability}>
              <Check size={13} /> {capability}
            </li>
          ))}
        </ul>
      </div>

      {liveConnection && (
        <div className="oj-conn-block">
          <div className="oj-card-label">Sua conexão</div>
          <div className="oj-conn-facts">
            {connection.account_label && (
              <div>
                <span>Conta</span>
                <strong>{connection.account_label}</strong>
              </div>
            )}
            <div>
              <span>Conectada</span>
              <strong>
                {formatRelativeTime(connection.connected_at) || '—'}
              </strong>
            </div>
            <div>
              <span>Última sincronização</span>
              <strong>
                {connection.last_sync_at
                  ? formatRelativeTime(connection.last_sync_at)
                  : 'nunca'}
              </strong>
            </div>
          </div>
          {connection.last_error && (
            <div className="oj-conn-error-note">{connection.last_error}</div>
          )}
          {connection.granted_scopes.length > 0 && (
            <>
              <div className="oj-card-label">Acessos concedidos</div>
              <div className="oj-conn-scopes">
                {connection.granted_scopes.map((scope) => (
                  <code key={scope}>{scope}</code>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {provider.availability === 'needs_setup' && (
        <div className="oj-conn-block">
          <div className="oj-card-label">O que falta no servidor</div>
          <ul className="oj-conn-prereqs">
            {provider.prerequisites.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <div className="oj-conn-scopes">
            {provider.missing_config.map((name) => (
              <code key={name}>{name}</code>
            ))}
          </div>
        </div>
      )}

      {provider.availability === 'coming_soon' && (
        <div className="oj-conn-block">
          <div className="oj-card-label">O que falta para ativar</div>
          <ul className="oj-conn-prereqs">
            {provider.prerequisites.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {provider.id === 'whatsapp' && <WhatsAppActivation />}

      {canSyncProvider(provider) && (
        <div className="oj-conn-sync">
          <Button disabled={busy} onClick={onSync}>
            <RefreshCw
              size={17}
              className={busyAction === 'sync' ? 'is-spinning' : ''}
            />
            {busyAction === 'sync' ? 'Sincronizando…' : 'Sincronizar agora'}
          </Button>
          <p className="oj-conn-note">
            Atualiza o contexto usado pelo Jarvis sem importar corpos completos ou
            credenciais.
          </p>
        </div>
      )}

      {provider.availability === 'device_only' && (
        <div className="oj-conn-block">
          <div className="oj-card-label">Como conectar</div>
          <p className="oj-conn-note">
            {provider.id === 'apple_calendar'
              ? 'Toque em Autorizar calendário. O iPhone abre a tela oficial do sistema; o servidor registra apenas que esta instalação recebeu acesso, nunca uma credencial.'
              : 'A integração HealthKit ainda não foi implementada neste build. Nenhum dado de saúde será marcado como conectado antes de existir uma ponte nativa real.'}
          </p>
        </div>
      )}

      {provider.pending_auth ? (
        <>
          <p className="oj-conn-note">
            Aguardando você concluir a autorização no navegador. Se não
            concluir, ela expira sozinha em alguns minutos.
          </p>
          <Button variant="ghost" disabled={busy} onClick={onDisconnect}>
            Cancelar autorização
          </Button>
        </>
      ) : (
        view.cta !== 'none' && (
          <Button disabled={busy} onClick={onConnect}>
            {busy
              ? view.cta === 'device'
                ? 'Solicitando…'
                : 'Abrindo…'
              : view.ctaLabel}
          </Button>
        )
      )}

      {showGenericDisconnect(provider) && !provider.pending_auth && (
        <div className="oj-conn-danger-zone">
          {confirmingDisconnect ? (
            <>
              <p className="oj-conn-note">
                Os dados já importados permanecem no Jarvis. O acesso é
                revogado aqui; revogue também no painel do provedor se quiser
                garantia total.
              </p>
              <button
                type="button"
                className="oj-conn-danger"
                disabled={busy}
                onClick={onDisconnect}
              >
                {busy ? 'Desconectando…' : 'Confirmar desconexão'}
              </button>
              <Button variant="ghost" onClick={() => onToggleConfirm(false)}>
                Voltar
              </Button>
            </>
          ) : (
            <button
              type="button"
              className="oj-conn-danger oj-conn-danger--quiet"
              onClick={() => onToggleConfirm(true)}
            >
              Desconectar
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function WhatsAppActivation() {
  const channel = useLoader(fetchWhatsAppChannel);
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const status = channel.data?.status ?? 'disconnected';

  async function activate() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await linkWhatsApp(phone.trim());
      setNotice(
        'Código enviado pelo número oficial do Jarvis. Responda no WhatsApp somente com os seis dígitos.',
      );
      channel.reload();
    } catch (exc) {
      setError(
        exc instanceof Error
          ? exc.message
          : 'Não foi possível iniciar a ativação.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError('');
    try {
      await revokeWhatsApp();
      setNotice('WhatsApp desconectado desta conta Jarvis.');
      setPhone('');
      channel.reload();
    } catch (exc) {
      setError(
        exc instanceof Error ? exc.message : 'Não foi possível desconectar.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="oj-conn-block oj-wa-activation">
      <div className="oj-card-label">Ativação do piloto oficial</div>
      <div className={`oj-wa-status oj-wa-status--${status}`}>
        <span />
        {channel.loading
          ? 'Verificando vínculo…'
          : status === 'verified'
            ? 'WhatsApp ativo'
            : status === 'pending'
              ? 'Aguardando seu código'
              : 'Ainda não vinculado'}
      </div>

      <ol className="oj-conn-steps oj-wa-steps">
        <li>Digite seu número com país e DDD, por exemplo +5527999990001.</li>
        <li>O número oficial do Jarvis envia um código de seis dígitos.</li>
        <li>Responda no WhatsApp somente com esse código para confirmar.</li>
        <li>
          Depois disso, converse, use ações rápidas e receba briefings e treinos
          na mesma conversa.
        </li>
      </ol>

      {status === 'verified' ? (
        <>
          <p className="oj-conn-note">
            O Jarvis reconhece esta conta sem exibir nem guardar seu telefone
            em texto aberto no banco operacional.
          </p>
          <Button variant="ghost" disabled={busy} onClick={disconnect}>
            {busy ? 'Desconectando…' : 'Desconectar WhatsApp'}
          </Button>
          <WhatsAppBriefingSettings />
        </>
      ) : (
        <>
          <label className="oj-wa-field">
            <span>Número com código do país</span>
            <input
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="+5527999990001"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </label>
          <div className="oj-wa-actions">
            <Button disabled={busy || phone.trim().length < 9} onClick={activate}>
              {busy ? 'Enviando…' : 'Enviar código oficial'}
            </Button>
            {status === 'pending' && (
              <Button variant="ghost" disabled={busy} onClick={channel.reload}>
                Já respondi — verificar
              </Button>
            )}
          </div>
        </>
      )}

      {notice && <div className="oj-wa-notice">{notice}</div>}
      {(error || channel.error) && (
        <div className="oj-conn-error-note">{error || channel.error}</div>
      )}
    </div>
  );
}

const BRIEFING_SECTIONS: Array<{
  id: WhatsAppBriefingSection;
  label: string;
}> = [
  { id: 'priorities', label: 'Prioridades' },
  { id: 'finance', label: 'Finanças' },
  { id: 'fitness', label: 'Treino' },
  { id: 'routine', label: 'Rotina' },
  { id: 'family', label: 'Família' },
  { id: 'work', label: 'Trabalho' },
  { id: 'health', label: 'Saúde' },
  { id: 'news', label: 'Notícias' },
];

const BRIEFING_DAYS = [
  { id: 0, label: 'Seg' },
  { id: 1, label: 'Ter' },
  { id: 2, label: 'Qua' },
  { id: 3, label: 'Qui' },
  { id: 4, label: 'Sex' },
  { id: 5, label: 'Sáb' },
  { id: 6, label: 'Dom' },
];

function WhatsAppBriefingSettings() {
  const preference = useLoader(fetchWhatsAppBriefing);
  const [draft, setDraft] = useState<WhatsAppBriefingPreference | null>(null);
  const [topics, setTopics] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!preference.data) return;
    setDraft(preference.data);
    setTopics(preference.data.news_topics.join('\n'));
  }, [preference.data]);

  function toggleSection(section: WhatsAppBriefingSection) {
    if (!draft) return;
    const selected = draft.sections.includes(section);
    setDraft({
      ...draft,
      sections: selected
        ? draft.sections.filter((item) => item !== section)
        : [...draft.sections, section],
    });
  }

  function toggleDay(day: number) {
    if (!draft) return;
    const selected = draft.delivery_days.includes(day);
    const deliveryDays = selected
      ? draft.delivery_days.filter((item) => item !== day)
      : [...draft.delivery_days, day].sort();
    if (deliveryDays.length > 0) setDraft({ ...draft, delivery_days: deliveryDays });
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setNotice('');
    setError('');
    try {
      const saved = await saveWhatsAppBriefing({
        ...draft,
        news_topics: topics
          .split(/[\n,]/)
          .map((topic) => topic.trim())
          .filter(Boolean),
      });
      setDraft(saved);
      setTopics(saved.news_topics.join('\n'));
      setNotice(
        saved.enabled
          ? `Briefing programado para ${saved.time}.`
          : 'Briefing pausado. Suas preferências foram mantidas.',
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Não foi possível salvar.');
    } finally {
      setSaving(false);
    }
  }

  if (preference.loading || !draft) {
    return <div className="oj-wa-briefing-loading">Carregando briefing…</div>;
  }

  return (
    <div className="oj-wa-briefing">
      <div className="oj-wa-briefing-head">
        <div>
          <div className="oj-card-label">Briefing diário</div>
          <p>Você escolhe quando chega e exatamente o que entra.</p>
        </div>
        <label className="oj-wa-switch">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
          />
          <span aria-hidden="true" />
          <b>{draft.enabled ? 'Ativo' : 'Pausado'}</b>
        </label>
      </div>

      <label className="oj-wa-field oj-wa-time">
        <span>Horário no seu fuso</span>
        <input
          type="time"
          value={draft.time}
          onChange={(event) => setDraft({ ...draft, time: event.target.value })}
        />
      </label>

      <fieldset className="oj-wa-choice-group">
        <legend>Dias de envio</legend>
        <div className="oj-wa-days">
          {BRIEFING_DAYS.map((day) => (
            <button
              type="button"
              key={day.id}
              className={draft.delivery_days.includes(day.id) ? 'is-selected' : ''}
              aria-pressed={draft.delivery_days.includes(day.id)}
              onClick={() => toggleDay(day.id)}
            >
              {day.label}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="oj-wa-choice-group">
        <legend>Conteúdo</legend>
        <div className="oj-wa-section-grid">
          {BRIEFING_SECTIONS.map((section) => (
            <button
              type="button"
              key={section.id}
              className={draft.sections.includes(section.id) ? 'is-selected' : ''}
              aria-pressed={draft.sections.includes(section.id)}
              onClick={() => toggleSection(section.id)}
            >
              {draft.sections.includes(section.id) && <Check size={13} />}
              {section.label}
            </button>
          ))}
        </div>
      </fieldset>

      {draft.sections.includes('news') && (
        <div className="oj-wa-news-config">
          <label className="oj-wa-field">
            <span>Assuntos de notícias — um por linha</span>
            <textarea
              rows={4}
              maxLength={400}
              placeholder={'mobilidade\ncarros por assinatura\ntecnologia e IA'}
              value={topics}
              onChange={(event) => setTopics(event.target.value)}
            />
            <small>
              O Jarvis pesquisa no dia do envio e inclui as fontes usadas.
            </small>
          </label>

          <label className="oj-wa-field">
            <span>Preferências editoriais</span>
            <textarea
              rows={3}
              maxLength={500}
              placeholder="Ex.: seja direto, priorize o Brasil e destaque impactos financeiros."
              value={draft.custom_instructions}
              onChange={(event) =>
                setDraft({ ...draft, custom_instructions: event.target.value })
              }
            />
            <small>{draft.custom_instructions.length}/500</small>
          </label>
        </div>
      )}

      <Button
        disabled={saving || draft.sections.length === 0}
        onClick={save}
      >
        {saving ? 'Salvando…' : 'Salvar briefing'}
      </Button>
      {notice && <div className="oj-wa-notice">{notice}</div>}
      {(error || preference.error) && (
        <div className="oj-conn-error-note">{error || preference.error}</div>
      )}
    </div>
  );
}

// -- "Como funciona" ----------------------------------------------------------

export function ConnectionsAbout() {
  return (
    <>
      <div className="oj-card">
        <div className="oj-card-label">Privacidade primeiro</div>
        <p className="oj-conn-note">
          Conectar abre a tela oficial do provedor — o Jarvis nunca vê a sua
          senha. Credenciais não ficam no aplicativo nem no banco de dados em
          texto claro: apenas num cofre dedicado do servidor, e o app só
          enxerga o estado da conexão.
        </p>
      </div>

      <Section title="Como uma conexão funciona">
        <ol className="oj-conn-steps">
          <li>Você toca em Conectar e é levado ao site oficial do provedor.</li>
          <li>
            O provedor pergunta exatamente o que será compartilhado — os
            acessos pedidos são mínimos e de leitura sempre que possível.
          </li>
          <li>
            Só depois que o provedor confirma a autorização a conexão aparece
            como ativa. Autorização iniciada e não concluída expira sozinha em
            minutos.
          </li>
          <li>
            Desconectar revoga o acesso na hora e descarta a credencial do
            cofre. Os dados já importados permanecem — e podem ser apagados
            nos apps.
          </li>
        </ol>
      </Section>

      <Section title="O que nunca acontece">
        <ul className="oj-conn-prereqs">
          <li>Mostrar um serviço como conectado sem autorização concluída.</li>
          <li>Guardar tokens junto com os seus dados.</li>
          <li>Pedir mais acesso do que a função anunciada usa.</li>
          <li>
            Apple Health fora do iPhone: a permissão de saúde é do aparelho,
            e fica nele.
          </li>
        </ul>
      </Section>
    </>
  );
}
