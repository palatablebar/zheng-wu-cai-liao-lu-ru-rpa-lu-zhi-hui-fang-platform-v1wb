export type Role = 'admin' | 'engineer' | 'viewer';

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
}

export interface LocatorCandidate {
  strategy: string;
  selector: string;
  desc: string;
}

export interface LocatorScore extends LocatorCandidate {
  hits: number;
  samples: number;
  hit_rate: number;
  avg_attempts: number;
  avg_attempt_score: number;
  specificity: number;
  dynamic_risk: number;
  dynamic_flags: string[];
  score: number;
  reason: string;
}

export type StepAction = 'click' | 'input' | 'upload' | 'assert' | 'wait';
export type RiskLevel = 'low' | 'medium' | 'high';

export interface BranchMeta {
  branch_id: string;
  path_id: string;
  when: string;
  merge_seq: number;
  is_default: boolean;
}

export interface BranchPath {
  path_id: string;
  entry_seq: number;
  seqs: number[];
  when: string;
  is_default: boolean;
  value?: boolean | null;
  reason?: string;
}

export interface BranchGroup {
  branch_id: string;
  merge_seq: number;
  merge_target?: string;
  paths: BranchPath[];
  evaluations?: BranchPath[];
}

export interface PreflightBlocker {
  branch_id: string;
  path_id: string;
  entry_seq: number;
  when: string;
  reason: string;
  message: string;
}

export interface PreflightResult {
  process_id: number;
  material_id: number;
  company_name: string;
  resolvable: boolean;
  can_declare: boolean;
  credit_valid: boolean;
  blockers: PreflightBlocker[];
  chosen_paths: Record<string, string>;
  merge_points: number[];
  expected_seqs: number[];
  skipped_branch_seqs: number[];
  ordered_seqs: number[];
  branch_groups: BranchGroup[];
}

export interface Reconciliation {
  expected: { seq: number; reason?: string; target_desc?: string }[];
  executed: { seq: number; status: string; note?: string; target_desc?: string }[];
  skipped: { seq: number; status: string; note?: string; target_desc?: string }[];
  unmatched: { seq: number; status: string; reason?: string; target_desc?: string }[];
  summary: {
    planned_total: number;
    expected_total: number;
    executed_total: number;
    skipped_total: number;
    unmatched_total: number;
    pending_total: number;
  };
  chosen_paths: Record<string, string>;
  merge_points: number[];
  branch_groups?: BranchGroup[];
}

export interface Step {
  id: number;
  process_id: number;
  seq: number;
  action: StepAction;
  target_desc: string;
  timeout_ms: number;
  max_retries: number;
  depends_on: number[];
  assert_rule: string;
  branch_meta: BranchMeta | Record<string, never>;
  robustness_score: number;
  risk_level: RiskLevel;
  risk_reason: string;
  locator_candidates: LocatorCandidate[];
  locator_scores: LocatorScore[];
}

export type ProcessStatus = 'draft' | 'published' | 'archived';
export type MatterType = 'annual_report' | 'qualification' | 'subsidy' | 'change';

export interface ProcessItem {
  id: number;
  name: string;
  matter_type: MatterType;
  version: number;
  status: ProcessStatus;
  step_count: number;
  success_rate: number;
  archived_versions: string[];
  owner: string;
  published_at: string | null;
  created_at?: string;
  steps?: Step[];
  branch_groups?: BranchGroup[];
  risk_summary?: { high: number; medium: number; low: number };
}

export interface Material {
  id: number;
  company_name: string;
  credit_code: string;
  contact: string;
  attachments: string[];
  validity_date: string | null;
  missing_flags: string[];
  credit_valid: boolean;
  credit_reason: string;
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'takeover';
export type RunTrigger = 'manual' | 'schedule' | 'dry_run';
export type RunStepStatus = 'ok' | 'retried' | 'failed' | 'skipped';
export type StepPathRole = 'main' | 'branch_taken' | 'branch_skipped';
export type ReconcileBucket = 'expected' | 'executed' | 'skipped' | 'unmatched';

export interface RunStepItem {
  id: number;
  run_id: number;
  step_id: number;
  seq: number;
  status: RunStepStatus;
  locator_used: string;
  locator_strategy: string;
  attempt: number;
  duration_ms: number;
  error_code: string | null;
  error_message: string;
  idempotency_key: string;
  output_digest: string;
  note: string;
  path_role: StepPathRole;
  branch_id: string;
  path_id: string;
  reconcile_bucket: ReconcileBucket;
  skip_reason: string;
  target_desc: string;
  action: string;
  assert_rule: string;
  branch_meta?: BranchMeta | Record<string, never>;
}

export interface InterventionItem {
  id: number;
  code: string;
  run_id: number;
  run_step_id: number;
  reason: string;
  status: string;
  detail: string;
  action: string;
  resolution: string;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string | null;
  run_code: string;
  run_status: string;
  process_name: string;
  company_name: string;
  step_seq: number | null;
  step_target: string;
}

export interface RunItem {
  id: number;
  run_code: string;
  process_id: number;
  process_version: number;
  material_id: number;
  robot_name: string;
  trigger: RunTrigger;
  status: RunStatus;
  started_at: string | null;
  ended_at: string | null;
  total_steps: number;
  success_steps: number;
  retry_count: number;
  attempt_count: number;
  execution_sequence: number[];
  skipped_steps: number[];
  branch_plan: {
    ordered_seqs?: number[];
    expected_seqs?: number[];
    skipped_branch_seqs?: number[];
    chosen_paths?: Record<string, string>;
    merge_points?: number[];
    unknown_conditions?: PreflightBlocker[];
    warnings?: string[];
  };
  preflight_blockers: PreflightBlocker[];
  log_text: string;
  created_at: string;
  process_name?: string;
  company_name?: string;
  credit_code?: string;
  credit_valid?: boolean;
  matter_type?: string;
  run_steps?: RunStepItem[];
  interventions?: InterventionItem[];
  reconciliation?: Reconciliation;
  progress?: { total: number; done: number; steps: BoardStep[] };
}

export interface BoardStep {
  seq: number;
  status: RunStepStatus;
  attempt: number;
  duration_ms: number;
  target_desc: string;
  action: string;
  locator_used: string;
  error_code: string | null;
}

export interface BoardSnapshot {
  robot: { name: string; station: string; poll_seconds: number };
  stats: {
    total: number;
    succeeded: number;
    failed: number;
    takeover: number;
    running: number;
    queued: number;
  };
  queue: RunItem[];
  active: RunItem[];
  recent_failures: RunItem[];
}

export interface ApiErrorShape {
  code: string;
  message: string;
  detail?: string | null;
}
