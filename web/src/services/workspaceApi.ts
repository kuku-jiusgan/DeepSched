import { normalizeWorkspaceTask, type WorkspaceTask } from '@/domains/tasks/workspaceTask'
import http from './http'

export interface AgendaAssignee {
  id: number
  display_name: string
}

export interface AgendaItem {
  slot_id: number
  task_id: number
  task_name: string
  top_level_task_name: string | null
  task_status: string
  slot_status: string
  execution_status: string
  project_id: number
  project_code: string
  project_name: string
  instrument_id: number | null
  instrument_code: string | null
  instrument_name: string | null
  plan_start: string
  plan_end: string
  task_plan_end: string | null
  actual_start: string | null
  actual_end: string | null
}

export interface AgendaResult {
  start_date: string
  end_date: string
  assignee: AgendaAssignee
  can_select_assignee: boolean
  items: AgendaItem[]
}

export interface AgendaParams {
  start_date: string
  end_date: string
  assignee_id?: number
}


export const getMyTasks = (): Promise<WorkspaceTask[]> =>
  http.get<unknown[]>('/schedules/my-tasks')
    .then(response => response.data.map(normalizeWorkspaceTask))

export const getMyAgenda = (params: AgendaParams): Promise<AgendaResult> =>
  http.get<AgendaResult>('/schedules/my-agenda', { params }).then(response => response.data)
