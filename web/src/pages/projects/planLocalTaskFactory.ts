import type { Task } from '@/services/api'

export interface LocalTaskPayload {
  name: string
  task_type: string
  requires_instrument: boolean
  est_duration_hours: number | null
  switchover_hours: number
  predecessor_ids: number[]
  assignee_id: number | null
  parent_id: number | null
  instrument_ids: number[]
}

interface BuildLocalTaskOptions {
  projectId: number
  id: number
  planOrder: number
  assigneeName: string | null
}

export function buildLocalTask(
  payload: LocalTaskPayload,
  options: BuildLocalTaskOptions,
): Task {
  return {
    id: options.id,
    project_id: options.projectId,
    name: payload.name,
    task_type: payload.task_type,
    requires_instrument: payload.requires_instrument,
    requires_human: payload.task_type !== 'group',
    est_duration_hours: payload.est_duration_hours ?? undefined,
    switchover_hours: payload.switchover_hours,
    status: 'pending',
    delay_status: 'not_delayed',
    schedule_dirty: true,
    schedule_lock_status: 'none',
    can_edit_schedule_fields: true,
    can_edit_basic_fields: true,
    can_edit_schedule_window: true,
    can_edit_resource_fields: true,
    priority_weight: 1,
    allow_split: false,
    instrument_ids: [...payload.instrument_ids],
    predecessor_ids: [...payload.predecessor_ids],
    assignee_id: payload.assignee_id,
    assignee_name: options.assigneeName,
    parent_id: payload.parent_id,
    is_local_draft: true,
    plan_order: options.planOrder,
  }
}
