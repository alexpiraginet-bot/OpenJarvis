/**
 * The Jarvis command surface, embedded beside the orbital core on Home.
 *
 * A live HUD that reacts to the voice in the room: concentric rings driven by
 * the microphone's frequency spectrum, a core that pulses with loudness, and
 * spoken answers grounded in the client's own life data.
 *
 * Everything animated is drawn on one canvas and reads its inputs from refs.
 * React renders this component only when the *status* or the *text* changes —
 * a handful of times per conversation — while the visuals run at 60fps
 * independently. Driving the rings through component state instead would
 * re-render the whole shell on every frame.
 */

import { Check, Mic, MicOff, Send, X } from 'lucide-react';
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ask,
  cancelAction,
  confirmAction,
  fetchIntegrations,
  listPendingActions,
  type ConfirmationMethod,
  type DialogueMessage,
  type JarvisActionProposal,
} from './api';
import {
  buildStrongAuthProof,
  executeNativeCalendarProposal,
  proposalRequiresStrongAuth,
  requestNativeCalendarContext,
} from './nativeIntegrations';
import { DialogueSessionController } from './dialogueSession';
import { JarvisPresence } from './JarvisPresence';
import {
  deriveJarvisPresenceState,
  getJarvisPresenceCopy,
  presenceNeedsProgress,
  type JarvisPresenceState,
} from './jarvisPresenceState';
import type { IntegrationConnection, ShellAppId, Today } from './types';
import { useVoice, type VoiceState, type VoiceStatus } from './useVoice';

const CONTEXT_LABELS: Record<ShellAppId, string> = {
  finance: 'Finanças',
  fitness: 'Treino',
  routine: 'Rotina',
  family: 'Família',
  work: 'Trabalho',
  health: 'Saúde',
  connections: 'Conexões',
};

const CONTEXT_HINTS: Record<ShellAppId, string> = {
  finance: '“Quanto gastei este mês?” ou “O que vence hoje?”',
  fitness: '“Monte meu treino desta semana” ou “Como está minha evolução?”',
  routine: '“Organize meu dia” ou “Crie uma rotina para a manhã”',
  family: '“O que a família tem hoje?” ou “Lembre o aniversário da Ana”',
  work: '“Priorize minhas tarefas” ou “Prepare meu briefing de amanhã”',
  health: '“Registre 350 ml de água” ou “Organize meus exames”',
  connections: '“Conecte meu calendário” ou “Quais integrações estão ativas?”',
};

export function corePixelAlpha(
  red: number,
  green: number,
  blue: number,
  originalAlpha = 255,
): number {
  const luminance = Math.max(red, green, blue);
  const extracted = Math.max(0, Math.min(255, Math.round((luminance - 8) * 6)));
  return Math.min(originalAlpha, extracted);
}

export function deriveJarvisCorePresence({
  voiceStatus,
  resolving = false,
  submitting = false,
  dialogueStatus = '',
  success = false,
  error = false,
}: {
  voiceStatus: VoiceStatus;
  resolving?: boolean;
  submitting?: boolean;
  dialogueStatus?: string;
  success?: boolean;
  error?: boolean;
}): JarvisPresenceState {
  return deriveJarvisPresenceState({
    error:
      error ||
      voiceStatus === 'denied' ||
      dialogueStatus.toLocaleLowerCase('pt-BR').startsWith('falha'),
    executing: resolving,
    speaking: voiceStatus === 'speaking',
    listening: voiceStatus === 'listening',
    thinking: submitting || voiceStatus === 'thinking',
    profileBuilding: /sincronizando|restaurando|preparando/i.test(
      dialogueStatus,
    ),
    success,
  });
}

export function confirmedProposalSucceeded(
  proposal: JarvisActionProposal,
): boolean {
  return proposal.status === 'confirmed' && proposal.result?.success !== false;
}

export type VoiceProposalDecision = 'confirm' | 'cancel' | null;

