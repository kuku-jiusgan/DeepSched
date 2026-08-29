import api from './http'
import type {
  PendingApprovalSegment,
  Project, Instrument, TimeSlot, InstrumentBridgeReservation, DashboardData, UtilizationStats, ProjectPlanApplyResult,
  DAGData, InsertCost, InsertOrderResult, Task, CapabilityReq, InstrumentFault,
  ApprovalGate, ApprovalGateAction, ApprovalGateList,
  StandardPlanImportResult,
  DetectionTask,
  ProjectHoursReport,
} from '@/types';

export type {
  Project, Instrument, TimeSlot, DashboardData, UtilizationStats,
  DAGData, InsertCost, Task, CapabilityReq, InstrumentFault,
  ApprovalGate, ApprovalGateAction, ApprovalGateList,
  StandardPlanImportResult,
  DetectionTask,
}


// Projects
export const getProjects = (status?: 'active' | 'pending' | 'completed'): Promise<Project[]> =>
  api.get<Project[]>('/projects', { params: status ? { status } : undefined }).then(r => r.data);

export const createProject = (data: Partial<Project>): Promise<Project> =>
  api.post<Project>('/projects', data).then(r => r.data);

export const updateProject = (id: number, data: Partial<Project>): Promise<Project> =>
  api.put<Project>(`/projects/${id}`, data).then(r => r.data);

export const getProject = (id: number): Promise<Project> =>
  api.get<Project>(`/projects/${id}`).then(r => r.data);

export interface ProjectHealthTask { task_id: number; task_name: string; status: string; plan_start: string | null; plan_end: string | null; actual_start: string | null; actual_end: string | null; delay_days: number; delay_reason: string | null; assignee_name: string | null }
export interface ProjectHealthBlocker extends ProjectHealthTask { blocker_type: 'delayed' | 'unscheduled' | 'waiting_external' }
export interface ProjectHealthPoint { date: string; ideal: number; actual: number; forecast: number }
export interface ProjectHealthAnnotation { date: string; title: string; detail: string; task_id: number | null }
export interface ProjectHealthTimelineTask { task_id: number; task_name: string; status: string; plan_start: string | null; plan_end: string | null; actual_start: string | null; actual_end: string | null; assignee_name: string | null; is_external_gate: boolean; expected_approval_at: string | null }
export interface ProjectArrangementItem {
  slot_id: number | null; task_id: number; task_name: string; top_level_task_name: string | null; plan_order: number
  task_status: string; slot_status: string | null; delay_status: string; assignee_id: number | null; assignee_name: string | null
  instrument_id: number | null; instrument_code: string | null; instrument_name: string | null
  plan_start: string | null; plan_end: string | null; actual_start: string | null; actual_end: string | null
  is_external_gate: boolean; expected_approval_at: string | null
}
export interface ProjectHealth {
  project_id: number; project_code: string; project_name: string; client_name: string | null; manager_name: string | null; start_date: string | null; end_date: string | null
  summary: { project_status: string; health_score: number; health_level: 'green' | 'yellow' | 'red'; delivery_status: 'on_time' | 'at_risk' | 'overdue'; due_date: string | null; predicted_end: string | null; days_delta: number; schedule_state: 'not_scheduled' | 'scheduled' | 'dirty' | 'executing' | 'completed'; metric_mode: 'estimated_hours' | 'task_count'; task_counts: Record<string, number> }
  due_this_week_open: ProjectHealthTask[]; delayed_over_three_days: ProjectHealthTask[]; blockers: ProjectHealthBlocker[]
  timeline: { total_value: number; points: ProjectHealthPoint[]; annotations: ProjectHealthAnnotation[]; tasks: ProjectHealthTimelineTask[] }
  arrangement_items: ProjectArrangementItem[]
}
export const getProjectHealth = (id: number): Promise<ProjectHealth> => api.get<ProjectHealth>(`/projects/${id}/health`).then(r => r.data)

