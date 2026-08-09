export interface Project {
  id: number;
  name: string;
  code: string;
  client_name?: string;
  estimated_hours?: number | null;
  actual_hours?: number;
  priority: number;
  status: string;
  manager_id?: number | null;
  manager_name?: string;
  start_date?: string;
  end_date?: string;
  project_kind?: 'project' | 'detection';
  tasks: Task[];
}

export interface DetectionTask {
  id: number;
  project_id: number;
  code: string;
  name: string;
  client_name?: string | null;
  priority: number;
  manager_id?: number | null;
  manager_name?: string | null;
  start_date: string;
  end_date: string;
  task: Task;
  actual_hours: number;
  schedule_status?: string | null;
  schedule_message?: string | null;
  preview_token?: string | null;
}

export interface Task {
  id: number;
  project_id: number;
  name: string;
  task_type: string;
  requires_instrument: boolean;
  requires_human: boolean;
  est_duration_hours?: number;
  switchover_hours: number;
  status: string;
  delay_status: 'delayed' | 'not_delayed';
  schedule_dirty: boolean;
  schedule_lock_status: 'none' | 'frozen' | 'running' | 'completed';
  can_edit_schedule_fields: boolean;
  can_edit_basic_fields: boolean;
  can_edit_schedule_window: boolean;
  can_edit_resource_fields: boolean;
  earliest_start?: string;
  latest_due?: string;
  priority_weight: number;
  allow_split?: boolean;
  instrument_ids: number[];
  predecessor_ids: number[];
  assignee_id: number | null;
  assignee_name: string | null;
  parent_id: number | null;
  plan_order?: number;
  children?: Task[];
  is_external_gate?: boolean;
  gate_status?: ApprovalGateStatus;
  expected_approval_at?: string | null;
  submitted_at?: string | null;
  approved_at?: string | null;
  approved_by_name?: string | null;
  approval_note?: string | null;
  approval_schedule_status?: string | null;
  is_local_draft?: boolean;
}

export type ApprovalGateStatus = 'not_submitted' | 'waiting_approval' | 'approved';
export type ApprovalRiskStatus = 'normal' | 'upcoming' | 'overdue' | 'deadline_risk';

export interface ApprovalGateTaskRef {
  id: number;
  name: string;
  status?: string | null;
  completed_at?: string | null;
}

export interface ApprovalGate {
  id: number;
  project_id: number;
  project_code: string;
  project_name: string;
  client_name?: string | null;
  project_manager_id?: number | null;
  project_manager_name?: string | null;
  assignee_id?: number | null;
  assignee_name?: string | null;
  project_end_date?: string | null;
  name: string;
  gate_status: ApprovalGateStatus;
  expected_approval_at?: string | null;
  submitted_at?: string | null;
  approved_at?: string | null;
  approved_by_name?: string | null;
  approval_note?: string | null;
  predecessor_tasks: ApprovalGateTaskRef[];
  unlock_tasks: ApprovalGateTaskRef[];
  latest_approval_at?: string | null;
  risk_status: ApprovalRiskStatus;
  schedule_status?: string | null;
  schedule_message?: string | null;
  schedule_run_id?: string | null;
  preview_token?: string | null;
  moved_tasks: number;
  project_expected_completion?: string | null;
  can_operate: boolean;
}

export interface ApprovalGateList {
  items: ApprovalGate[];
  total: number;
  page: number;
  page_size: number;
  pending_count: number;
  approved_count: number;
  upcoming_count: number;
  overdue_count: number;
}

export interface ApprovalGateAction {
  gate: ApprovalGate;
  schedule_status: string;
  schedule_message?: string | null;
  preview_token?: string | null;
}

export interface StandardPlanTask {
  id: number;
  name: string;
  task_type: string;
  percentage?: number | null;
  estimated_hours?: number | null;
  is_approval_restriction: boolean;
}

export interface StandardPlanImportResult {
  status: string;
  message: string;
  project_id: number;
  estimated_hours: number;
  tasks: StandardPlanTask[];
}

export interface CapabilityReq {
  id: number;
  tag_name: string;
  tag_value: string;
}

