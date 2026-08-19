import dayjs, { type Dayjs } from 'dayjs'
import { isTaskCompleted } from '@/utils/statusMeta'

export interface TaskTimeWindow {
  start: string | null
  end: string | null
}

export interface WorkspaceSegment {
  id: number
  instrument_id: number | null
  instrument_name: string | null
  instrument_code: string | null
  plan_start: string
  plan_end: string
  actual_start: string | null
  actual_end: string | null
  tier: string
  status: string
}

export interface WorkspaceDelay {
  status: 'delayed' | 'not_delayed'
  hours: number | null
  reason: string | null
  reported_at: string | null
}

export interface WorkspaceResumePriority {
  task_id: number
  task_name: string
  project_id: number | null
  project_name: string | null
  project_code: string | null
}

export interface WorkspaceTask {
  task_id: number
  task_name: string | null
  top_level_task_name?: string | null
  task_type: string | null
  assignee_id: number | null
  assignee_name: string | null
  project_id: number | null
  project_name: string | null
  project_code: string | null
  execution_status: string
  est_duration_hours: number | null
  actual_duration_hours?: number | null
  task_window: TaskTimeWindow
  actual_window: TaskTimeWindow
  actionable_slot: WorkspaceSegment | null
  segments: WorkspaceSegment[]
  delay: WorkspaceDelay
  resume_priority: WorkspaceResumePriority | null
}

type UnknownRecord = Record<string, unknown>

export function normalizeWorkspaceTask(value: unknown): WorkspaceTask {
  const task = asRecord(value)
  const taskWindow = asRecord(task.task_window)
  const actualWindow = asRecord(task.actual_window)
  const delay = asRecord(task.delay)
  const resumePriority = normalizeResumePriority(task.resume_priority)
  const nestedActionableSlot = normalizeWorkspaceSegment(task.actionable_slot)
  const legacyActionableSlot = nestedActionableSlot || normalizeLegacyWorkspaceSegment(task)
  const nestedSegments = Array.isArray(task.segments)
    ? task.segments.map(normalizeWorkspaceSegment).filter(isWorkspaceSegment)
    : []

  return {
    task_id: numberValue(task.task_id) ?? 0,
    task_name: stringValue(task.task_name),
    top_level_task_name: stringValue(task.top_level_task_name),
    task_type: stringValue(task.task_type),
    assignee_id: numberValue(task.assignee_id),
    assignee_name: stringValue(task.assignee_name),
    project_id: numberValue(task.project_id),
    project_name: stringValue(task.project_name),
    project_code: stringValue(task.project_code),
    execution_status: stringValue(task.execution_status) || stringValue(task.status) || 'pending',
    est_duration_hours: numberValue(task.est_duration_hours),
    actual_duration_hours: numberValue(task.actual_duration_hours),
    task_window: {
      start: stringValue(taskWindow.start) || stringValue(task.task_plan_start) || stringValue(task.plan_start),
      end: stringValue(taskWindow.end) || stringValue(task.task_plan_end) || stringValue(task.plan_end),
    },
    actual_window: {
      start: stringValue(actualWindow.start) || stringValue(task.actual_start),
      end: stringValue(actualWindow.end) || stringValue(task.actual_end),
    },
    actionable_slot: legacyActionableSlot,
    segments: nestedSegments.length ? nestedSegments : (legacyActionableSlot ? [legacyActionableSlot] : []),
    delay: {
      status: delayStatus(delay.status ?? task.delay_status),
      hours: numberValue(delay.hours) ?? numberValue(task.delay_hours),
      reason: stringValue(delay.reason) || stringValue(task.delay_reason),
      reported_at: stringValue(delay.reported_at) || stringValue(task.delay_reported_at),
    },
    resume_priority: resumePriority,
  }
}

function normalizeResumePriority(value: unknown): WorkspaceResumePriority | null {
  const priority = asRecord(value)
  const taskId = numberValue(priority.task_id)
  const taskName = stringValue(priority.task_name)
  if (taskId === null || !taskName) return null
  return {
    task_id: taskId,
    task_name: taskName,
    project_id: numberValue(priority.project_id),
    project_name: stringValue(priority.project_name),
    project_code: stringValue(priority.project_code),
  }
}