export type ProjectDeliveryStatus = 'on_time' | 'at_risk' | 'overdue'
export interface ProjectProgressOverview {
  project_id: number; project_code: string; project_name: string; client_name: string | null; manager_name: string | null
  project_status: string; delivery_status: ProjectDeliveryStatus; health_level: 'green' | 'yellow' | 'red'
  plan_start: string | null; plan_end: string | null; actual_start: string | null; actual_end: string | null
  actual_started_at: string | null; due_date: string | null; predicted_end: string | null; days_delta: number
  completed_tasks: number; total_tasks: number
}
export interface ProjectProgressList { generated_at: string; items: ProjectProgressOverview[] }
export const getProjectProgress = (): Promise<ProjectProgressList> =>
  api.get<ProjectProgressList>('/stats/project-progress').then(response => response.data)

export interface ProjectHoursReportQuery {
  start_date?: string
  end_date?: string
  keyword?: string
}

export const getProjectHoursReport = (params?: ProjectHoursReportQuery): Promise<ProjectHoursReport> =>
  api.get<ProjectHoursReport>('/reports/project-hours', { params }).then(response => response.data)

export const exportProjectHoursReport = (params?: ProjectHoursReportQuery): Promise<Blob> =>
  api.get('/reports/project-hours/export', { params, responseType: 'blob' }).then(response => response.data as Blob)

export const deleteProject = (id: number): Promise<void> =>
  api.delete(`/projects/${id}`)

export const getProjectDAG = (id: number): Promise<DAGData> =>
  api.get<DAGData>(`/projects/${id}/dag`).then(r => r.data);

export interface DetectionTaskCreatePayload {
  code: string
  name: string
  client_name?: string
  priority: number
  manager_id?: number | null
  start_date: string
  end_date: string
  task_type: string
  est_duration_hours: number
  switchover_hours: number
  requires_instrument: boolean
  requires_human: boolean
  allow_split: boolean
  allow_transfer: boolean
  instrument_ids: number[]
  assignee_id: number
}

export const getDetectionTasks = (): Promise<DetectionTask[]> =>
  api.get<DetectionTask[]>('/detection-tasks').then(response => response.data)

export const createDetectionTask = (data: DetectionTaskCreatePayload): Promise<DetectionTask> =>
  api.post<DetectionTask>('/detection-tasks', data).then(response => response.data)

export const updateDetectionTask = (id: number, data: DetectionTaskCreatePayload): Promise<DetectionTask> =>
  api.put<DetectionTask>(`/detection-tasks/${id}`, data).then(response => response.data)

export const confirmDetectionTaskInsert = (id: number, previewToken: string): Promise<DetectionTask> =>
  api.post<DetectionTask>(`/detection-tasks/${id}/confirm-insert`, { preview_token: previewToken }).then(response => response.data)

export const deleteDetectionTask = (id: number): Promise<void> =>
  api.delete(`/detection-tasks/${id}`).then(() => undefined)

export const addTask = (projId: number, data: {
  name: string; task_type: string; requires_instrument: boolean;
  est_duration_hours: number | null; switchover_hours: number;
  predecessor_ids: number[]; instrument_ids: number[];
}): Promise<Task> =>
  api.post<Task>(`/projects/${projId}/tasks`, data).then(r => r.data);


export const deleteTask = (id: number): Promise<void> =>
  api.delete(`/projects/tasks/${id}`)

export interface TaskUpdatePayload {
  name?: string
  task_type?: string
  requires_instrument?: boolean
  requires_human?: boolean
  est_duration_hours?: number | null
  switchover_hours?: number
  allow_split?: boolean
  allow_transfer?: boolean
  milestone_id?: number | null
  priority_weight?: number
  predecessor_ids?: number[]
  instrument_ids?: number[]
  assignee_id?: number | null
  parent_id?: number | null
}

export const updateTask = (taskId: number, data: TaskUpdatePayload): Promise<Task> =>
  api.put<Task>(`/projects/tasks/${taskId}`, data).then(r => r.data);

export const reorderProjectTasks = (projectId: number, parentId: number | null, taskIds: number[]): Promise<void> =>
  api.post(`/projects/${projectId}/tasks/reorder`, { parent_id: parentId, task_ids: taskIds }).then(() => undefined)

