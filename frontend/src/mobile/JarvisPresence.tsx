import { useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';
import {
  getJarvisPresenceCopy,
  presenceNeedsProgress,
  type JarvisPresenceState,
} from './jarvisPresenceState';

const CORE_ASSET = '/assets/aether-neural-core-768.jpg';
const BOOT_TIMEOUT_MS = 5000;

type PresenceVariant = 'hero' | 'orbital' | 'login' | 'compact' | 'inline';
type PresenceRendererMode = 'boot' | 'webgl' | 'fallback' | 'static';
type PresenceFallbackReason = 'timeout' | 'asset' | 'webgl' | 'context-lost';

interface PresenceRenderer {
  resize: () => void;
  draw: (
    elapsedSeconds: number,
    level: number,
    spectralEnergy: number,
    state: JarvisPresenceState,
  ) => void;
  dispose: () => void;
}

interface FrameLoopOptions {
  draw: () => void;
  reduceMotion: boolean;
  requestFrame?: (callback: FrameRequestCallback) => number;
  cancelFrame?: (handle: number) => void;
}

interface BootFallbackOptions {
  schedule?: (callback: () => void, delay: number) => unknown;
  clear?: (handle: unknown) => void;
}

export function createPresenceFrameLoop({
  draw,
  reduceMotion,
  requestFrame = requestAnimationFrame,
  cancelFrame = cancelAnimationFrame,
}: FrameLoopOptions): () => void {
  if (reduceMotion) {
    draw();
    return () => undefined;
  }

  let frame = 0;
  const tick = () => {
    draw();
    frame = requestFrame(tick);
  };
  frame = requestFrame(tick);
  return () => cancelFrame(frame);
}

export function schedulePresenceBootFallback(
  onFallback: (reason: PresenceFallbackReason) => void,
  {
    schedule = (callback, delay) => window.setTimeout(callback, delay),
    clear = (handle) => window.clearTimeout(handle as number),
  }: BootFallbackOptions = {},
): () => void {
  const handle = schedule(() => onFallback('timeout'), BOOT_TIMEOUT_MS);
  return () => clear(handle);
}

const VERTEX_SHADER = `
attribute vec2 a_position;
varying vec2 v_uv;

void main() {
  v_uv = (a_position + 1.0) * 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

const FRAGMENT_SHADER = `
precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_texture;
uniform vec2 u_resolution;
uniform vec3 u_color;
uniform vec3 u_ring;
uniform float u_time;
uniform float u_level;
uniform float u_spectrum;
uniform float u_speed;
uniform float u_energy;
uniform float u_motion;

void main() {
  float aspect = u_resolution.x / max(u_resolution.y, 1.0);
  vec2 point = v_uv - 0.5;
  point.x *= aspect;
  float radius = length(point);
  float angle = atan(point.y, point.x);

  float breath = 1.0 + sin(u_time * (0.9 + u_speed)) * 0.018 * u_motion;
  float response = 1.0 + min(u_level, 1.0) * 0.07;
  vec2 textureUv = point / (breath * response);
  textureUv.x /= max(aspect, 0.001);
  textureUv += 0.5;
  float distortion = (0.004 + u_spectrum * 0.012) * u_motion;
  textureUv.x += sin(textureUv.y * 18.0 + u_time * (1.2 + u_speed)) * distortion;
  textureUv.y += cos(textureUv.x * 14.0 - u_time * 0.8) * distortion * 0.65;

  vec3 source = texture2D(u_texture, textureUv).rgb;
  float inBounds = step(0.0, textureUv.x) * step(textureUv.x, 1.0)
    * step(0.0, textureUv.y) * step(textureUv.y, 1.0);
  float filament = smoothstep(0.035, 0.46, max(source.r, max(source.g, source.b))) * inBounds;
  vec3 neural = mix(source, u_color, 0.48 + u_energy * 0.18);

  float outerRing = 1.0 - smoothstep(0.0, 0.008, abs(radius - 0.43));
  float innerRing = 1.0 - smoothstep(0.0, 0.006, abs(radius - 0.34));
  float sweep = 0.5 + 0.5 * sin(angle * 3.0 - u_time * (1.4 + u_speed * 2.0));
  float ringMask = (outerRing * (0.16 + sweep * 0.38) + innerRing * 0.18)
    * (0.45 + u_energy * 0.55);
  float voiceHalo = exp(-34.0 * abs(radius - (0.27 + u_level * 0.035)))
    * (0.08 + u_level * 0.52);

  vec3 color = neural * filament * (0.72 + u_energy * 0.48)
    + u_ring * (ringMask + voiceHalo);
  float alpha = clamp(filament * 0.92 + ringMask + voiceHalo, 0.0, 1.0);
  gl_FragColor = vec4(color, alpha);
}
`;

function compileShader(
  gl: WebGLRenderingContext,
  type: number,
  source: string,
): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function colorChannels(hex: string): [number, number, number] {
  return [
    Number.parseInt(hex.slice(1, 3), 16) / 255,
    Number.parseInt(hex.slice(3, 5), 16) / 255,
    Number.parseInt(hex.slice(5, 7), 16) / 255,
  ];
}

export function createWebGLPresenceRenderer(
  canvas: HTMLCanvasElement,
  image: TexImageSource,
  onContextLost: () => void,
): PresenceRenderer | null {
  const gl = canvas.getContext('webgl', {
    alpha: true,
    antialias: true,
    depth: false,
    powerPreference: 'low-power',
    premultipliedAlpha: true,
  }) as WebGLRenderingContext | null;
  if (!gl) return null;

  const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
  if (!vertex || !fragment) {
    if (vertex) gl.deleteShader(vertex);
    if (fragment) gl.deleteShader(fragment);
    return null;
  }

  const program = gl.createProgram();
  if (!program) {
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    return null;
  }
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }

  const buffer = gl.createBuffer();
  const texture = gl.createTexture();
  const position = gl.getAttribLocation(program, 'a_position');
  if (!buffer || !texture || position < 0) {
    if (buffer) gl.deleteBuffer(buffer);
    if (texture) gl.deleteTexture(texture);
    gl.deleteProgram(program);
    return null;
  }

  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.RGBA,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    image,
  );

  const uniforms = {
    resolution: gl.getUniformLocation(program, 'u_resolution'),
    color: gl.getUniformLocation(program, 'u_color'),
    ring: gl.getUniformLocation(program, 'u_ring'),
    time: gl.getUniformLocation(program, 'u_time'),
    level: gl.getUniformLocation(program, 'u_level'),
    spectrum: gl.getUniformLocation(program, 'u_spectrum'),
    speed: gl.getUniformLocation(program, 'u_speed'),
    energy: gl.getUniformLocation(program, 'u_energy'),
    motion: gl.getUniformLocation(program, 'u_motion'),
  };

  const resize = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
  };

  const contextLost = (event: Event) => {
    event.preventDefault();
    onContextLost();
  };
  canvas.addEventListener('webglcontextlost', contextLost);

  return {
    resize,
    draw: (elapsedSeconds, level, spectralEnergy, state) => {
      const copy = getJarvisPresenceCopy(state);
      resize();
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.enableVertexAttribArray(position);
      gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.uniform2f(uniforms.resolution, canvas.width, canvas.height);
      gl.uniform3fv(uniforms.color, colorChannels(copy.color));
      gl.uniform3fv(uniforms.ring, colorChannels(copy.ring));
      gl.uniform1f(uniforms.time, elapsedSeconds);
      gl.uniform1f(uniforms.level, Math.max(0, Math.min(1, level)));
      gl.uniform1f(
        uniforms.spectrum,
        Math.max(0, Math.min(1, spectralEnergy)),
      );
      gl.uniform1f(uniforms.speed, copy.speed);
      gl.uniform1f(uniforms.energy, copy.energy);
      gl.uniform1f(uniforms.motion, state === 'fallback' ? 0 : 1);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    },
    dispose: () => {
      canvas.removeEventListener('webglcontextlost', contextLost);
      gl.deleteBuffer(buffer);
      gl.deleteTexture(texture);
      gl.deleteProgram(program);
    },
  };
}

function averageSpectrum(spectrum: Uint8Array | undefined): number {
  if (!spectrum?.length) return 0;
  const step = Math.max(1, Math.floor(spectrum.length / 24));
  let total = 0;
  let count = 0;
  for (let index = 0; index < spectrum.length; index += step) {
    total += spectrum[index] ?? 0;
    count += 1;
  }
  return count ? total / count / 255 : 0;
}

export function getPresenceStatusLabel(
  label: string,
  requestedState: JarvisPresenceState,
  rendererMode: PresenceRendererMode,
): string {
  if (rendererMode !== 'fallback' || requestedState === 'fallback') return label;
  const separator = /[.!?]$/.test(label) ? ' ' : '. ';
  return `${label}${separator}Modo visual simplificado.`;
}

export function JarvisPresence({
  state,
  variant = 'hero',
  label,
  levelRef,
  spectrumRef,
  onFallback,
}: {
  state: JarvisPresenceState;
  variant?: PresenceVariant;
  label?: string;
  levelRef?: { current: number };
  spectrumRef?: { current: Uint8Array };
  onFallback?: (reason: PresenceFallbackReason) => void;
}) {
  const staticVariant = variant === 'compact' || variant === 'inline';
  const reduceMotion = Boolean(useReducedMotion());
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<PresenceRenderer | null>(null);
  const stateRef = useRef(state);
  const [rendererMode, setRendererMode] = useState<PresenceRendererMode>(
    staticVariant ? 'static' : 'boot',
  );

  const visibleState =
    rendererMode === 'fallback' && state === 'idle' ? 'fallback' : state;
  stateRef.current = visibleState;
  const copy = getJarvisPresenceCopy(visibleState);
  const statusLabel = label ?? copy.label;

  useEffect(() => {
    if (staticVariant) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let active = true;
    let stopLoop: () => void = () => undefined;
    const image = new Image();
    image.decoding = 'async';

    const fallBack = (reason: PresenceFallbackReason) => {
      if (!active) return;
      stopLoop();
      rendererRef.current?.dispose();
      rendererRef.current = null;
      setRendererMode('fallback');
      onFallback?.(reason);
    };
    const cancelBootFallback = schedulePresenceBootFallback(fallBack);

    image.onload = () => {
      if (!active) return;
      cancelBootFallback();
      const renderer = createWebGLPresenceRenderer(canvas, image, () => {
        fallBack('context-lost');
      });
      if (!renderer) {
        fallBack('webgl');
        return;
      }

      rendererRef.current = renderer;
      setRendererMode('webgl');
      const draw = () => {
        renderer.draw(
          performance.now() / 1000,
          levelRef?.current ?? 0,
          averageSpectrum(spectrumRef?.current),
          stateRef.current,
        );
      };
      stopLoop = createPresenceFrameLoop({ draw, reduceMotion });
    };
    image.onerror = () => fallBack('asset');
    image.src = CORE_ASSET;

    return () => {
      active = false;
      cancelBootFallback();
      stopLoop();
      image.onload = null;
      image.onerror = null;
      rendererRef.current?.dispose();
      rendererRef.current = null;
    };
  }, [levelRef, onFallback, reduceMotion, spectrumRef, staticVariant]);

  useEffect(() => {
    if (!reduceMotion) return;
    rendererRef.current?.draw(
      performance.now() / 1000,
      levelRef?.current ?? 0,
      averageSpectrum(spectrumRef?.current),
      visibleState,
    );
  }, [levelRef, reduceMotion, spectrumRef, visibleState]);

  return (
    <div
      className={`oj-presence oj-presence--${variant}`}
      data-presence={visibleState}
      data-renderer={rendererMode}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-busy={presenceNeedsProgress(visibleState)}
      style={{ '--oj-presence-color': copy.color } as React.CSSProperties}
    >
      <img
        className="oj-presence-asset"
        src={CORE_ASSET}
        alt=""
        draggable={false}
      />
      {!staticVariant && (
        <canvas ref={canvasRef} className="oj-presence-canvas" aria-hidden="true" />
      )}
      <span className="oj-presence-progress" aria-hidden="true" />
      <span
        className={
          variant === 'compact' || variant === 'inline'
            ? 'oj-presence-label'
            : 'oj-visually-hidden'
        }
      >
        {getPresenceStatusLabel(statusLabel, state, rendererMode)}
      </span>
    </div>
  );
}
