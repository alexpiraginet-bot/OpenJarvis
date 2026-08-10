/**
 * The Jarvis core — the first thing a client sees.
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

import { Check, LayoutGrid, Mic, MicOff, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ask,
  cancelAction,
  confirmAction,
  listPendingActions,
  type ConfirmationMethod,
  type JarvisActionProposal,
} from './api';
import type { Today } from './types';
import { useVoice, type VoiceState, type VoiceStatus } from './useVoice';

/** Palette per status. The HUD's colour *is* its state readout. */
const TONES: Record<VoiceStatus, { core: string; ring: string; label: string }> = {
  idle: { core: '#0e7490', ring: 'rgba(34, 211, 238, 0.45)', label: 'Em espera' },
  listening: { core: '#22d3ee', ring: 'rgba(34, 211, 238, 0.95)', label: 'Ouvindo' },
  thinking: { core: '#f59e0b', ring: 'rgba(245, 158, 11, 0.9)', label: 'Pensando' },
  speaking: { core: '#34d399', ring: 'rgba(52, 211, 153, 0.95)', label: 'Respondendo' },
  denied: { core: '#ef4444', ring: 'rgba(239, 68, 68, 0.8)', label: 'Sem microfone' },
};

const TICKS = 72;
const CORE_TEXTURE_SIZE = 768;

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

function confirmationFailureMessage(proposal: JarvisActionProposal): string {
  const detail = proposal.result?.error ?? proposal.result?.detail;
  return typeof detail === 'string' && detail.trim()
    ? detail
    : 'A ação não foi concluída. Revise os dados e tente novamente.';
}

