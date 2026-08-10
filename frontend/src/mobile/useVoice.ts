/**
 * Voice for the Jarvis core screen: live microphone level, speech recognition
 * and spoken replies.
 *
 * Deliberately built on the browser's own Web Speech API rather than the
 * server's `/v1/speech` transcription route. The HUD has to react to the voice
 * *while it is being spoken* — a record-then-upload round trip cannot animate
 * anything, and the whole point of this screen is that it is alive. The
 * server-side path stays the right choice for the chat composer, which
 * transcribes a finished clip.
 *
 * The analyser runs independently of recognition, so the visuals stay live even
 * where `SpeechRecognition` is unavailable (Firefox, some Android browsers).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

/** Minimal shape of the Web Speech API — absent from lib.dom in this TS version. */
interface SpeechRecognitionAlternativeLike {
  transcript: string;
}
interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionAlternativeLike;
}
interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: SpeechRecognitionResultLike;
  };
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

interface NativeVoiceHandler {
  postMessage(message: { action: 'start' | 'stop' | 'speak'; text?: string }): void;
}

interface NativeVoiceEventDetail {
  type: 'state' | 'transcript' | 'level' | 'error';
  state?: VoiceStatus;
  text?: string;
  final?: boolean;
  level?: number;
  spectrum?: number[];
}

/** Diagnostics go to the console, never to the client's screen. */
function logger(message: string): void {
  if (import.meta.env.DEV) console.debug('[voice]', message);
}

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const scope = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

function getNativeVoiceHandler(): NativeVoiceHandler | null {
  const scope = window as unknown as {
    webkit?: {
      messageHandlers?: {
        jarvisVoice?: NativeVoiceHandler;
      };
    };
  };
  return scope.webkit?.messageHandlers?.jarvisVoice ?? null;
}

export type VoiceStatus = 'idle' | 'listening' | 'thinking' | 'speaking' | 'denied';

export interface VoiceState {
  status: VoiceStatus;
  /**
   * Live 0–1 loudness and signal bins, exposed as refs rather than state.
   * The browser supplies frequency bins; the native shell supplies time bins.
   *
   * These update on every animation frame. Routing them through `useState`
   * would re-render the whole shell 60 times a second to move a few pixels of
   * canvas; the draw loop reads the refs directly instead, and React never
   * hears about it.
   */
  levelRef: React.MutableRefObject<number>;
  spectrumRef: React.MutableRefObject<Uint8Array>;
  transcript: string;
  interim: string;
  error: string;
  supported: boolean;
  start: () => void;
  stop: () => void;
  speak: (text: string) => void;
  setStatus: (status: VoiceStatus) => void;
  reset: () => void;
}

const BIN_COUNT = 64;

