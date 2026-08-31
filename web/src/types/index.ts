export interface Project {
  id: number;
  name: string;
  code: string;
  client_name?: string;
  estimated_hours?: number | null;
  actual_hours?: number;
  priority: number;
  status: string;
  delivery_status?: 'on_time' | 'at_risk' | 'overdue' | null;
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
  project_impacts: ProjectScheduleImpact[];
}

export interface ProjectHoursTask {
  task_id: number;
  parent_id: number | null;
  task_name: string;
  top_level_task_name: string;
  assignee_name: string | null;
  status: string;
  depth: number;
  planned_hours: number;
  actual_hours: number;
  instrument_codes: string[];
  planned_start: string | null;
  planned_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  schedule_judgement: string;
  delay_hours: number;
  /** 该任务全部未作废夜跑时间槽的时长合计，父任务按子任务汇总。 */
  night_run_hours: number;
  pause_count: number;
  pause_reasons: string[];
}

export interface ProjectHoursItem {
  project_id: number;
  project_code: string;
  project_name: string;
  client_name: string | null;
  manager_name: string | null;
  start_date: string | null;
  end_date: string | null;
  project_status: string;
  task_count: number;
  planned_hours: number;
  actual_hours: number;
  variance_hours: number;
  tasks: ProjectHoursTask[];
}

export interface ProjectHoursReport {
  generated_at: string;
  project_count: number;
  planned_hours: number;
  actual_hours: number;
  items: ProjectHoursItem[];
}

export interface Task {
  id: number;
  project_id: number;
  name: string;
  task_type: string;
  requires_instrument: boolean;
  requires_human: boolean;
  est_duration_hours?: number;
  actual_hours?: number;
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

/** 待方案签批工时在仪器时间轴上的预测段。每个项目单独一段，不合并。 */
export interface PendingApprovalSegment {
  instrument_id: number;
  project_id: number;
  project_code: string;
  project_name: string;
  task_id: number;
  task_name: string;
  hours: number;
  /** 同一任务按工作日切成多段，段序号用于区分。 */
  segment_index: number;
  plan_start: string;
  plan_end: string;
}

export interface ApprovalGateTaskRef {
  id: number;
  name: string;
  status?: string | null;
  completed_at?: string | null;
  // 签批通过前下游任务不落地时间槽，甘特图据此在已排时间块之后列出待排工时。
  est_duration_hours?: number | null;
  requires_instrument?: boolean;
  instrument_ids?: number[];
  is_scheduled?: boolean;
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
  top_level_task_name?: string | null;
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
  switchover_base_hours: number;
  effective_work_start: string;
  effective_work_end: string;
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
    risk_tasks?: number;
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
  instrument_id: number | null;
  plan_start: string;
  plan_end: string;
  actual_start?: string;
  actual_end?: string;
  task_actual_start?: string | null;
  task_actual_end?: string | null;
  tier: string;
  status: string;
  execution_status?: string;
  is_night_run?: boolean;
  /** 后端按工作日历切好的显示分段：周末与每日工作时段之外不出现色块。 */
  display_spans?: [string, string][];
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

export interface InstrumentBridgeReservation {
  id: number;
  kind: 'human_bridge_reservation' | 'historical_human_bridge';
  schedule_run_id: string;
  task_id: number;
  instrument_id: number;
  previous_task_id: number;
  following_task_id: number;
  plan_start: string;
  plan_end: string;
  task_name: string;
  task_type?: string | null;
  project_id?: number | null;
  project_code?: string | null;
  project_name?: string | null;
  assignee_id?: number | null;
  assignee_name?: string | null;
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
  pending_approval_hours: number;
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
  schedule_failure?: ScheduleFailureDiagnostic | null;
}

export interface ScheduleRecommendationJob {
  id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'stale';
  poll_after_ms?: number;
  recommendation?: ScheduleFailureRecommendation | null;
  recommendations?: ScheduleFailureRecommendation[];
  elapsed_seconds?: number | null;
  message?: string;
}

export interface ScheduleFailureDetail {
  project_id: number;
  project_label: string;
  instrument_label: string;
  scheduled_hours: number;
  forecast_hours: number;
  waiting_hours: number;
  total_hours: number;
}

export interface ScheduleFailureGroup {
  top_level_task_id: number;
  top_level_task_name: string;
  instrument_id: number;
  instrument_label: string;
  deadline: string;
  available_hours: number;
  occupied_hours: number;
  remaining_hours: number;
  required_hours: number;
  deficit_hours: number;
  details: ScheduleFailureDetail[];
}

export interface ScheduleFailureDiagnostic {
  title: string;
  kind: 'instrument_capacity' | 'scheduling_constraints';
  summary: string;
  deadline?: string;
  groups: ScheduleFailureGroup[];
  project_id?: number;
  project_label?: string;
  days_remaining?: number;
  instruments?: ScheduleFailureInstrument[];
  occupancy?: ScheduleFailureOccupancy[];
  recommendations?: ScheduleFailureRecommendation[];
  recommendation_job?: { id: string; status: string; poll_after_ms?: number } | null;
  window?: ScheduleFailureWindow;
}

export interface ScheduleFailureWindow {
  task_name: string;
  earliest_start: string;
  deadline: string;
  required_hours: number;
  available_hours: number;
}

export interface ScheduleFailureInstrument {
  instrument_id: number;
  instrument_label: string;
  available_hours: number;
  occupied_hours: number;
  remaining_hours: number;
  required_hours: number;
  deficit_hours: number;
}

export interface ScheduleFailureOccupancy {
  instrument_id: number;
  instrument_label: string;
  project_id: number;
  project_label: string;
  scheduled_hours: number;
  bridged_hours: number;
  forecast_hours: number;
  total_hours: number;
}

export interface ScheduleFailureRecommendation {
  code: string;
  kind: string;
  title: string;
  description: string;
  instrument_id?: number;
  project_id?: number;
  hours?: number;
  verified: boolean;
  verification: string;
  projects?: number[];
  changes?: ScheduleFailureDeadlineChange[];
}

export interface ScheduleFailureDeadlineChange {
  project_id: number;
  project_label: string;
  original_deadline: string;
  suggested_deadline: string;
  delay_days: number;
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