export function JarvisCore({
  today,
  onOpenSpringboard,
  onRefresh,
}: {
  today: Today | null;
  onOpenSpringboard: () => void;
  onRefresh: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const gestureStartYRef = useRef<number | null>(null);
  const gestureOpenedRef = useRef(false);
  const [answer, setAnswer] = useState('');
  const [proposals, setProposals] = useState<JarvisActionProposal[]>([]);
  const [resolving, setResolving] = useState('');
  // The draw loop must see the current status without being torn down and
  // rebuilt every time it changes.
  const statusRef = useRef<VoiceStatus>('idle');
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

  useEffect(() => {
    proposalsRef.current = proposals;
  }, [proposals]);

  const resolveProposal = useCallback(
    async (
      proposal: JarvisActionProposal,
      approved: boolean,
      confirmationMethod: ConfirmationMethod = 'explicit',
    ) => {
      if (resolvingRef.current) return;
      resolvingRef.current = proposal.id;
      setResolving(proposal.id);
      try {
        if (approved) {
          const result = await confirmAction(proposal.id, confirmationMethod);
          if (!confirmedProposalSucceeded(result.proposal)) {
            throw new Error(confirmationFailureMessage(result.proposal));
          }
          setAnswer('Ação confirmada e registrada.');
          voiceRef.current?.speak('Ação confirmada e registrada.');
          onRefresh();
        } else {
          await cancelAction(proposal.id);
          setAnswer('Ação cancelada.');
          voiceRef.current?.speak('Ação cancelada.');
        }
        setProposals((current) => {
          const remaining = current.filter((item) => item.id !== proposal.id);
          proposalsRef.current = remaining;
          return remaining;
        });
      } catch (exc) {
        const message =
          exc instanceof Error ? exc.message : 'Não consegui concluir a ação.';
        setAnswer(message);
        voiceRef.current?.speak(message);
      } finally {
        resolvingRef.current = '';
        setResolving('');
      }
    },
    [onRefresh],
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
      controls.setStatus('thinking');
      statusRef.current = 'thinking';
      try {
        const result = await ask(question);
        setAnswer(result.answer);
        const nextProposals = result.proposals ?? [];
        proposalsRef.current = nextProposals;
        setProposals(nextProposals);
        controls.speak(result.answer);
        // A spoken exchange may have changed the data behind the badges.
        onRefresh();
      } catch (exc) {
        const message =
          exc instanceof Error ? exc.message : 'Não consegui responder agora.';
        setAnswer(message);
        controls.speak(message);
      } finally {
        busyRef.current = false;
        const queued = queuedQuestionsRef.current.shift();
        if (queued) {
          queueMicrotask(() => void handleQuestionRef.current(queued));
        }
      }
    },
    [onRefresh, resolveProposal],
  );
  handleQuestionRef.current = handleQuestion;

  const voice = useVoice(handleQuestion);
  voiceRef.current = voice;
  statusRef.current = voice.status;

  // -- The HUD -------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    let frame = 0;
    let rotation = 0;
    let destroyed = false;
    let coreTexture: HTMLCanvasElement | null = null;
    const reduceMotion = window.matchMedia?.(
      '(prefers-reduced-motion: reduce)',
    ).matches;

    const textureImage = new Image();
    textureImage.decoding = 'async';
    textureImage.onload = () => {
      if (destroyed) return;
      const texture = document.createElement('canvas');
      texture.width = CORE_TEXTURE_SIZE;
      texture.height = CORE_TEXTURE_SIZE;
      const textureContext = texture.getContext('2d', {
        willReadFrequently: true,
      });
      if (!textureContext) return;
      textureContext.drawImage(
        textureImage,
        0,
        0,
        CORE_TEXTURE_SIZE,
        CORE_TEXTURE_SIZE,
      );
      const pixels = textureContext.getImageData(
        0,
        0,
        CORE_TEXTURE_SIZE,
        CORE_TEXTURE_SIZE,
      );
      for (let index = 0; index < pixels.data.length; index += 4) {
        pixels.data[index + 3] = corePixelAlpha(
          pixels.data[index],
          pixels.data[index + 1],
          pixels.data[index + 2],
          pixels.data[index + 3],
        );
      }
      textureContext.clearRect(0, 0, CORE_TEXTURE_SIZE, CORE_TEXTURE_SIZE);
      textureContext.putImageData(pixels, 0, 0);
      coreTexture = texture;
    };
    textureImage.src = '/aether-neural-core.png';

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const { clientWidth, clientHeight } = canvas;
      canvas.width = clientWidth * ratio;
      canvas.height = clientHeight * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize);

    const draw = () => {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const cx = width / 2;
      const cy = height / 2;
      const base = Math.min(width, height) * 0.3;
      const tone = TONES[statusRef.current];
      const level = voice.levelRef.current;
      const spectrum = voice.spectrumRef.current;
      const elapsed = performance.now() / 1000;

      context.clearRect(0, 0, width, height);
      if (!reduceMotion) {
        const velocity =
          statusRef.current === 'thinking'
            ? 0.011
            : statusRef.current === 'listening'
              ? 0.0065
              : 0.0035;
        rotation += velocity;
      }

      // The source artwork is converted to a luminance alpha mask once, then
      // rendered as a voice-reactive texture. That removes the opaque square
      // permanently and lets the neural filaments move independently of the
      // HUD rings instead of rotating a flat image.
      if (coreTexture) {
        const breathing = reduceMotion ? 1 : 1 + Math.sin(elapsed * 1.35) * 0.018;
        const reactive = reduceMotion ? 0 : Math.min(level * 0.09, 0.09);
        const size = base * 2.56 * (breathing + reactive);
        const half = size / 2;

        context.save();
        context.translate(cx, cy);
        context.rotate(rotation * 0.16);
        context.globalCompositeOperation = 'lighter';
        context.globalAlpha = 0.16 + Math.min(level * 0.12, 0.12);
        context.shadowColor = tone.core;
        context.shadowBlur = 30 + level * 26;
        context.drawImage(coreTexture, -half * 1.035, -half * 1.035, size * 1.035, size * 1.035);
        context.restore();

        context.save();
        context.translate(cx, cy);
        context.rotate(-rotation * 0.08);
        context.globalCompositeOperation = 'screen';
        context.globalAlpha = 0.72;
        context.drawImage(coreTexture, -half, -half, size, size);

        if (!reduceMotion) {
          const slices = 48;
          const sourceWidth = coreTexture.width / slices;
          const destinationWidth = size / slices;
          const distortion = size * (0.004 + Math.min(level, 1) * 0.012);
          context.globalCompositeOperation = 'lighter';
          context.globalAlpha = 0.34 + Math.min(level * 0.18, 0.18);
          for (let slice = 0; slice < slices; slice += 1) {
            const normalized = (slice + 0.5) / slices;
            const phase = elapsed * 1.8 + normalized * Math.PI * 4;
            const offsetX = Math.sin(phase) * distortion;
            const offsetY = Math.cos(phase * 0.72) * distortion * 0.9;
            context.drawImage(
              coreTexture,
              slice * sourceWidth,
              0,
              sourceWidth + 1,
              coreTexture.height,
              -half + slice * destinationWidth + offsetX,
              -half + offsetY,
              destinationWidth + 1,
              size,
            );
          }
        }
        context.restore();
      }

      // Outer dashed ring — slow, steady, the "system is up" signal.
      context.save();
      context.translate(cx, cy);
      context.rotate(rotation);
      context.strokeStyle = tone.ring;
      context.globalAlpha = 0.35;
      context.lineWidth = 1;
      context.setLineDash([12, 18]);
      context.beginPath();
      context.arc(0, 0, base * 1.42, 0, Math.PI * 2);
      context.stroke();
      context.setLineDash([]);
      context.restore();

      // Tick ring — each tick's length is one frequency bin, so the ring
      // literally spells out the shape of the voice.
      context.save();
      context.translate(cx, cy);
      context.rotate(-rotation * 1.6);
      context.strokeStyle = tone.ring;
      context.lineWidth = 2;
      for (let i = 0; i < TICKS; i += 1) {
        const bin = spectrum[i % spectrum.length] ?? 0;
        const energy = bin / 255;
        const inner = base * 1.12;
        const outer = inner + 6 + energy * 34;
        const angle = (i / TICKS) * Math.PI * 2;
        context.globalAlpha = 0.25 + energy * 0.75;
        context.beginPath();
        context.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
        context.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer);
        context.stroke();
      }
      context.restore();

      // Three sweeping arcs at different speeds — the Stark "it's alive" cue.
      context.save();
      context.translate(cx, cy);
      context.strokeStyle = tone.ring;
      context.lineWidth = 2.5;
      context.globalAlpha = 0.8;
      const arcs = [
        { radius: base * 0.95, from: rotation * 2.2, span: 1.1 },
        { radius: base * 0.82, from: -rotation * 3.1 + 2, span: 0.8 },
        { radius: base * 0.68, from: rotation * 1.4 + 4, span: 1.5 },
      ];
      for (const arc of arcs) {
        context.beginPath();
        context.arc(0, 0, arc.radius, arc.from, arc.from + arc.span);
        context.stroke();
      }
      context.restore();

      // Reactive waveform — a closed blob whose radius follows the spectrum.
      context.save();
      context.translate(cx, cy);
      context.beginPath();
      const points = 96;
      for (let i = 0; i <= points; i += 1) {
        const angle = (i / points) * Math.PI * 2;
        const bin = spectrum[i % spectrum.length] ?? 0;
        const radius = base * 0.52 + (bin / 255) * base * 0.3 + level * 8;
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;
        if (i === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.closePath();
      context.strokeStyle = tone.core;
      context.lineWidth = 2;
      context.globalAlpha = 0.9;
      context.stroke();
      context.restore();

      // The core: a glow that breathes with loudness.
      const pulse = base * (0.3 + level * 0.22);
      const glow = context.createRadialGradient(cx, cy, 0, cx, cy, pulse * 2.1);
      glow.addColorStop(0, tone.core);
      glow.addColorStop(0.35, tone.ring);
      glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
      context.save();
      context.globalAlpha = 0.55 + level * 0.45;
      context.fillStyle = glow;
      context.beginPath();
      context.arc(cx, cy, pulse * 2.1, 0, Math.PI * 2);
      context.fill();
      context.restore();

      context.save();
      context.fillStyle = '#ffffff';
      context.globalAlpha = 0.85;
      context.beginPath();
      context.arc(cx, cy, pulse * 0.28, 0, Math.PI * 2);
      context.fill();
      context.restore();

      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);

    return () => {
      destroyed = true;
      textureImage.onload = null;
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
    };
  }, [voice.levelRef, voice.spectrumRef]);

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

  const tone = TONES[voice.status];
  const listening = voice.status === 'listening';
  const spoken = voice.interim || voice.transcript;

  return (
    <div className="oj-jarvis">
      <header className="oj-hud-top">
        <div className="oj-hud-brand">
          <strong>JARVIS LIFE</strong>
          <span>NEURAL CORE / VOICE OS</span>
        </div>
        <div className="oj-hud-readout">
          <span className="oj-hud-dot" style={{ background: tone.core }} />
          {tone.label}
        </div>
        <button
          type="button"
          className="oj-hud-btn"
          onClick={onOpenSpringboard}
          aria-label="Abrir aplicativos"
        >
          <LayoutGrid size={20} />
        </button>
      </header>

      <div className="oj-hud-stage">
        <div className="oj-hud-telemetry" aria-hidden="true">
          <span>CORE SYNC<br /><strong>100%</strong></span>
          <span>AGENTS<br /><strong>05 ONLINE</strong></span>
          <span>VOICE LINK<br /><strong>{tone.label}</strong></span>
        </div>
        <canvas
          ref={canvasRef}
          className="oj-hud-canvas"
          role="img"
          aria-label="Núcleo neural vivo do Jarvis"
        />
        <button
          type="button"
          className="oj-hud-hit"
          onPointerDown={(event) => {
            gestureStartYRef.current = event.clientY;
            gestureOpenedRef.current = false;
          }}
          onPointerUp={(event) => {
            const startY = gestureStartYRef.current;
            gestureStartYRef.current = null;
            if (startY !== null && startY - event.clientY > 52) {
              gestureOpenedRef.current = true;
              onOpenSpringboard();
            }
          }}
          onClick={() => {
            if (gestureOpenedRef.current) {
              gestureOpenedRef.current = false;
              return;
            }
            if (listening) voice.stop();
            else voice.start();
          }}
          aria-label={listening ? 'Parar de ouvir' : 'Começar a ouvir'}
        />
        <span className="oj-hud-swipe" aria-hidden="true">
          deslize para cima · aplicativos
        </span>
      </div>

      <div className="oj-hud-text">
        {voice.error && <p className="oj-hud-error">{voice.error}</p>}
        {spoken && <p className="oj-hud-said">“{spoken}”</p>}
        {answer && <p className="oj-hud-answer">{answer}</p>}
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
              ? 'Fale comigo. “Quanto gastei esse mês?”, “O que vence hoje?”'
              : 'Este navegador não reconhece fala — use os apps abaixo.'}
          </p>
        )}
      </div>

      <footer className="oj-hud-bottom">
        <div className="oj-hud-specialists" aria-label="Especialistas disponíveis">
          <span>FINANCE</span>
          <span>COACH</span>
          <span>ROUTINE</span>
          <span>WORK</span>
          <span>FAMILY</span>
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