export const importStandardProjectPlan = (projectId: number): Promise<StandardPlanImportResult> =>
  api.post<StandardPlanImportResult>(`/projects/${projectId}/import-standard-plan`).then(r => r.data)

export interface ProjectPlanDraftTaskPayload {
  client_id: number
  name: string
  task_type: string
  requires_instrument: boolean
  requires_human: boolean
  estimated_hours?: number | null
  switchover_hours: number
  assignee_id?: number | null
  parent_id?: number | null
  predecessor_ids: number[]
  instrument_ids: number[]
  is_external_gate: boolean
  plan_order: number
}

export const commitProjectPlanDrafts = (
  projectId: number,
  tasks: ProjectPlanDraftTaskPayload[],
): Promise<{ status: string; message: string; created: number; id_map: { client_id: number; task_id: number }[] }> =>
  api.post(`/projects/${projectId}/plan-drafts/commit`, { tasks }).then(r => r.data)

export const saveAndScheduleProjectPlan = (
  projectId: number,
  tasks: ProjectPlanDraftTaskPayload[],
): Promise<ProjectPlanApplyResult> =>
  api.post<ProjectPlanApplyResult>(`/projects/${projectId}/plan-drafts/save-and-schedule`, { tasks }).then(r => r.data)

export const getDeadlineRecommendation = (projectId: number, jobId: string) =>
  api.get<import('@/types').ScheduleRecommendationJob>(
    `/projects/${projectId}/plan-drafts/deadline-recommendations/${jobId}`,
  ).then(r => r.data)

export interface ApprovalGateCreatePayload {
  name: string
  assignee_id?: number | null
  predecessor_task_id: number
  unlock_task_ids: number[]
}

export interface ApprovalGateQuery {
  status?: 'pending' | 'approved'
  keyword?: string
  project_id?: number
  manager_id?: number
  risk?: string
  expected_from?: string
  expected_to?: string
  workspace_only?: boolean
  page?: number
  page_size?: number
}

export const createApprovalGate = (projectId: number, data: ApprovalGateCreatePayload): Promise<ApprovalGate> =>
  api.post<ApprovalGate>(`/projects/${projectId}/approval-gates`, data).then(r => r.data)

export const getApprovalGates = (params?: ApprovalGateQuery): Promise<ApprovalGateList> =>
  api.get<ApprovalGateList>('/approval-gates', { params }).then(r => r.data)

export const getPendingApprovalSegments = (): Promise<PendingApprovalSegment[]> =>
  api.get<PendingApprovalSegment[]>('/approval-gates/pending-forecast').then(r => r.data)

export const getApprovalGate = (gateId: number): Promise<ApprovalGate> =>
  api.get<ApprovalGate>(`/approval-gates/${gateId}`).then(r => r.data)

export const submitApprovalGate = (
  gateId: number,
  data: { expected_approval_at: string; approval_note?: string | null },
): Promise<ApprovalGateAction> =>
  api.post<ApprovalGateAction>(`/approval-gates/${gateId}/submit`, data).then(r => r.data)

export const approveApprovalGate = (
  gateId: number,
  data: { approval_note?: string | null },
): Promise<ApprovalGateAction> =>
  api.post<ApprovalGateAction>(`/approval-gates/${gateId}/approve`, data).then(r => r.data)

export const confirmApprovalScheduleImpact = (
  gateId: number,
  previewToken: string,
): Promise<ApprovalGateAction> =>
  api.post<ApprovalGateAction>(`/approval-gates/${gateId}/confirm-schedule-impact`, null, {
    params: { preview_token: previewToken },
  }).then(r => r.data)

// Instruments
export interface InstrumentQuery {
  include_unavailable?: boolean
}

export const getInstruments = (params?: InstrumentQuery): Promise<Instrument[]> =>
  api.get<Instrument[]>('/instruments', { params }).then(r => r.data);

