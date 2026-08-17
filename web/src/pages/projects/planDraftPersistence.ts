import type { ProjectPlanDraftTaskPayload, Task } from '@/services/api'

export interface DraftOrderGroup {
  parentId: number | null
  taskIds: number[]
}

export function siblingOrderGroups(
  tasks: Task[],
  siblingTasks: (parentId: number | null) => Task[],
): DraftOrderGroup[] {
  const parentIds = [...new Set(tasks.map(task => task.parent_id))]
  return parentIds.map(parentId => ({ parentId, taskIds: siblingTasks(parentId).map(task => task.id) }))
}

export async function persistCommittedDraftOrders(
  projectId: number,
  groups: DraftOrderGroup[],
  idMapRows: { client_id: number; task_id: number }[],
  reorderProjectTasks: (projectId: number, parentId: number | null, taskIds: number[]) => Promise<void>,
) {
  const idMap = new Map(idMapRows.map(row => [row.client_id, row.task_id]))
  for (const group of groups) {
    const parentId = group.parentId == null ? null : (idMap.get(group.parentId) ?? group.parentId)
    const taskIds = group.taskIds.map(taskId => idMap.get(taskId) ?? taskId)
    await reorderProjectTasks(projectId, parentId, taskIds)
  }
}

export function toDraftPayload(
  task: Task,
  isParentTask: (taskId: number) => boolean,
): ProjectPlanDraftTaskPayload {
  const isParent = isParentTask(task.id)
  return {
    client_id: task.id,
    name: task.name,
    task_type: isParent ? 'group' : task.task_type,
    requires_instrument: isParent ? false : task.requires_instrument,
    requires_human: isParent ? false : task.requires_human,
    estimated_hours: isParent ? null : (task.est_duration_hours ?? null),
    switchover_hours: isParent ? 0 : task.switchover_hours,
    assignee_id: isParent ? null : task.assignee_id,
    parent_id: task.parent_id,
    predecessor_ids: isParent ? [] : [...task.predecessor_ids],
    instrument_ids: isParent ? [] : [...task.instrument_ids],
    is_external_gate: Boolean(task.is_external_gate),
    plan_order: task.plan_order ?? 0,
  }
}
