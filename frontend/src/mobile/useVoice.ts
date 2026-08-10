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
import { synthesizeJarvisVoice } from './voiceApi';

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
export type VoiceListeningMode = 'tap' | 'continuous';

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
  listeningMode: VoiceListeningMode;
  start: () => void;
  stop: () => void;
  speak: (text: string) => void;
  setListeningMode: (mode: VoiceListeningMode) => void;
  setStatus: (status: VoiceStatus) => void;
  reset: () => void;
}

const BIN_COUNT = 64;
const ENVELOPE_FPS = 30;
const LISTENING_MODE_KEY = 'oj-life-listening-mode';

function storedListeningMode(): VoiceListeningMode {
  try {
    return localStorage.getItem(LISTENING_MODE_KEY) === 'continuous'
      ? 'continuous'
      : 'tap';
  } catch {
    return 'tap';
  }
}

async function buildVoiceEnvelope(audio: Blob): Promise<Uint8Array> {
  const AudioCtor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!AudioCtor) return new Uint8Array();

  const context = new AudioCtor();
  try {
    const buffer = await context.decodeAudioData(await audio.arrayBuffer());
    const channel = buffer.getChannelData(0);
    const samplesPerFrame = Math.max(
      1,
      Math.floor(buffer.sampleRate / ENVELOPE_FPS),
    );
    const envelope = new Uint8Array(
      Math.max(1, Math.ceil(channel.length / samplesPerFrame)),
    );
    for (let frame = 0; frame < envelope.length; frame += 1) {
      const start = frame * samplesPerFrame;
      const end = Math.min(channel.length, start + samplesPerFrame);
      let sumSquares = 0;
      for (let index = start; index < end; index += 1) {
        sumSquares += channel[index] * channel[index];
      }
      const rms = Math.sqrt(sumSquares / Math.max(1, end - start));
      envelope[frame] = Math.round(Math.min(1, rms * 7.5) * 255);
    }
    return envelope;
  } finally {
    void context.close().catch(() => undefined);
  }
}

