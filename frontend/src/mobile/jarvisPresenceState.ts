export const JARVIS_PRESENCE_STATES = [
  'boot',
  'idle',
  'loading',
  'listening',
  'thinking',
  'executing',
  'speaking',
  'success',
  'error',
  'fallback',
] as const;

export type JarvisPresenceState = (typeof JARVIS_PRESENCE_STATES)[number];

export interface JarvisPresenceSignals {
  initializing?: boolean;
  profileBuilding?: boolean;
  listening?: boolean;
  thinking?: boolean;
  executing?: boolean;
  speaking?: boolean;
  success?: boolean;
  error?: boolean;
  fallback?: boolean;
}

interface PresenceCopy {
  label: string;
  color: string;
  ring: string;
  speed: number;
  energy: number;
}

const PRESENCE_COPY: Record<JarvisPresenceState, PresenceCopy> = {
  boot: {
    label: 'Inicializando o núcleo Jarvis',
    color: '#38bdf8',
    ring: '#67e8f9',
    speed: 0.72,
    energy: 0.42,
  },
  idle: {
    label: 'Jarvis pronto',
    color: '#0e7490',
    ring: '#22d3ee',
    speed: 0.24,
    energy: 0.18,
  },
  loading: {
    label: 'Preparando seu contexto',
    color: '#38bdf8',
    ring: '#67e8f9',
    speed: 0.68,
    energy: 0.45,
  },
  listening: {
    label: 'Ouvindo você',
    color: '#22d3ee',
    ring: '#a5f3fc',
    speed: 0.5,
    energy: 0.72,
  },
  thinking: {
    label: 'Analisando sua solicitação',
    color: '#f59e0b',
    ring: '#fcd34d',
    speed: 0.86,
    energy: 0.58,
  },
  executing: {
    label: 'Executando a ação confirmada',
    color: '#f59e0b',
    ring: '#67e8f9',
    speed: 1,
    energy: 0.74,
  },
  speaking: {
    label: 'Jarvis está respondendo',
    color: '#34d399',
    ring: '#a7f3d0',
    speed: 0.58,
    energy: 0.66,
  },
  success: {
    label: 'Concluído',
    color: '#34d399',
    ring: '#a7f3d0',
    speed: 0.36,
    energy: 0.48,
  },
  error: {
    label: 'O Jarvis precisa da sua atenção',
    color: '#ef4444',
    ring: '#fca5a5',
    speed: 0.18,
    energy: 0.28,
  },
  fallback: {
    label: 'Jarvis disponível em modo visual simplificado',
    color: '#38bdf8',
    ring: '#94a3b8',
    speed: 0,
    energy: 0.16,
  },
};

/** Latest active signal wins immediately; no queued animation state exists. */
export function deriveJarvisPresenceState(
  signals: JarvisPresenceSignals,
): JarvisPresenceState {
  if (signals.error) return 'error';
  if (signals.executing) return 'executing';
  if (signals.speaking) return 'speaking';
  if (signals.listening) return 'listening';
  if (signals.thinking) return 'thinking';
  if (signals.profileBuilding) return 'loading';
  if (signals.success) return 'success';
  if (signals.initializing) return 'boot';
  if (signals.fallback) return 'fallback';
  return 'idle';
}

export function getJarvisPresenceCopy(state: JarvisPresenceState): PresenceCopy {
  return PRESENCE_COPY[state];
}

export function presenceNeedsProgress(state: JarvisPresenceState): boolean {
  return (
    state === 'boot' ||
    state === 'loading' ||
    state === 'thinking' ||
    state === 'executing'
  );
}
