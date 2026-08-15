import { getToken, LifeApiError, setToken } from './api';

export interface RealtimeVoiceToken {
  clientSecret: string;
  expiresAt: number;
  model: string;
  voice: string;
}

/** Mint a short-lived, tenant-authenticated credential for one WebRTC call. */
export async function requestRealtimeVoiceToken(
  signal?: AbortSignal,
): Promise<RealtimeVoiceToken> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch('/v1/life/voice/realtime/token', {
    method: 'POST',
    headers,
    body: '{}',
    signal,
  });
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail) detail = String(body.detail);
    } catch {
      /* Keep the HTTP status when the server did not return JSON. */
    }
    if (response.status === 401) setToken('');
    throw new LifeApiError(detail, response.status);
  }

  const payload = (await response.json()) as {
    client_secret?: unknown;
    expires_at?: unknown;
    model?: unknown;
    voice?: unknown;
  };
  if (
    typeof payload.client_secret !== 'string' ||
    !payload.client_secret ||
    typeof payload.expires_at !== 'number' ||
    typeof payload.model !== 'string' ||
    typeof payload.voice !== 'string'
  ) {
    throw new LifeApiError('Credencial de voz realtime inválida', 502);
  }
  return {
    clientSecret: payload.client_secret,
    expiresAt: payload.expires_at,
    model: payload.model,
    voice: payload.voice,
  };
}

/** Fetch one authenticated, AI-generated Jarvis reply as playable audio. */
export async function synthesizeJarvisVoice(
  text: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const spokenText = text.trim();
  if (!spokenText) throw new LifeApiError('Resposta de voz vazia', 400);

  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch('/v1/life/voice/speech', {
    method: 'POST',
    headers,
    body: JSON.stringify({ text: spokenText }),
    signal,
  });

  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail) detail = String(body.detail);
    } catch {
      /* Keep the HTTP status when the server did not return JSON. */
    }
    if (response.status === 401) setToken('');
    throw new LifeApiError(detail, response.status);
  }

  const audio = await response.blob();
  if (!audio.type.startsWith('audio/') || audio.size === 0) {
    throw new LifeApiError('Resposta de voz inválida', 502);
  }
  return audio;
}