function normalizeWorkspaceSegment(value: unknown): WorkspaceSegment | null {
  const segment = asRecord(value)
  const id = numberValue(segment.id)
  const planStart = stringValue(segment.plan_start)
  const planEnd = stringValue(segment.plan_end)
  if (id === null || !planStart || !planEnd) return null
  return {
    id,
    instrument_id: numberValue(segment.instrument_id),
    instrument_name: stringValue(segment.instrument_name),
    instrument_code: stringValue(segment.instrument_code),
    plan_start: planStart,
    plan_end: planEnd,
    actual_start: stringValue(segment.actual_start),
    actual_end: stringValue(segment.actual_end),
    tier: stringValue(segment.tier) || 'unscheduled',
    status: stringValue(segment.status) || 'scheduled',
  }
}

function normalizeLegacyWorkspaceSegment(task: UnknownRecord): WorkspaceSegment | null {
  const slotId = numberValue(task.slot_id)
  if (!slotId) return null
  return normalizeWorkspaceSegment({
    id: slotId,
    instrument_id: task.instrument_id,
    instrument_name: task.instrument_name,
    instrument_code: task.instrument_code,
    plan_start: task.plan_start,
    plan_end: task.plan_end,
    actual_start: task.actual_start,
    actual_end: task.actual_end,
    tier: task.tier,
    status: task.status,
  })
}

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === 'object' ? value as UnknownRecord : {}
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function delayStatus(value: unknown): WorkspaceDelay['status'] {
  return value === 'delayed' ? 'delayed' : 'not_delayed'
}

function isWorkspaceSegment(value: WorkspaceSegment | null): value is WorkspaceSegment {
  return value !== null
}

export function isTaskClosed(task: WorkspaceTask) {
  return isTaskCompleted(task.execution_status)
}

export function isTaskDue(task: WorkspaceTask, now: Dayjs = dayjs()) {
  if (!task.task_window.start) return false
  return !dayjs(task.task_window.start).isAfter(now.endOf('day'))
}

export function isDuePendingTask(task: WorkspaceTask, now: Dayjs = dayjs()) {
  return ['pending', 'scheduled'].includes(task.execution_status) && isTaskDue(task, now)
}

export function isWorkspaceActiveTask(task: WorkspaceTask, now: Dayjs = dayjs()) {
  return ['running', 'paused', 'blocked', 'interrupted'].includes(task.execution_status) || isDuePendingTask(task, now)
}

export function isWorkspacePendingTask(task: WorkspaceTask, now: Dayjs = dayjs()) {
  return ['pending', 'scheduled'].includes(task.execution_status) && !isDuePendingTask(task, now)
}

export function workspaceActionStatus(task: WorkspaceTask) {
  return task.actionable_slot?.status || task.execution_status
}

export function canStartWorkspaceTask(task: WorkspaceTask) {
  return ['pending', 'scheduled', 'blocked', 'paused', 'interrupted'].includes(workspaceActionStatus(task))
    && Boolean(actionableSlotId(task))
}

export function canCompleteWorkspaceTask(task: WorkspaceTask) {
  return workspaceActionStatus(task) === 'running' && Boolean(actionableSlotId(task))
}

export function canPauseWorkspaceTask(task: WorkspaceTask) {
  return workspaceActionStatus(task) === 'running' && Boolean(actionableSlotId(task))
}

export function isExceptionConfirmTask(task: WorkspaceTask, now: Dayjs = dayjs()) {
  const isProblemStatus = ['blocked', 'interrupted'].includes(task.execution_status)
  const hasStarted = Boolean(task.actual_window.start)
  const isCompletionOverdue = hasStarted
    && Boolean(task.task_window.end)
    && dayjs(task.task_window.end).isBefore(now)
    && !isTaskClosed(task)
  const isStartOverdue = !hasStarted
    && Boolean(task.task_window.end)
    && dayjs(task.task_window.end).isBefore(now)
    && !isTaskClosed(task)
  // 延期启动在任务已经启动后不再作为异常确认；只有完成节点也超期
  // 时才回到异常栏。
  return (!hasStarted && task.delay.status === 'delayed')
    || isProblemStatus
    || isStartOverdue
    || isCompletionOverdue
    || (!hasStarted && Boolean(task.delay.reason))
    || (!hasStarted && Boolean(task.delay.hours))
}

export function actionableSlotId(task: WorkspaceTask) {
  return task.actionable_slot?.id ?? null
}