export interface InstrumentPayload {
  code: string
  name: string
  instrument_group: string
  brand?: string
  model?: string
  location?: string
  availability_status: 'available' | 'unavailable'
  switchover_base_hours: number
  effective_work_start: string
  effective_work_end: string
  capabilities: { tag_name: string; tag_value: string }[]
}

export const createInstrument = (data: InstrumentPayload): Promise<Instrument> =>
  api.post<Instrument>('/instruments', data).then(r => r.data);

export const updateInstrument = (id: number, data: InstrumentPayload): Promise<Instrument> =>
  api.put<Instrument>(`/instruments/${id}`, data).then(r => r.data);

export const deleteInstrument = (id: number): Promise<void> =>
  api.delete(`/instruments/${id}`);

export interface InstrumentFaultRequest {
  description: string
  estimated_resolved_at: string
  resolved_at?: string | null
}

export const reportInstrumentFault = (instId: number, data: InstrumentFaultRequest): Promise<InstrumentFault> =>
  api.post<InstrumentFault>(`/instruments/${instId}/fault`, data).then(r => r.data)

export const getOpenInstrumentFaults = (): Promise<InstrumentFault[]> =>
  api.get<InstrumentFault[]>('/instruments/faults/open').then(r => r.data)

export const getInstrumentFaults = (): Promise<InstrumentFault[]> =>
  api.get<InstrumentFault[]>('/instruments/faults').then(r => r.data)

export const resolveInstrumentFault = (instId: number, faultId: number): Promise<InstrumentFault> =>
  api.put<InstrumentFault>(`/instruments/${instId}/fault/${faultId}/resolve`).then(r => r.data)

// Schedules
export const getTimeslots = (
  params?: Record<string, unknown>,
  timeout?: number,
): Promise<TimeSlot[]> =>
  api.get<TimeSlot[]>('/schedules/timeslots', { params, timeout }).then(r => r.data);

export const getInstrumentBridgeReservations = (
  params?: Record<string, unknown>,
  timeout?: number,
): Promise<InstrumentBridgeReservation[]> =>
  api.get<InstrumentBridgeReservation[]>('/schedules/instrument-bridge-reservations', { params, timeout }).then(r => r.data);

export const generateSchedule = (projectIds?: number[]): Promise<{ status: string; message?: string }> =>
  api.post('/schedules/generate', { project_ids: projectIds, mode: 'normal' }).then(r => r.data);

export const applyProjectPlan = (projectId: number): Promise<ProjectPlanApplyResult> =>
  api.post<ProjectPlanApplyResult>('/schedules/apply-project-plan', { project_id: projectId }).then(r => r.data)

export const confirmProjectPlanInsert = (projectId: number, previewToken: string): Promise<ProjectPlanApplyResult> =>
  api.post<ProjectPlanApplyResult>('/schedules/apply-project-plan/confirm-insert', {
    project_id: projectId,
    preview_token: previewToken,
  }).then(r => r.data)

export const startTask = (slotId: number): Promise<{ status: string }> =>
  api.post(`/schedules/timeslots/${slotId}/start`).then(r => r.data);

export interface CompleteTaskRequest {
  release_instrument?: boolean
}

export interface CompleteTaskResponse {
  status: string
  message?: string
  moved_tasks?: number
  released_instrument?: boolean
}

export const completeTask = (
  slotId: number,
  data: CompleteTaskRequest = {},
): Promise<CompleteTaskResponse> =>
  api.post<CompleteTaskResponse>(`/schedules/timeslots/${slotId}/complete`, data).then(r => r.data);

export const interruptTask = (slotId: number): Promise<{ status: string }> =>
  api.post(`/schedules/timeslots/${slotId}/interrupt`).then(r => r.data);

export interface TaskSwitchCandidate {
  slot_id: number
  task_id: number
  task_name: string
  project_code: string
  project_name: string
  assignee_name: string | null
  plan_start: string
  plan_end: string
  is_paused: boolean
}

export const getTaskSwitchCandidates = (slotId: number): Promise<TaskSwitchCandidate[]> =>
  api.get<TaskSwitchCandidate[]>(`/schedules/timeslots/${slotId}/switch-candidates`).then(r => r.data)

