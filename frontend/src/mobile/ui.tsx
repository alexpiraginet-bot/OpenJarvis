/** Shared presentation primitives for the mobile shell. */

import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';

// -- Formatting --------------------------------------------------------------

/** Render integer cents as currency. The API never sends floats for money. */
export function formatMoney(cents: number, currency = 'BRL'): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

/** Parse a typed amount ("45,90" or "45.90") into integer cents. */
export function parseMoney(input: string): number {
  const normalized = input.replace(/[^\d,.-]/g, '').replace(',', '.');
  const value = Number.parseFloat(normalized);
  if (!Number.isFinite(value)) return 0;
  // Round rather than truncate: 45.899999 must not become R$45,89.
  return Math.round(value * 100);
}

/** "10/08" — compact date for list rows. */
export function formatShortDate(iso: string | null): string {
  if (!iso) return '';
  const [year, month, day] = iso.slice(0, 10).split('-');
  if (!year || !month || !day) return iso;
  return `${day}/${month}`;
}

/** "segunda-feira, 10 de agosto" — the springboard header. */
export function formatLongDate(iso: string): string {
  const parsed = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString('pt-BR', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });
}

/** Today as an ISO date, in the browser's own timezone. */
export function todayIso(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

// -- Components --------------------------------------------------------------

export function Card({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <div className="oj-card">
      {label && <div className="oj-card-label">{label}</div>}
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  tone,
  small,
}: {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'warn';
  small?: boolean;
}) {
  const toneClass = tone ? ` oj-${tone}` : '';
  return (
    <div className="oj-card">
      <div className="oj-card-label">{label}</div>
      <div className={(small ? 'oj-stat--sm' : 'oj-stat') + toneClass}>{value}</div>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <div className="oj-section-title">{title}</div>
      {children}
    </>
  );
}

export function ListGroup({ children }: { children: ReactNode }) {
  return <div className="oj-list">{children}</div>;
}

export function Row({
  title,
  sub,
  value,
  valueTone,
  leading,
  trailing,
  onClick,
}: {
  title: string;
  sub?: string;
  value?: string;
  valueTone?: 'pos' | 'neg' | 'warn';
  leading?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
}) {
  const content = (
    <>
      {leading}
      <div className="oj-row-body">
        <div className="oj-row-title">{title}</div>
        {sub && <div className="oj-row-sub">{sub}</div>}
      </div>
      {value && (
        <div className={'oj-row-value' + (valueTone ? ` oj-${valueTone}` : '')}>
          {value}
        </div>
      )}
      {trailing}
    </>
  );
  // A row that does something must be a button, so it is reachable by keyboard
  // and announced as actionable rather than as decorative text.
  if (onClick) {
    return (
      <button type="button" className="oj-row oj-row--tappable" onClick={onClick}>
        {content}
      </button>
    );
  }
  return <div className="oj-row">{content}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="oj-empty">{children}</div>;
}

export function Spinner() {
  return <div className="oj-spinner" role="status" aria-label="Carregando" />;
}

export function ProgressBar({ pct, tone }: { pct: number; tone?: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const color =
    tone ?? (clamped >= 100 ? 'var(--oj-critical)' : clamped >= 80 ? 'var(--oj-warning)' : 'var(--oj-ok)');
  return (
    <div className="oj-bar">
      <div className="oj-bar-fill" style={{ width: `${clamped}%`, background: color }} />
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant,
  size,
  disabled,
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'ghost';
  size?: 'sm';
  disabled?: boolean;
  type?: 'button' | 'submit';
}) {
  const classes = [
    'oj-btn',
    variant === 'ghost' ? 'oj-btn--ghost' : '',
    size === 'sm' ? 'oj-btn--sm' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button type={type} className={classes} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function Field({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
}) {
  return (
    <div className="oj-field">
      <label className="oj-label">
        {label}
        {options ? (
          <select
            className="oj-input"
            value={value}
            onChange={(event) => onChange(event.target.value)}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="oj-input"
            type={type}
            value={value}
            placeholder={placeholder}
            onChange={(event) => onChange(event.target.value)}
          />
        )}
      </label>
    </div>
  );
}

/** Bottom sheet used for every "add" form. Closes on backdrop tap or Escape. */
export function Sheet({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="oj-sheet-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="oj-sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="oj-grabber" />
        <div className="oj-sheet-title">{title}</div>
        {children}
      </div>
    </div>
  );
}

/**
 * Load data on mount, with a `reload` handle for after a mutation.
 *
 * A dedicated fetching library would be overkill for a shell this size, but
 * hand-rolled `useEffect` fetches that forget the unmount guard leak state
 * updates into unmounted screens — so that guard lives here, once.
 */
export function useLoader<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
): { data: T | null; error: string; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loader()
      .then((result) => {
        if (active) {
          setData(result);
          setError('');
        }
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : 'Falha ao carregar');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
