/**
 * Types mirroring the Life API (`/v1/life/*`).
 *
 * Money is always integer cents — the backend never sends floats for currency,
 * and neither should anything here. Format at the edge with `formatMoney`.
 */

export type AppId =
  | 'finance'
  | 'fitness'
  | 'routine'
  | 'family'
  | 'work'
  | 'health';

/**
 * Apps abríveis pelo shell. `connections` não é um domínio de dados (não tem
 * tabela nem badge do backend) — é a central de infraestrutura, então vive
 * fora de `AppId` para não mentir no tipo de `Badges`/`Alert`.
 */
export type ShellAppId = AppId | 'connections';

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
  health: HealthSummary;
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

export interface FinancialCandidate {
  kind: 'expense' | 'income';
  amount_cents: number;
  description: string;
  category: string;
  occurred_on: string;
  confidence: number;
}

export interface FinancialDocument extends BaseRecord {
  filename: string;
  content_type: string;
  sha256: string;
  document_kind: 'receipt' | 'statement' | 'unknown';
  status: 'review_required';
  analysis: {
    document_kind: 'receipt' | 'statement' | 'unknown';
    candidates: FinancialCandidate[];
  };
  proposal_ids: string[];
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

export type TrainingGoal =
  | 'general_fitness'
  | 'endurance'
  | 'performance'
  | 'technique'
  | 'strength'
  | 'hypertrophy'
  | 'mobility'
  | '5k'
  | '10k'
  | 'half_marathon';
export type TrainingLevel = 'beginner' | 'intermediate' | 'advanced';
export type TrainingSport =
  | 'running'
  | 'canoeing'
  | 'cycling'
  | 'swimming'
  | 'strength'
  | 'mobility'
  | 'functional'
  | 'walking'
  | 'hiking'
  | 'rowing';

export interface TrainingProfile extends BaseRecord {
  primary_sport: TrainingSport;
  secondary_sports: TrainingSport[];
  primary_goal: TrainingGoal;
  target_distance_km: number;
  target_date: string | null;
  level: TrainingLevel;
  weekly_days: number;
  available_weekdays: number[];
  session_minutes: number;
  current_weekly_km: number;
  longest_recent_run_km: number;
  equipment: string[];
  limitations: string;
}

export type TrainingProfileInput = Omit<
  TrainingProfile,
  keyof BaseRecord | 'target_date'
> & { target_date: string | null };

export interface TrainingPlan extends BaseRecord {
  profile_id: string;
  name: string;
  primary_sport: TrainingSport;
  goal: TrainingGoal;
  start_on: string;
  end_on: string;
  weeks: number;
  current_week: number;
  status: 'active' | 'replaced' | 'completed';
  source: string;
}

export interface TrainingSession extends BaseRecord {
  plan_id: string;
  workout_id: string;
  scheduled_on: string;
  week_index: number;
  day_index: number;
  title: string;
  sport: TrainingSport;
  session_type: 'easy' | 'quality' | 'strength' | 'recovery' | 'long';
  objective: string;
  rationale: string;
  estimated_min: number;
  target_rpe: number;
  status: 'planned' | 'completed' | 'canceled';
  adaptation_note: string;
}

export interface TrainingStep extends BaseRecord {
  session_id: string;
  step_index: number;
  kind: string;
  title: string;
  instructions: string;
  duration_sec: number;
  distance_m: number;
  target_pace_min_km: number;
  target_rpe: number;
  sets: number;
  reps: number;
  rest_sec: number;
  alternative: string;
}

export interface TrainingCheckin extends BaseRecord {
  session_id: string;
  observed_at: string;
  sleep_quality: number;
  soreness: number;
  stress: number;
  motivation: number;
  pain: number;
  readiness_score: number;
  recommendation:
    | 'ready'
    | 'reduce_load'
    | 'recovery_only'
    | 'stop_and_seek_care';
  notes: string;
}

export interface TrainingFeedback extends BaseRecord {
  session_id: string;
  completed_at: string;
  completion_pct: number;
  actual_duration_min: number;
  rpe: number;
  energy: number;
  pain: number;
  notes: string;
  adaptation: {
    reason?: string;
    factor?: number;
    message?: string;
    sessions?: Array<Record<string, unknown>>;
  };
}

export interface CoachOverview {
  profile: TrainingProfile | null;
  active_plan: TrainingPlan | null;
  sessions: TrainingSession[];
  next_session: TrainingSession | null;
}

export interface CoachSessionDetail {
  session: TrainingSession;
  steps: TrainingStep[];
  checkin: TrainingCheckin | null;
  feedback: TrainingFeedback | null;
  workout: Workout | null;
}

export interface TrainingCheckinInput {
  sleep_quality: number;
  soreness: number;
  stress: number;
  motivation: number;
  pain: number;
  notes: string;
}

export interface TrainingCompletionInput {
  completion_pct: number;
  actual_duration_min: number;
  rpe: number;
  energy: number;
  pain: number;
  notes: string;
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

export interface HealthProfile extends BaseRecord {
  birth_date: string | null;
  sex_at_birth: string;
  height_cm: number;
  blood_type: string;
  goals: string;
  emergency_contact: string;
  consent_health_memory: number;
}

export interface HealthCondition extends BaseRecord {
  name: string;
  status: string;
  diagnosed_on: string | null;
  notes: string;
  source: string;
  confirmed_at: string | null;
}

export interface Medication extends BaseRecord {
  name: string;
  dose_text: string;
  frequency: string;
  started_on: string | null;
  ended_on: string | null;
  status: string;
  notes: string;
  source: string;
  confirmed_at: string | null;
}

export interface Allergy extends BaseRecord {
  substance: string;
  reaction: string;
  severity: string;
  notes: string;
  source: string;
  confirmed_at: string | null;
}

export interface HealthObservation extends BaseRecord {
  kind: string;
  value: number;
  unit: string;
  observed_at: string;
  source: string;
  notes: string;
}

export interface HydrationLog extends BaseRecord {
  amount_ml: number;
  occurred_at: string;
  source: string;
}

export interface NutritionLog extends BaseRecord {
  meal_type: string;
  description: string;
  occurred_at: string;
  source: string;
}

export interface HealthDocument extends BaseRecord {
  name: string;
  kind: string;
  document_date: string | null;
  provider: string;
  status: string;
  notes: string;
  source: string;
}

export interface HealthSummary {
  profile: HealthProfile | null;
  active_conditions: HealthCondition[];
  active_medications: Medication[];
  allergies: Allergy[];
  hydration_today_ml: number;
  nutrition_today_count: number;
  latest_observations: HealthObservation[];
  documents: HealthDocument[];
}

// -- Integrações (`/v1/life/integrations`) -----------------------------------

/** Camada 1 do estado: o que este deployment pode oferecer de verdade. */
export type IntegrationAvailability =
  | 'available'
  | 'needs_setup'
  | 'device_only'
  | 'coming_soon';

/**
 * Camada 2: a conexão deste usuário. `pending` não existe aqui — aguardar
 * autorização é um `pending_auth` separado, nunca um status de conexão.
 */
export type IntegrationConnectionStatus =
  | 'connected'
  | 'error'
  | 'expired'
  | 'revoked';

export interface IntegrationConnection {
  status: IntegrationConnectionStatus;
  account_label: string;
  granted_scopes: string[];
  connected_at: string | null;
  last_sync_at: string | null;
  last_sync_status: 'ok' | 'error' | '';
  last_error: string;
  revoked_at: string | null;
  updated_at: string;
  /** O backend nunca envia a credencial — apenas se existe uma no cofre. */
  has_credential: boolean;
}

export interface IntegrationProvider {
  id: string;
  label: string;
  category: string;
  description: string;
  capabilities: string[];
  scopes: string[];
  auth: { kind: 'oauth' | 'device' | 'none'; pkce: boolean };
  availability: IntegrationAvailability;
  missing_config: string[];
  prerequisites: string[];
  icon: string;
  tint: string;
  connection: IntegrationConnection | null;
  pending_auth: { expires_at: string } | null;
}

export interface IntegrationsSummary {
  connected: number;
  attention: number;
  pending: number;
}

export interface IntegrationsOverview {
  providers: IntegrationProvider[];
  summary: IntegrationsSummary;
}

export interface WhatsAppChannelStatus {
  id?: string;
  channel?: 'whatsapp';
  status: 'disconnected' | 'pending' | 'verified';
  verified_at?: string;
}

export interface WhatsAppLinkPending {
  status: 'pending';
  expires_at: string;
}

export type WhatsAppBriefingSection =
  | 'priorities'
  | 'finance'
  | 'fitness'
  | 'routine'
  | 'family'
  | 'work'
  | 'health'
  | 'calendar'
  | 'email'
  | 'news';

export interface WhatsAppBriefingPreference {
  enabled: boolean;
  time: string;
  sections: WhatsAppBriefingSection[];
  news_topics: string[];
  /** Monday = 0, matching Python's local weekday contract. */
  delivery_days: number[];
  custom_instructions: string;
}