export const pauseTask = (
  slotId: number,
  reason: string,
  targetSlotId?: number,
): Promise<{ status: string; message?: string }> =>
  api.post(`/schedules/timeslots/${slotId}/pause`, {
    reason,
    target_slot_id: targetSlotId,
  }).then(r => r.data)

export interface NightRunRequest {
  duration_hours: number
  earliest_start?: string
  latest_end?: string
  requires_operator: boolean
  remark?: string
}

export const recordNightRun = (slotId: number, data: NightRunRequest): Promise<TimeSlot> =>
  api.post<TimeSlot>(`/schedules/timeslots/${slotId}/night-run`, data).then(r => r.data)

export interface TaskDelayRequest {
  delay_hours: number
  reason: string
}

export interface TaskDelayResponse {
  status: string
  task_id: number
  slot_id: number
  delay_hours: number
  shifted_slots: number
  affected_tasks: number
  reason: string
}

export const reportTaskDelay = (slotId: number, data: TaskDelayRequest): Promise<TaskDelayResponse> =>
  api.post<TaskDelayResponse>(`/schedules/timeslots/${slotId}/delay`, data).then(r => r.data)

export interface InsertOrderRequest {
  project_id: number
  task_ids: number[]
  priority_override?: number
  mode?: 'priority' | 'custom_after_task'
  anchor_task_id?: number
}

export const calculateInsertCost = (data: InsertOrderRequest): Promise<InsertCost> =>
  api.post<InsertCost>('/schedules/insert-order', data).then(r => r.data);

export const confirmInsert = (data: InsertOrderRequest): Promise<InsertOrderResult> =>
  api.post<InsertOrderResult>('/schedules/insert-order/confirm', data).then(r => r.data);

export const reschedule = (data: { trigger_type: string; strategy: string }): Promise<{ status: string; message?: string }> =>
  api.post('/schedules/reschedule', data).then(r => r.data);

export const dailyRoll = (): Promise<{ status: string }> =>
  api.post('/schedules/daily-roll').then(r => r.data);