function normaliseVoiceCommand(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('pt-BR')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Recognise only short, explicit approval phrases while an action is pending.
 * Longer requests still go to Jarvis, so a sentence such as "registre uma
 * despesa" can never accidentally approve an older proposal.
 */
export function voiceProposalDecision(text: string): VoiceProposalDecision {
  const normalised = normaliseVoiceCommand(text);

  const confirmations = new Set([
    'sim',
    'confirmar',
    'confirma',
    'confirme',
    'confirmo',
    'sim confirmar',
    'sim confirma',
    'sim confirme',
    'pode confirmar',
    'pode confirmar sim',
  ]);
  const cancellations = new Set([
    'nao',
    'cancelar',
    'cancela',
    'cancele',
    'nao confirmar',
    'nao confirma',
    'nao confirme',
    'pode cancelar',
  ]);

  if (confirmations.has(normalised)) return 'confirm';
  if (cancellations.has(normalised)) return 'cancel';
  if (/^(confirmar|confirma|confirme) (a |o )?(primeira|primeiro|segunda|segundo|terceira|terceiro|ultima|ultimo|1|2|3)$/.test(normalised)) {
    return 'confirm';
  }
  if (/^(cancelar|cancela|cancele) (a |o )?(primeira|primeiro|segunda|segundo|terceira|terceiro|ultima|ultimo|1|2|3)$/.test(normalised)) {
    return 'cancel';
  }
  return null;
}

export function voiceProposalIndex(text: string, count: number): number | null {
  const normalised = normaliseVoiceCommand(text);
  if (/\b(ultima|ultimo)\b/.test(normalised)) return count > 0 ? count - 1 : null;
  if (/\b(primeira|primeiro|1)\b/.test(normalised)) return count >= 1 ? 0 : null;
  if (/\b(segunda|segundo|2)\b/.test(normalised)) return count >= 2 ? 1 : null;
  if (/\b(terceira|terceiro|3)\b/.test(normalised)) return count >= 3 ? 2 : null;
  return null;
}

export async function askWithNativeCalendarContext(
  question: string,
  askQuestion: typeof ask = ask,
  readContext: typeof requestNativeCalendarContext = requestNativeCalendarContext,
  connectionIsActive: () => Promise<boolean> = nativeCalendarConnectionIsActive,
) {
  let deviceContext: Awaited<ReturnType<typeof requestNativeCalendarContext>> | undefined;
  try {
    if (await connectionIsActive()) {
      deviceContext = await readContext();
    }
  } catch {
    // Connection lookup and the device bridge both fail closed. Asking Jarvis
    // remains available, but EventKit is never read without a live server grant.
  }
  return askQuestion(question, deviceContext);
}

export async function nativeCalendarConnectionIsActive(): Promise<boolean> {
  const overview = await fetchIntegrations();
  const calendar = overview.providers.find(
    (provider) => provider.id === 'apple_calendar',
  );
  return calendarConnectionAllowsContext(calendar?.connection ?? null);
}

export function calendarConnectionAllowsContext(
  connection: IntegrationConnection | null,
): boolean {
  return (
    connection?.status === 'connected' &&
    connection.granted_scopes.includes('events.read')
  );
}

function confirmationFailureMessage(proposal: JarvisActionProposal): string {
  const detail = proposal.result?.error ?? proposal.result?.detail;
  return typeof detail === 'string' && detail.trim()
    ? detail
    : 'A ação não foi concluída. Revise os dados e tente novamente.';
}

export function JarvisCore({
  today,
  userId,
  contextApp = null,
  variant = 'standalone',
  onClose,
  onRefresh,
}: {
  today: Today | null;
  userId: string;
  contextApp?: ShellAppId | null;
  variant?: 'standalone' | 'embedded';
  onClose: () => void;
  onRefresh: () => void;
}) {
  const embedded = variant === 'embedded';
  const dialogue = useMemo(
    () => new DialogueSessionController(userId),
    [userId],
  );
  const [answer, setAnswer] = useState('');
  const [history, setHistory] = useState<DialogueMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [dialogueStatus, setDialogueStatus] = useState('Pronto para conversar');
  const [proposals, setProposals] = useState<JarvisActionProposal[]>([]);
  const [resolving, setResolving] = useState('');
  const [presenceSuccess, setPresenceSuccess] = useState(false);
  const presenceSuccessTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  // The speech handler is created *before* `useVoice` returns, so it reaches
  // the controls through a ref rather than closing over a binding that does
  // not exist yet. `busy` is a ref too: recognition fires from outside React,
  // where a captured state value would already be stale.
  const voiceRef = useRef<VoiceState | null>(null);
  const busyRef = useRef(false);
  const queuedQuestionsRef = useRef<string[]>([]);
  const handleQuestionRef = useRef<(question: string) => Promise<void>>(
    async () => undefined,
  );
  const proposalsRef = useRef<JarvisActionProposal[]>([]);
  const resolvingRef = useRef('');

  const interruptPresenceSuccess = useCallback(() => {
    if (presenceSuccessTimerRef.current) {
      clearTimeout(presenceSuccessTimerRef.current);
      presenceSuccessTimerRef.current = null;
    }
    setPresenceSuccess(false);
  }, []);

  const triggerPresenceSuccess = useCallback(() => {
    interruptPresenceSuccess();
    setPresenceSuccess(true);
    presenceSuccessTimerRef.current = setTimeout(() => {
      presenceSuccessTimerRef.current = null;
      setPresenceSuccess(false);
    }, 1800);
  }, [interruptPresenceSuccess]);

  useEffect(
    () => () => {
      if (presenceSuccessTimerRef.current) {
        clearTimeout(presenceSuccessTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    proposalsRef.current = proposals;
  }, [proposals]);

  useEffect(() => {
    let active = true;
    interruptPresenceSuccess();
    if (contextApp) {
      dialogue.beginSpecialistContext(contextApp);
      setAnswer('');
      setHistory([]);
      setDialogueStatus(`Especialista de ${CONTEXT_LABELS[contextApp]} ativo`);
      return () => {
        active = false;
      };
    }

    setDialogueStatus('Sincronizando contexto');
    void dialogue.restore().then((snapshot) => {
      if (!active) return;
      const previousAnswer = [...snapshot.history]
        .reverse()
        .find((message) => message.role === 'assistant')?.content;
      if (previousAnswer) setAnswer(previousAnswer);
      setHistory(snapshot.history);
      setDialogueStatus(
        snapshot.conversationId ? 'Contexto restaurado' : 'Pronto para conversar',
      );
      if (snapshot.conversationId) triggerPresenceSuccess();
    }).catch(() => {
      if (!active) return;
      setDialogueStatus('Falha ao restaurar contexto');
    });
    return () => {
      active = false;
    };
  }, [contextApp, dialogue, interruptPresenceSuccess, triggerPresenceSuccess]);

  const resolveProposal = useCallback(
    async (
      proposal: JarvisActionProposal,
      approved: boolean,
      confirmationMethod: ConfirmationMethod = 'explicit',
    ) => {
      if (resolvingRef.current) return;
      interruptPresenceSuccess();
      resolvingRef.current = proposal.id;
      setResolving(proposal.id);
      setDialogueStatus(
        approved ? 'Executando ação confirmada' : 'Cancelando ação',
      );
      try {
        if (approved) {
          const result =
            proposal.tool_name === 'calendar_create'
              ? await executeNativeCalendarProposal(proposal, confirmationMethod)
              : await confirmAction(
                  proposal.id,
                  confirmationMethod,
                  proposalRequiresStrongAuth(proposal)
                    ? await buildStrongAuthProof(
                        'finance',
                        proposal.id,
                        confirmationMethod,
                        true,
                      )
                    : undefined,
                );
          if (!confirmedProposalSucceeded(result.proposal)) {
            throw new Error(confirmationFailureMessage(result.proposal));
          }
          setAnswer('Ação confirmada e registrada.');
          setDialogueStatus('Ação concluída');
          voiceRef.current?.speak('Ação confirmada e registrada.');
          onRefresh();
        } else {
          await cancelAction(proposal.id);
          setAnswer('Ação cancelada.');
          setDialogueStatus('Ação cancelada');
          voiceRef.current?.speak('Ação cancelada.');
        }
        triggerPresenceSuccess();
        setProposals((current) => {
          const remaining = current.filter((item) => item.id !== proposal.id);
          proposalsRef.current = remaining;
          return remaining;
        });
      } catch (exc) {
        const message =
          exc instanceof Error ? exc.message : 'Não consegui concluir a ação.';
        setAnswer(message);
        setDialogueStatus('Falha ao executar ação');
        voiceRef.current?.speak(message);
      } finally {
        resolvingRef.current = '';
        setResolving('');
      }
    },
    [interruptPresenceSuccess, onRefresh, triggerPresenceSuccess],
  );

  const handleQuestion = useCallback(
    async (question: string) => {
      const controls = voiceRef.current;
      if (!controls) return;
      if (busyRef.current) {
        const queue = queuedQuestionsRef.current;
        if (queue[queue.length - 1] !== question) queue.push(question);
        if (queue.length > 3) queue.shift();
        return;
      }

      const decision = voiceProposalDecision(question);
      const pending = proposalsRef.current;
      if (decision && pending.length > 1) {
        const selectedIndex = voiceProposalIndex(question, pending.length);
        if (selectedIndex === null) {
          const message =
            'Há mais de uma ação pendente. Diga confirmar primeira, segunda, terceira ou última.';
          setAnswer(message);
          controls.speak(message);
          return;
        }
        await resolveProposal(
          pending[selectedIndex],
          decision === 'confirm',
          'voice_explicit',
        );
        return;
      }
      if (decision && pending.length === 1) {
        await resolveProposal(
          pending[0],
          decision === 'confirm',
          'voice_explicit',
        );
        return;
      }

      busyRef.current = true;
      interruptPresenceSuccess();
      setSubmitting(true);
      controls.setStatus('thinking');
      setDialogueStatus('Processando sua solicitação');
      setHistory((current) => [
        ...current,
        { role: 'user' as const, content: question },
      ].slice(-20));
      try {
        const result = await askWithNativeCalendarContext(
          question,
          (message, deviceContext) => dialogue.submit(message, deviceContext),
        );
        setAnswer(result.answer);
        setHistory(result.history ?? dialogue.current().history);
        const nextProposals = result.proposals ?? [];
        proposalsRef.current = nextProposals;
        setProposals(nextProposals);
        controls.speak(result.answer);
        setDialogueStatus('Resposta pronta');
        triggerPresenceSuccess();
        // A spoken exchange may have changed the data behind the badges.
        onRefresh();
      } catch (exc) {
        const message =
          exc instanceof Error ? exc.message : 'Não consegui responder agora.';
        setAnswer(message);
        controls.speak(message);
        interruptPresenceSuccess();
        setDialogueStatus('Falha ao responder');
      } finally {
        busyRef.current = false;
        setSubmitting(false);
        const queued = queuedQuestionsRef.current.shift();
        if (queued) {
          queueMicrotask(() => void handleQuestionRef.current(queued));
        }
      }
    },
    [
      dialogue,
      interruptPresenceSuccess,
      onRefresh,
      resolveProposal,
      triggerPresenceSuccess,
    ],
  );
  handleQuestionRef.current = handleQuestion;

  const voice = useVoice(handleQuestion);
  voiceRef.current = voice;

  useEffect(() => {
    let active = true;
    void listPendingActions()
      .then((result) => {
        if (active) setProposals(result.proposals);
      })
      .catch(() => {
        // The HUD remains usable when an older backend lacks this endpoint.
      });
    return () => {
      active = false;
    };
  }, []);

  const presenceState = deriveJarvisCorePresence({
    voiceStatus: voice.status,
    resolving: Boolean(resolving),
    submitting,
    dialogueStatus,
    success: presenceSuccess,
    error: Boolean(voice.error),
  });
  const presenceCopy = getJarvisPresenceCopy(presenceState);
  const listening = voice.status === 'listening';
  const spoken = voice.interim || voice.transcript;

  const submitDraft = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = draft.trim();
    if (!question || submitting) return;
    setDraft('');
    void handleQuestion(question);
  };

  return (
    <div
      className={`oj-jarvis${embedded ? ' oj-jarvis--embedded' : ''}`}
      role={embedded ? undefined : 'dialog'}
      aria-modal={embedded ? undefined : true}
      aria-label={embedded ? 'Comandos do Jarvis' : 'Painel do Jarvis'}
    >
      {embedded ? (
        <div className="oj-embedded-command-head">
          <div className="oj-embedded-command-state">
            <JarvisPresence
              state={presenceState}
              variant="inline"
              levelRef={voice.levelRef}
              spectrumRef={voice.spectrumRef}
            />
            <strong>JARVIS</strong>
          </div>
          <button
            type="button"
            className="oj-hud-btn"
            onClick={onClose}
            aria-label="Recolher comandos do Jarvis"
          >
            <X size={18} />
          </button>
        </div>
      ) : (
        <header className="oj-hud-top">
          <div className="oj-hud-brand">
            <strong>JARVIS LIFE</strong>
            <span>NEURAL CORE / VOICE OS</span>
          </div>
          <div className="oj-hud-readout" aria-live="polite">
            <span
              className="oj-hud-dot"
              style={{ background: presenceCopy.color }}
            />
            {presenceCopy.label}
          </div>
          <button
            type="button"
            className="oj-hud-btn"
            onClick={onClose}
            aria-label="Fechar painel do Jarvis"
            autoFocus
          >
            <X size={20} />
          </button>
        </header>
      )}

      {contextApp && (
        <div className="oj-hud-context">Contexto: {CONTEXT_LABELS[contextApp]}</div>
      )}

      {!embedded && (
        <div className="oj-hud-stage">
          <div className="oj-hud-telemetry" aria-hidden="true">
            <span>CORE SYNC<br /><strong>100%</strong></span>
            <span>AGENTS<br /><strong>06 ONLINE</strong></span>
            <span>VOICE LINK<br /><strong>{presenceCopy.label}</strong></span>
          </div>
          <JarvisPresence
            state={presenceState}
            variant="hero"
            levelRef={voice.levelRef}
            spectrumRef={voice.spectrumRef}
          />
          <button
            type="button"
            className="oj-hud-hit"
            onClick={() => (listening ? voice.stop() : voice.start())}
            aria-label={listening ? 'Parar de ouvir' : 'Começar a ouvir'}
          />
          <span className="oj-hud-swipe" aria-hidden="true">
            toque no núcleo · voz
          </span>
        </div>
      )}

      <div
        className="oj-hud-text"
        role="status"
        aria-live="polite"
        aria-atomic="false"
        aria-busy={presenceNeedsProgress(presenceState)}
      >
        <span className="oj-visually-hidden">{dialogueStatus}</span>
        <div className="oj-hud-history" aria-label="Histórico da conversa">
          {history.slice(-6).map((message, index) => (
            <p
              key={`${message.role}-${index}-${message.content.slice(0, 24)}`}
              data-role={message.role}
            >
              <span>{message.role === 'user' ? 'Você' : 'Jarvis'}</span>
              {message.content}
            </p>
          ))}
        </div>
        {voice.error && <p className="oj-hud-error">{voice.error}</p>}
        {spoken && <p className="oj-hud-said">“{spoken}”</p>}
        {answer && history[history.length - 1]?.content !== answer && (
          <p className="oj-hud-answer">{answer}</p>
        )}
        {proposals.map((proposal) => (
          <section className="oj-action-card" key={proposal.id} aria-live="polite">
            <span className="oj-action-eyebrow">Confirmação necessária</span>
            <strong>{proposal.summary}</strong>
            <div className="oj-action-controls">
              <button
                type="button"
                className="oj-action-btn oj-action-btn--cancel"
                disabled={Boolean(resolving)}
                onClick={() => void resolveProposal(proposal, false)}
              >
                <X size={16} /> Cancelar
              </button>
              <button
                type="button"
                className="oj-action-btn oj-action-btn--confirm"
                disabled={Boolean(resolving)}
                onClick={() => void resolveProposal(proposal, true)}
              >
                <Check size={16} />
                {resolving === proposal.id ? 'Confirmando…' : 'Confirmar'}
              </button>
            </div>
          </section>
        ))}
        {!spoken && !answer && !voice.error && (
          <p className="oj-hud-hint">
            {voice.supported
              ? `Fale comigo. ${
                  contextApp
                    ? CONTEXT_HINTS[contextApp]
                    : '“Quanto gastei este mês?” ou “O que vence hoje?”'
                }`
              : 'Use o campo abaixo para conversar com o Jarvis.'}
          </p>
        )}
      </div>

      <form
        className="oj-hud-composer"
        aria-label="Conversar por texto com o Jarvis"
        onSubmit={submitDraft}
      >
        <input
          type="text"
          name="jarvis-message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Digite um comando para o Jarvis"
          aria-label="Mensagem para o Jarvis"
          autoComplete="off"
        />
        <button
          type="submit"
          disabled={submitting || !draft.trim()}
          aria-label="Enviar mensagem"
        >
          <Send size={18} />
        </button>
      </form>

      <footer className="oj-hud-bottom">
        <div className="oj-hud-specialists" aria-label="Especialistas disponíveis">
          <span>FINANCE</span>
          <span>COACH</span>
          <span>ROUTINE</span>
          <span>WORK</span>
          <span>FAMILY</span>
          <span>HEALTH</span>
        </div>
        {today && (
          <div className="oj-hud-chips">
            {today.badges.finance > 0 && (
              <span className="oj-chip oj-chip--alert">
                {today.badges.finance} financeiro
              </span>
            )}
            {today.badges.routine > 0 && (
              <span className="oj-chip">{today.badges.routine} hábitos</span>
            )}
            {today.badges.work > 0 && (
              <span className="oj-chip">{today.badges.work} tarefas</span>
            )}
            {today.alerts.length === 0 && <span className="oj-chip">Tudo em dia</span>}
          </div>
        )}
        <div className="oj-voice-mode" role="group" aria-label="Modo de escuta">
          <button
            type="button"
            data-active={voice.listeningMode === 'tap'}
            onClick={() => voice.setListeningMode('tap')}
          >
            TOCAR
          </button>
          <button
            type="button"
            data-active={voice.listeningMode === 'continuous'}
            onClick={() => voice.setListeningMode('continuous')}
          >
            ESCUTA ATIVA
          </button>
        </div>
        <button
          type="button"
          className="oj-hud-mic"
          data-live={listening}
          onClick={() => (listening ? voice.stop() : voice.start())}
        >
          {listening ? <Mic size={22} /> : <MicOff size={22} />}
          <span>
            {listening
              ? voice.listeningMode === 'continuous'
                ? 'Escuta ativa'
                : 'Ouvindo'
              : voice.listeningMode === 'continuous'
                ? 'Retomar escuta'
                : 'Tocar para falar'}
          </span>
        </button>
      </footer>
    </div>
  );
}