export function useVoice(onFinalTranscript: (text: string) => void): VoiceState {
  const [status, setStatus] = useState<VoiceStatus>('idle');
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim] = useState('');
  const [error, setError] = useState('');

  const levelRef = useRef(0);
  const spectrumRef = useRef<Uint8Array>(new Uint8Array(BIN_COUNT));
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const frameRef = useRef(0);
  const wantListeningRef = useRef(false);
  // Kept in a ref so the recognition callback never closes over a stale
  // handler — recognition instances outlive a render.
  const onFinalRef = useRef(onFinalTranscript);
  onFinalRef.current = onFinalTranscript;

  const supported =
    typeof window !== 'undefined' &&
    (getNativeVoiceHandler() !== null || getRecognitionCtor() !== null);

  useEffect(() => {
    const onNativeVoice = (event: Event) => {
      const detail = (event as CustomEvent<NativeVoiceEventDetail>).detail;
      if (!detail) return;

      if (detail.type === 'error') {
        wantListeningRef.current = false;
        setInterim('');
        setError(detail.text || 'Não consegui acessar o microfone.');
        setStatus('denied');
        return;
      }

      if (detail.type === 'state' && detail.state) {
        setStatus(detail.state);
        if (detail.state === 'listening') setError('');
        if (detail.state !== 'listening') {
          levelRef.current = 0;
          spectrumRef.current = new Uint8Array(BIN_COUNT);
        }
        return;
      }

      if (detail.type === 'level') {
        levelRef.current = Math.max(0, Math.min(1, detail.level ?? 0));
        if (Array.isArray(detail.spectrum)) {
          spectrumRef.current = Uint8Array.from(
            detail.spectrum.slice(0, BIN_COUNT),
            (value) => Math.max(0, Math.min(255, Math.round(value))),
          );
        }
        return;
      }

      if (detail.type === 'transcript' && detail.text?.trim()) {
        const text = detail.text.trim();
        if (detail.final) {
          wantListeningRef.current = false;
          setTranscript(text);
          setInterim('');
          onFinalRef.current(text);
        } else {
          setInterim(text);
        }
      }
    };

    window.addEventListener('jarvis-native-voice', onNativeVoice);
    return () => window.removeEventListener('jarvis-native-voice', onNativeVoice);
  }, []);

  const teardownAudio = useCallback(() => {
    cancelAnimationFrame(frameRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioContextRef.current?.close().catch(() => undefined);
    audioContextRef.current = null;
    analyserRef.current = null;
    levelRef.current = 0;
    spectrumRef.current = new Uint8Array(BIN_COUNT);
  }, []);

  const startAudio = useCallback(async () => {
    if (analyserRef.current) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;

    const AudioCtor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    const context = new AudioCtor();
    audioContextRef.current = context;

    const analyser = context.createAnalyser();
    analyser.fftSize = BIN_COUNT * 2;
    // Without smoothing the rings jitter into noise; too much and they lag the
    // voice. 0.75 tracks speech envelopes while staying readable.
    analyser.smoothingTimeConstant = 0.75;
    context.createMediaStreamSource(stream).connect(analyser);
    analyserRef.current = analyser;

    const bins = new Uint8Array(analyser.frequencyBinCount);
    const tick = () => {
      const node = analyserRef.current;
      if (!node) return;
      node.getByteFrequencyData(bins);
      spectrumRef.current = bins.slice(0, BIN_COUNT);
      let sum = 0;
      for (let i = 0; i < bins.length; i += 1) sum += bins[i];
      // 140 rather than 255: normal speech never saturates the full byte
      // range, so dividing by the maximum would leave the rings barely moving.
      levelRef.current = Math.min(1, sum / bins.length / 140);
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
  }, []);

  const start = useCallback(() => {
    setError('');
    wantListeningRef.current = true;
    window.speechSynthesis?.cancel();

    const nativeVoice = getNativeVoiceHandler();
    if (nativeVoice) {
      nativeVoice.postMessage({ action: 'start' });
      setStatus('listening');
      return;
    }

    startAudio()
      .then(() => {
        if (wantListeningRef.current) {
          setError('');
          setStatus('listening');
        } else {
          teardownAudio();
        }
      })
      .catch(() => {
        if (!wantListeningRef.current) {
          teardownAudio();
          return;
        }
        recognitionRef.current?.abort();
        recognitionRef.current = null;
        setError('Preciso do microfone para ouvir você.');
        setStatus('denied');
        wantListeningRef.current = false;
      });

    const Ctor = getRecognitionCtor();
    if (!Ctor || recognitionRef.current) return;

    const recognition = new Ctor();
    recognition.lang = 'pt-BR';
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event) => {
      let finalText = '';
      let pending = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else pending += result[0].transcript;
      }
      setInterim(pending);
      if (finalText.trim()) {
        wantListeningRef.current = false;
        recognition.stop();
        recognitionRef.current = null;
        teardownAudio();
        setTranscript(finalText.trim());
        setInterim('');
        onFinalRef.current(finalText.trim());
      }
    };

    recognition.onerror = (event) => {
      // Only one recognition error is worth a client's attention, because it
      // is the only one they can act on: a denied microphone. The rest are
      // routine on a screen that listens continuously — a pause in speech, a
      // restart between phrases, another app grabbing the mic, a flaky
      // network — and the browser recovers from all of them via `onend`.
      // Painting the HUD red with a raw error code teaches people to distrust
      // a working app.
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        recognitionRef.current = null;
        teardownAudio();
        setError('Preciso do microfone para ouvir você. Libere nas permissões.');
        setStatus('denied');
        wantListeningRef.current = false;
        return;
      }
      logger(`recognition: ${event.error}`);
    };

    recognition.onend = () => {
      // Browsers stop recognition on their own after a pause. If the user
      // still wants to be heard, restart it.
      if (wantListeningRef.current) {
        try {
          recognition.start();
        } catch {
          /* already restarting */
        }
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      /* start() throws if called twice — harmless */
    }
  }, [startAudio, teardownAudio]);

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    getNativeVoiceHandler()?.postMessage({ action: 'stop' });
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    teardownAudio();
    setInterim('');
    setError('');
    setStatus('idle');
  }, [teardownAudio]);

  const speak = useCallback((text: string) => {
    if (!text) return;
    const nativeVoice = getNativeVoiceHandler();
    if (nativeVoice) {
      setStatus('speaking');
      nativeVoice.postMessage({ action: 'speak', text });
      return;
    }
    if (typeof window.speechSynthesis === 'undefined') return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'pt-BR';
    utterance.rate = 1.02;
    utterance.onend = () => {
      setStatus(wantListeningRef.current ? 'listening' : 'idle');
    };
    setStatus('speaking');
    window.speechSynthesis.speak(utterance);
  }, []);

  const reset = useCallback(() => {
    setTranscript('');
    setInterim('');
    setError('');
  }, []);

  useEffect(
    () => () => {
      wantListeningRef.current = false;
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      teardownAudio();
      window.speechSynthesis?.cancel();
    },
    [teardownAudio],
  );

  return {
    status,
    levelRef,
    spectrumRef,
    transcript,
    interim,
    error,
    supported,
    start,
    stop,
    speak,
    setStatus,
    reset,
  };
}
