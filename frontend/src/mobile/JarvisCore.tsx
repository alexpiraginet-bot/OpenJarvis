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

import { LayoutGrid, Mic, MicOff } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ask } from './api';
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
  const [answer, setAnswer] = useState('');
  // The draw loop must see the current status without being torn down and
  // rebuilt every time it changes.
  const statusRef = useRef<VoiceStatus>('idle');
  // The speech handler is created *before* `useVoice` returns, so it reaches
  // the controls through a ref rather than closing over a binding that does
  // not exist yet. `busy` is a ref too: recognition fires from outside React,
  // where a captured state value would already be stale.
  const voiceRef = useRef<VoiceState | null>(null);
  const busyRef = useRef(false);

  const handleQuestion = useCallback(
    async (question: string) => {
      const controls = voiceRef.current;
      if (!controls || busyRef.current) return;
      busyRef.current = true;
      controls.setStatus('thinking');
      statusRef.current = 'thinking';
      try {
        const result = await ask(question);
        setAnswer(result.answer);
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
      }
    },
    [onRefresh],
  );

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
    const reduceMotion = window.matchMedia?.(
      '(prefers-reduced-motion: reduce)',
    ).matches;

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

      context.clearRect(0, 0, width, height);
      if (!reduceMotion) rotation += 0.0035;

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
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
    };
  }, [voice.levelRef, voice.spectrumRef]);

  // Start listening as soon as the screen appears. Browsers only grant the
  // microphone after a user gesture, so a refusal here is expected and lands
  // the HUD in `denied` with a tap-to-enable button rather than an error.
  useEffect(() => {
    voice.start();
    return () => voice.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tone = TONES[voice.status];
  const listening = voice.status === 'listening';
  const spoken = voice.interim || voice.transcript;

  return (
    <div className="oj-jarvis">
      <header className="oj-hud-top">
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
        <canvas ref={canvasRef} className="oj-hud-canvas" />
        <button
          type="button"
          className="oj-hud-hit"
          onClick={() => (listening ? voice.stop() : voice.start())}
          aria-label={listening ? 'Parar de ouvir' : 'Começar a ouvir'}
        />
      </div>

      <div className="oj-hud-text">
        {voice.error && <p className="oj-hud-error">{voice.error}</p>}
        {spoken && <p className="oj-hud-said">“{spoken}”</p>}
        {answer && <p className="oj-hud-answer">{answer}</p>}
        {!spoken && !answer && !voice.error && (
          <p className="oj-hud-hint">
            {voice.supported
              ? 'Fale comigo. “Quanto gastei esse mês?”, “O que vence hoje?”'
              : 'Este navegador não reconhece fala — use os apps abaixo.'}
          </p>
        )}
      </div>

      <footer className="oj-hud-bottom">
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
        <button
          type="button"
          className="oj-hud-mic"
          data-live={listening}
          onClick={() => (listening ? voice.stop() : voice.start())}
        >
          {listening ? <Mic size={22} /> : <MicOff size={22} />}
          <span>{listening ? 'Ouvindo' : 'Tocar para falar'}</span>
        </button>
      </footer>
    </div>
  );
}