export interface Instrument {
  id: number;
  code: string;
  name: string;
  instrument_group: string;
  brand?: string;
  model?: string;
  location?: string;
  availability_status: 'available' | 'unavailable';
  status: string;
  buffer_rate: number;
  switchover_base_hours: number;
  capabilities: CapabilityReq[];
}

export interface InstrumentFault {
  id: number;
  instrument_id: number;
  reported_at: string;
  estimated_resolved_at: string | null;
  resolved_at: string | null;
  description: string;
  status: string;
  schedule_impact?: {
    shifted_slots: number;
    affected_tasks: number;
    notified_users: number;
  };
  affected_tasks?: FaultAffectedTask[];
}

export interface FaultAffectedTask {
  task_id: number;
  task_name: string;
  project_id: number | null;
  project_name: string | null;
  project_code: string | null;
  assignee_name: string | null;
  original_start: string;
  original_end: string;
  shifted_start: string;
  shifted_end: string;
  can_shift: boolean;
  reason: string;
}

export interface TimeSlot {
  id: number;
  task_id: number;
  instrument_id: number;
  plan_start: string;
  plan_end: string;
  actual_start?: string;
  actual_end?: string;
  tier: string;
  status: string;
  execution_status?: string;
  is_night_run?: boolean;
  task_name?: string;
  task_type?: string | null;
  task_status?: string | null;
  delay_status?: 'delayed' | 'not_delayed';
  project_code?: string | null;
  project_name?: string;
  instrument_name?: string;
  instrument_code?: string;
  assignee_id: number | null;
  assignee_name?: string;
  project_id?: number | null;
  delay_hours?: number | null;
  delay_reason?: string | null;
  delay_reported_at?: string | null;
  delay_started_at?: string | null;
  approval_gate_status?: ApprovalGateStatus;
  approval_risk_status?: ApprovalRiskStatus;
  approval_latest_at?: string | null;
  approval_unlock_tasks?: ApprovalGateTaskRef[];
}

export interface DashboardData {
  total_instruments: number;
  active_instruments: number;
  total_projects: number;
  active_projects: number;
  avg_utilization: number;
  delayed_tasks: number;
  buffer_warnings: string[];
  milestone_risks: { project: string; milestone: string; due_date: string }[];
}

export interface UtilizationStats {
  instrument_id: number;
  instrument_name: string;
  instrument_code?: string | null;
  total_available_hours: number;
  scheduled_hours: number;
  actual_run_hours: number;
  expected_utilization_rate: number;
  actual_utilization_rate: number;
  utilization_rate: number;
  buffer_consumed_rate: number;
}

export interface DAGData {
  nodes: { id: number; name: string; type: string; requires_instrument: boolean; status: string; is_external_gate?: boolean; gate_status?: ApprovalGateStatus }[];
  edges: { from: number; to: number }[];
}

export interface InsertOrderImpact {
  task_id: number;
  task_name: string;
  project_id: number;
  project_name: string;
  is_insert_task: boolean;
  original_start: string | null;
  original_end: string | null;
  new_start: string;
  new_end: string;
  delay_hours: number;
  impact_role?: 'inserted' | 'anchor_downstream' | 'source_downstream' | 'shifted' | null;
}

export interface ProjectScheduleImpact {
  project_id: number;
  project_code: string;
  project_name: string;
  project_end_date: string | null;
  original_start: string | null;
  new_start: string | null;
  original_completion: string | null;
  new_completion: string | null;
  delay_hours: number;
  exceeds_end_date: boolean;
  overdue_hours: number;
}

export type ProjectPlanApplyStatus = 'applied' | 'no_changes' | 'insert_confirmation_required' | 'error';

export interface ProjectPlanApplyResult {
  status: ProjectPlanApplyStatus;
  message?: string;
  project_id: number;
  schedule_run_id?: string | null;
  timeslots_created: number;
  moved_tasks: number;
  conflicts_checked: boolean;
  preview_token?: string | null;
  impacts: InsertOrderImpact[];
  project_impacts: ProjectScheduleImpact[];
}

export interface InsertCost {
  status: string;
  schedule_run_id: string;
  timeslots_created: number;
  total_delay_hours: number;
  impacts: InsertOrderImpact[];
}

export interface InsertOrderResult extends InsertCost {
  moved_tasks: number;
  conflicts_checked: boolean;
}
