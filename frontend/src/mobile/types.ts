/**
 * Types mirroring the Life API (`/v1/life/*`).
 *
 * Money is always integer cents — the backend never sends floats for currency,
 * and neither should anything here. Format at the edge with `formatMoney`.
 */

export type AppId = 'finance' | 'fitness' | 'routine' | 'family' | 'work';

export type Severity = 'critical' | 'warning' | 'info';

export interface LifeUser {
  id: string;
  email: string;
  name: string;
  timezone: string;
  currency: string;
  locale: string;
  created_at: string;
}

export interface AppManifestEntry {
  id: AppId;
  label: string;
  icon: string;
  tint: string;
  description: string;
}

export type Badges = Record<AppId, number>;

export interface Alert {
  severity: Severity;
  app: AppId;
  record_id: string;
  title: string;
  detail: string;
  amount_cents: number;
  action: string;
}

export interface Today {
  date: string;
  greeting: string;
  currency: string;
  alerts: Alert[];
  badges: Badges;
  finance: {
    balance_cents: number;
    net_cents: number;
    expense_cents: number;
    income_cents: number;
    overdue_count: number;
    due_soon_count: number;
  };
  fitness: {
    week_completed: number;
    week_planned: number;
    days_since_last: number | null;
    todays_workout: Workout | null;
  };
  routine: { completed: number; total: number; pending: string[] };
  family: { upcoming: FamilyUpcoming[] };
  work: { open_count: number; overdue_count: number; due_today_count: number };
}

/** Every record carries these; the rest varies by table. */
export interface BaseRecord {
  id: string;
  user_id: string;
  created_at: string;
}

export interface Account extends BaseRecord {
  name: string;
  kind: string;
  balance_cents: number;
  currency: string;
  institution: string;
  archived: number;
}

export interface Transaction extends BaseRecord {
  account_id: string;
  amount_cents: number;
  kind: 'income' | 'expense';
  category: string;
  description: string;
  occurred_on: string;
  source: string;
}

export interface Bill extends BaseRecord {
  name: string;
  amount_cents: number;
  due_on: string;
  recurrence: 'none' | 'weekly' | 'monthly' | 'yearly';
  status: 'pending' | 'paid' | 'overdue';
  category: string;
  paid_on: string | null;
  autopay: number;
}

export interface Goal extends BaseRecord {
  name: string;
  target_cents: number;
  saved_cents: number;
  target_date: string | null;
  icon: string;
}

export interface BudgetStatus {
  id: string;
  category: string;
  limit_cents: number;
  spent_cents: number;
  remaining_cents: number;
  pct_used: number;
}

export interface FinanceSummary {
  month: string;
  balance_cents: number;
  income_cents: number;
  expense_cents: number;
  net_cents: number;
  by_category: Array<{ label: string; total: number }>;
  budgets: BudgetStatus[];
}

export interface Workout extends BaseRecord {
  name: string;
  scheduled_on: string;
  completed_at: string | null;
  duration_min: number;
  focus: string;
  notes: string;
  source: string;
}

export interface ExerciseSet extends BaseRecord {
  workout_id: string;
  exercise: string;
  set_index: number;
  reps: number;
  weight_kg: number;
}

export interface Measurement extends BaseRecord {
  taken_on: string;
  weight_kg: number;
  body_fat_pct: number;
  waist_cm: number;
  resting_hr: number;
  sleep_hours: number;
}

export interface FitnessSummary {
  week_planned: number;
  week_completed: number;
  week_minutes: number;
  week_volume_kg: number;
  days_since_last: number | null;
  personal_records: Array<{ exercise: string; weight_kg: number; reps: number }>;
  latest_measurement: Measurement | null;
}

export interface Habit extends BaseRecord {
  name: string;
  cadence: string;
  target_per_period: number;
  icon: string;
  color: string;
  archived: number;
  done_today: boolean;
  streak: number;
}

export interface RoutineSummary {
  date: string;
  habits: Habit[];
  completed: number;
  total: number;
}

export interface FamilyMember extends BaseRecord {
  name: string;
  relation: string;
  birthday: string | null;
  phone: string;
  notes: string;
  avatar: string;
}

export interface FamilyUpcoming {
  kind: string;
  title: string;
  date: string;
  days_away: number;
  member_id: string;
  member_name: string;
  turning: number;
}

export interface Project extends BaseRecord {
  name: string;
  status: string;
  due_on: string | null;
  color: string;
  notes: string;
}

export interface WorkTask extends BaseRecord {
  project_id: string;
  title: string;
  status: 'todo' | 'doing' | 'done';
  priority: string;
  due_on: string | null;
  done_at: string | null;
  notes: string;
}

export interface WorkSummary {
  open_count: number;
  overdue: WorkTask[];
  due_today: WorkTask[];
  projects: Project[];
}
