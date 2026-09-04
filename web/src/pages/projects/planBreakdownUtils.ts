import type { Task } from '@/types'
import dayjs from 'dayjs'
import { isTaskCompleted } from '@/utils/statusMeta'
const TASK_TYPE_COLORS: Record<string, string> = {
  FFKF_001: '#8b5cf6', QCFA_001: '#f59e0b', FFYZ_001: '#10b981',
  SJCL_001: '#3b82f6', ZXBG_001: '#ef4444',
}

export function priorityLabel(priority: number) {
  return priority === 1 ? '一级（最高）' : priority === 2 ? '二级' : '三级'
}

export function priorityColor(priority: number) {
  return priority === 1 ? '#dc2626' : priority === 2 ? '#ea580c' : '#2563eb'
}

export function taskTreeHasCompletedTask(task: Task): boolean {
  return task.schedule_lock_status === 'completed'
    || isTaskCompleted(task.status)
    || Boolean(task.children?.some(taskTreeHasCompletedTask))
}

export function canAddChildTask(task: Task): boolean {
  // 已经开工或做完的任务不能再挂子任务。挂上去它就从叶子变成任务组，而排程只排
  // 叶子任务：它自己那些时间槽会脱离排程管理（不再被重排、顺延，故障也挪不动），
  // 预计工时被子任务汇总覆盖，工时统计里它自己已经做出来的工时也会被子任务之和顶掉。
  return task.status !== 'running' && !isTaskCompleted(task.status)
}

export function addChildDisabledReason(task: Task): string {
  return canAddChildTask(task) ? '添加子任务' : '任务已开始或已完成，不能再添加子任务'
}

export function gateStatusMeta(status?: string | null) {
  const metadata: Record<string, { label: string; color: string }> = {
    not_submitted: { label: '待提交', color: 'default' },
    waiting_approval: { label: '等待客户', color: 'blue' },
    approved: { label: '已签批', color: 'green' },
  }
  return metadata[status || 'not_submitted']
}

export function gateDateText(task: Task) {
  if (task.approved_at) return `签批 ${dayjs(task.approved_at).format('MM-DD HH:mm')}`
  if (task.expected_approval_at) return `预计 ${dayjs(task.expected_approval_at).format('MM-DD HH:mm')}`
  return '尚未提交客户'
}

export function getTaskTypeColor(code: string) {
  return TASK_TYPE_COLORS[code] || '#94a3b8'
}

export function buildTaskTree(tasks: Task[]): Task[] {
  const taskMap = new Map(tasks.map(task => [task.id, { ...task, children: [] as Task[] }]))
  const roots: Task[] = []
  for (const task of tasks) {
    const node = taskMap.get(task.id)!
    const parent = task.parent_id ? taskMap.get(task.parent_id) : undefined
    if (parent) parent.children?.push(node)
    else roots.push(node)
  }
  const compareTasks = (left: Task, right: Task) => (left.plan_order ?? 0) - (right.plan_order ?? 0) || left.id - right.id
  for (const task of taskMap.values()) task.children?.sort(compareTasks)
  return roots.sort(compareTasks)
}

export function countLeafTasks(tasks: Task[]): number {
  return tasks.reduce((count, task) => count + (
    task.children?.length ? countLeafTasks(task.children) : 1
  ), 0)
}

export function sumTaskHours(task: Task): number {
  return task.children?.length
    ? task.children.reduce((total, child) => total + sumTaskHours(child), 0)
    : task.est_duration_hours || 0
}

export function taskActualHoursText(task: Task): string {
  const sumActualHours = (current: Task): number => current.children?.length
    ? current.children.reduce((total, child) => total + sumActualHours(child), 0)
    : Number(current.actual_hours || 0)
  const hours = sumActualHours(task)
  return hours > 0 ? hours.toFixed(1) : '-'
}

export function taskInstrumentIds(task: Task): number[] {
  if (!task.children?.length) return task.instrument_ids || []
  return [...new Set(task.children.flatMap(taskInstrumentIds))]
}

export function parentTaskIds(tasks: Task[]): number[] {
  return [...new Set(tasks.flatMap(task => task.parent_id == null ? [] : [task.parent_id]))]
}

export function localDraftDependsOnTask(tasks: Task[], taskId: number): boolean {
  return tasks.some(task => task.is_local_draft
    && (task.parent_id === taskId || task.predecessor_ids.includes(taskId)))
}

export function taskTreeIds(tasks: Task[], rootId: number): Set<number> {
  const taskIds = new Set<number>([rootId])
  let hasNewDescendant = true
  while (hasNewDescendant) {
    hasNewDescendant = false
    for (const task of tasks) {
      if (task.parent_id != null && taskIds.has(task.parent_id) && !taskIds.has(task.id)) {
        taskIds.add(task.id)
        hasNewDescendant = true
      }
    }
  }
  return taskIds
}

export function allocateTemplateHours(total: number): [number, number, number, number] {
  const allocated = [0.7, 0.05, 0.2].map(rate => Math.round(total * rate * 100) / 100)
  return [allocated[0], allocated[1], allocated[2], Math.round((total - allocated.reduce((sum, value) => sum + value, 0)) * 100) / 100]
}