export function useVoice(onFinalTranscript: (text: string) => void): VoiceState {
  const [status, setStatus] = useState<VoiceStatus>('idle');
  const [transcript, setTranscript] = useState('');
  const [interim, setInterim] = useState('');
  const [error, setError] = useState('');
  const [listeningMode, setListeningModeState] =
    useState<VoiceListeningMode>(storedListeningMode);

  const levelRef = useRef(0);
  const spectrumRef = useRef<Uint8Array>(new Uint8Array(BIN_COUNT));
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const frameRef = useRef(0);
  const playbackFrameRef = useRef(0);
  const playbackRef = useRef<HTMLAudioElement | null>(null);
  const playbackUrlRef = useRef('');
  const voiceRequestRef = useRef<AbortController | null>(null);
  const wantListeningRef = useRef(false);
  const continuousRef = useRef(listeningMode === 'continuous');
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
        if (
          detail.state === 'idle' &&
          continuousRef.current &&
          wantListeningRef.current
        ) {
          requestAnimationFrame(() => {
            if (continuousRef.current && wantListeningRef.current) {
              getNativeVoiceHandler()?.postMessage({ action: 'start' });
            }
          });
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
          wantListeningRef.current = continuousRef.current;
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

  const cancelRemoteSpeech = useCallback(() => {
    voiceRequestRef.current?.abort();
    voiceRequestRef.current = null;
    cancelAnimationFrame(playbackFrameRef.current);
    playbackFrameRef.current = 0;

    const playback = playbackRef.current;
    playbackRef.current = null;
    if (playback) {
      playback.onplay = null;
      playback.onended = null;
      playback.onerror = null;
      playback.pause();
    }
    if (playbackUrlRef.current) {
      URL.revokeObjectURL(playbackUrlRef.current);
      playbackUrlRef.current = '';
    }
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
    cancelRemoteSpeech();
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
        wantListeningRef.current = continuousRef.current;
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
  }, [cancelRemoteSpeech, startAudio, teardownAudio]);

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    cancelRemoteSpeech();
    getNativeVoiceHandler()?.postMessage({ action: 'stop' });
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    teardownAudio();
    setInterim('');
    setError('');
    setStatus('idle');
  }, [cancelRemoteSpeech, teardownAudio]);

  const setListeningMode = useCallback(
    (mode: VoiceListeningMode) => {
      continuousRef.current = mode === 'continuous';
      setListeningModeState(mode);
      try {
        localStorage.setItem(LISTENING_MODE_KEY, mode);
      } catch {
        /* Private browsing may not persist the preference. */
      }
      if (mode === 'continuous') start();
      else stop();
    },
    [start, stop],
  );

  const speak = useCallback((text: string) => {
    if (!text) return;
    cancelRemoteSpeech();

    const speakLocally = () => {
      const nativeVoice = getNativeVoiceHandler();
      if (nativeVoice) {
        setStatus('speaking');
        nativeVoice.postMessage({ action: 'speak', text });
        return;
      }
      if (typeof window.speechSynthesis === 'undefined') {
        setStatus('idle');
        return;
      }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'pt-BR';
      utterance.rate = 1.02;
      utterance.onend = () => {
        if (continuousRef.current && wantListeningRef.current) start();
        else setStatus('idle');
      };
      setStatus('speaking');
      window.speechSynthesis.speak(utterance);
    };

    const controller = new AbortController();
    voiceRequestRef.current = controller;

    void synthesizeJarvisVoice(text, controller.signal)
      .then(async (audioBlob) => {
        if (voiceRequestRef.current !== controller || controller.signal.aborted) {
          return;
        }
        voiceRequestRef.current = null;

        const envelopePromise = buildVoiceEnvelope(audioBlob).catch(
          () => new Uint8Array(),
        );
        const url = URL.createObjectURL(audioBlob);
        const playback = new Audio(url);
        playback.preload = 'auto';
        playbackRef.current = playback;
        playbackUrlRef.current = url;

        let envelope = new Uint8Array();
        void envelopePromise.then((decoded) => {
          envelope = decoded;
        });

        const animate = () => {
          if (playbackRef.current !== playback || playback.paused) return;
          const frame = Math.min(
            envelope.length - 1,
            Math.max(0, Math.floor(playback.currentTime * ENVELOPE_FPS)),
          );
          const level = envelope.length ? envelope[frame] / 255 : 0.12;
          levelRef.current = level;
          spectrumRef.current = Uint8Array.from(
            { length: BIN_COUNT },
            (_, index) =>
              Math.round(
                Math.min(1, level * (0.72 + 0.28 * Math.sin(index * 0.61))) *
                  255,
              ),
          );
          playbackFrameRef.current = requestAnimationFrame(animate);
        };

        const finish = () => {
          if (playbackRef.current !== playback) return;
          const resume = continuousRef.current && wantListeningRef.current;
          cancelRemoteSpeech();
          if (resume) start();
          else setStatus('idle');
        };
        playback.onplay = () => {
          setStatus('speaking');
          playbackFrameRef.current = requestAnimationFrame(animate);
        };
        playback.onended = finish;
        playback.onerror = () => {
          if (playbackRef.current !== playback) return;
          cancelRemoteSpeech();
          speakLocally();
        };

        try {
          await playback.play();
        } catch {
          if (playbackRef.current !== playback) return;
          cancelRemoteSpeech();
          speakLocally();
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        logger(
          `OpenAI voice fallback: ${error instanceof Error ? error.name : 'unknown'}`,
        );
        if (voiceRequestRef.current === controller) {
          voiceRequestRef.current = null;
        }
        speakLocally();
      });
  }, [cancelRemoteSpeech, start]);

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
      cancelRemoteSpeech();
      teardownAudio();
      window.speechSynthesis?.cancel();
    },
    [cancelRemoteSpeech, teardownAudio],
  );

  return {
    status,
    levelRef,
    spectrumRef,
    transcript,
    interim,
    error,
    supported,
    listeningMode,
    start,
    stop,
    speak,
    setListeningMode,
    setStatus,
    reset,
  };
}