// Users
export interface User {
  id: number
  username: string
  display_name: string
  role: string
  roles: string[] | null
  email: string | null
  phone: string | null
  wecom_id: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface UserDirectoryEntry {
  id: number
  username: string
  display_name: string
  role: string
  roles: string[] | null
  is_active: boolean
}

export interface UserPayload {
  username: string
  display_name: string
  password?: string
  role: string
  roles: string[]
  email?: string | null
  phone?: string | null
  wecom_id?: string | null
  is_active: boolean
}

export const getUserDirectory = (): Promise<UserDirectoryEntry[]> =>
  api.get<UserDirectoryEntry[]>('/users/directory').then(r => r.data)

export const getUsers = (): Promise<User[]> =>
  api.get<User[]>('/users').then(r => r.data)

export const createUser = (data: UserPayload): Promise<User> =>
  api.post<User>('/users', data).then(r => r.data)

export const updateUser = (id: number, data: UserPayload): Promise<User> =>
  api.put<User>(`/users/${id}`, data).then(r => r.data)

export const resetUserPassword = (id: number, password: string): Promise<void> =>
  api.put(`/users/${id}/password`, { password }).then(() => undefined)

export const deleteUser = (id: number): Promise<void> =>
  api.delete(`/users/${id}`)

export interface PagePermission {
  page_key: string
  page_name: string
  group_name: string
  can_view: boolean
  can_operate: boolean
  actions: PageActionPermission[]
}

export interface PageActionPermission {
  action_key: string
  action_name: string
  allowed: boolean
}

export interface RolePermissionMatrix {
  roles: string[]
  items: Record<string, PagePermission[]>
}

export const getRolePermissions = (): Promise<RolePermissionMatrix> =>
  api.get<RolePermissionMatrix>('/role-permissions').then(response => response.data)

export const updateRolePermissions = (role: string, permissions: PagePermission[]): Promise<PagePermission[]> =>
  api.put<{ permissions: PagePermission[] }>(`/role-permissions/${encodeURIComponent(role)}`, {
    permissions: permissions.map(({ page_key, can_view, actions }) => ({
      page_key,
      can_view,
      can_operate: actions.some(action => action.allowed),
      actions,
    })),
  }).then(response => response.data.permissions)

export const keepSessionAlive = (): Promise<void> =>
  api.post('/users/keep-alive').then(() => undefined)

export interface ChangeMyPasswordPayload {
  old_password: string
  new_password: string
}

export const changeMyPassword = (data: ChangeMyPasswordPayload): Promise<void> =>
  api.post('/users/me/password', data).then(() => undefined)

export const logout = (): Promise<void> =>
  api.post('/users/logout').then(() => undefined)
// Schedule Rules
export type ScheduleRuleParamValue = string | number | boolean | number[] | null

export interface ScheduleRule {
  id: number
  category: string
  name: string
  code: string
  description: string | null
  params: Record<string, ScheduleRuleParamValue> | null
  is_enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export const getScheduleRules = (): Promise<ScheduleRule[]> =>
  api.get<ScheduleRule[]>('/schedule-rules').then(r => r.data)

export const updateScheduleRule = (id: number, data: { params?: Record<string, ScheduleRuleParamValue>; is_enabled?: boolean; sort_order?: number }): Promise<ScheduleRule> =>
  api.put<ScheduleRule>(`/schedule-rules/${id}`, data).then(r => r.data)

export const toggleScheduleRule = (id: number): Promise<ScheduleRule> =>
  api.put<ScheduleRule>(`/schedule-rules/${id}/toggle`).then(r => r.data)

// Stats
export interface StatsRangeParams {
  start_date?: string
  end_date?: string
}

export interface LabStatusInstrument {
  id: number
  code: string
  name: string
  group: string
  location: string | null
  status: string
  label_x: number
  label_y: number
  current_task: string | null
  current_project: string | null
  current_project_code?: string | null
  current_task_end?: string | null
  current_user: string | null
  progress: number | null
  next_task: string | null
  next_start: string | null
  next_project: string | null
  next_project_code: string | null
  next_user: string | null
  running_slot_id?: number | null
  running_start?: string | null
}

export const getDashboard = (params?: StatsRangeParams): Promise<DashboardData> =>
  api.get<DashboardData>('/stats/dashboard', { params }).then(r => r.data);

export const getUtilization = (params?: StatsRangeParams): Promise<UtilizationStats[]> =>
  api.get<UtilizationStats[]>('/stats/utilization', { params }).then(r => r.data);

export const getLabStatus = (): Promise<LabStatusInstrument[]> =>
  api.get<LabStatusInstrument[]>('/stats/lab-status').then(r => r.data);

// Task Types
export interface TaskTypeConfig {
  id: number
  name: string
  code: string
  resource_type: string
  description: string | null
  is_active: boolean
  sort_order: number
  predecessor_type_ids?: number[]
}

export const getTaskTypes = (): Promise<TaskTypeConfig[]> =>
  api.get<TaskTypeConfig[]>('/task-types').then(r => r.data)

export const createTaskType = (data: Partial<TaskTypeConfig>): Promise<TaskTypeConfig> =>
  api.post<TaskTypeConfig>('/task-types', data).then(r => r.data)

export const updateTaskType = (id: number, data: Partial<TaskTypeConfig>): Promise<TaskTypeConfig> =>
  api.put<TaskTypeConfig>('/task-types/' + id, data).then(r => r.data)

export const toggleTaskType = (id: number, isActive: boolean): Promise<TaskTypeConfig> =>
  api.put<TaskTypeConfig>(`/task-types/${id}/toggle`, { is_active: isActive }).then(r => r.data)

export const deleteTaskType = (id: number): Promise<void> =>
  api.delete('/task-types/' + id)

export interface AlertRule {
  id: number; name: string; rule_type: string; enabled: boolean;
  enable_site: boolean; enable_wecom: boolean;
  notify_roles: string | null; threshold_minutes: number; threshold_percent: number;
}
export const getAlertRules = (): Promise<AlertRule[]> =>
  api.get<AlertRule[]>('/alert-rules').then(r => r.data)
export const updateAlertRule = (id: number, data: Partial<AlertRule>): Promise<AlertRule> =>
  api.put<AlertRule>(`/alert-rules/${id}`, data).then(r => r.data)

export interface PushChannelConfig {
  id: number
  wecom_enabled: boolean
  wecom_corp_id: string | null
  wecom_agent_id: string | null
  has_wecom_secret: boolean
}

export interface PushChannelConfigUpdate {
  wecom_enabled?: boolean
  wecom_corp_id?: string | null
  wecom_agent_id?: string | null
  wecom_secret?: string | null
}

export const getPushConfig = (): Promise<PushChannelConfig> =>
  api.get<PushChannelConfig>('/alert-rules/push-config').then(r => r.data)

export const updatePushConfig = (data: PushChannelConfigUpdate): Promise<PushChannelConfig> =>
  api.put<PushChannelConfig>('/alert-rules/push-config', data).then(r => r.data)

export interface NotificationRecord {
  id: number
  user_name: string
  n_type: string
  channel: string
  delivery_status: string
  error_message: string | null
  title: string | null
  content: string | null
  is_read: boolean
  is_confirmed: boolean | null
  created_at: string
}

export const getNotificationHistory = (limit = 200): Promise<NotificationRecord[]> =>
  api.get<NotificationRecord[]>('/notifications/history', { params: { limit } }).then(r => r.data)

export interface NotificationQuery {
  user_name: string
  channel?: string
  unread_only?: boolean
}

export const getNotifications = (params: NotificationQuery): Promise<NotificationRecord[]> =>
  api.get<NotificationRecord[]>('/notifications', { params }).then(r => r.data)

export const markNotificationRead = (id: number): Promise<{ status: string }> =>
  api.put<{ status: string }>(`/notifications/${id}/read`).then(r => r.data)

export interface NotificationReadAllResult {
  status: string
  updated_count: number
}

export const markAllNotificationsRead = (): Promise<NotificationReadAllResult> =>
  api.put<NotificationReadAllResult>('/notifications/read-all').then(r => r.data)

export const confirmNotification = (id: number): Promise<{ status: string }> =>
  api.post<{ status: string }>(`/notifications/${id}/confirm`).then(r => r.data)

export interface CalendarDay {
  id: number
  date: string
  is_working_day: boolean
  holiday_name: string | null
  day_type: string
  source: 'default' | 'sync' | 'manual'
  affected_task_count?: number
  affected_project_count?: number
  needs_reschedule?: boolean
}
export const getCalendar = (year?: number, month?: number): Promise<CalendarDay[]> =>
  api.get<CalendarDay[]>('/calendar', { params: { year, month } }).then(r => r.data)

export interface AuditLogRecord {
  id: number
  user_name: string
  action: string
  target_type: string
  target_id: number | null
  detail: Record<string, unknown>
  created_at: string
  category: string
  category_label: string
  action_label: string
  target_display: string
  summary: string
  result: 'success' | 'failed'
  changes: Array<{ field: string; before: unknown; after: unknown }>
  business_detail: Record<string, unknown>
  technical_detail: Record<string, unknown>
}

export interface AuditLogQuery {
  keyword?: string
  action?: string
  category?: string
  user_name?: string
  page?: number
  page_size?: number
}
export interface AuditLogPage { items: AuditLogRecord[]; total: number; page: number; page_size: number }

export interface AuditLogCategoryOption { value: string; label: string }

export const getAuditLogCategories = (): Promise<AuditLogCategoryOption[]> =>
  api.get<AuditLogCategoryOption[]>('/audit-logs/categories').then(response => response.data)

export const getAuditLogs = (params?: AuditLogQuery): Promise<AuditLogPage> =>
  api.get<AuditLogPage>('/audit-logs', { params }).then(response => response.data)

export const exportAuditLogs = (params?: AuditLogQuery): Promise<Blob> =>
  api.get('/audit-logs/export', { params, responseType: 'blob' }).then(response => response.data as Blob)
