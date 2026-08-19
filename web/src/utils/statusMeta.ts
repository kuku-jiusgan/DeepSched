export interface StatusMeta {
  label: string
  color: string
  group: TaskStatusGroup
  isTerminal: boolean
}

export type TaskStatusGroup = 'pending' | 'active' | 'completed'

export type TaskStatus =
  | 'pending'
  | 'ready'
  | 'scheduled'
  | 'running'
  | 'paused'
  | 'blocked'
  | 'interrupted'
  | 'done'
  | 'completed'
  | 'waiting_external'
  | 'waiting_approval'

const taskStatusMetaMap: Record<TaskStatus, StatusMeta> = {
  pending: { label: '待处理', color: '#94a3b8', group: 'pending', isTerminal: false },
  ready: { label: '待处理', color: '#94a3b8', group: 'pending', isTerminal: false },
  scheduled: { label: '待执行', color: '#2563eb', group: 'pending', isTerminal: false },
  running: { label: '运行中', color: '#16a34a', group: 'active', isTerminal: false },
  paused: { label: '已暂停', color: '#d97706', group: 'active', isTerminal: false },
  blocked: { label: '已阻塞', color: '#dc2626', group: 'active', isTerminal: false },
  interrupted: { label: '已中断', color: '#ea580c', group: 'active', isTerminal: false },
  done: { label: '已完成', color: '#7c3aed', group: 'completed', isTerminal: true },
  completed: { label: '已完成', color: '#7c3aed', group: 'completed', isTerminal: true },
  waiting_external: { label: '等待外部签批', color: '#d97706', group: 'active', isTerminal: false },
  waiting_approval: { label: '等待客户签批', color: '#2563eb', group: 'active', isTerminal: false },
}

const fallbackStatusMeta: StatusMeta = {
  label: '未知状态',
  color: '#94a3b8',
  group: 'active',
  isTerminal: false,
}

function isTaskStatus(status: string): status is TaskStatus {
  return status in taskStatusMetaMap
}

export function getTaskStatusMeta(status: string | null | undefined): StatusMeta {
  if (!status) return taskStatusMetaMap.pending
  if (isTaskStatus(status)) return taskStatusMetaMap[status]
  return { ...fallbackStatusMeta, label: status }
}

export function taskStatusLabel(status: string | null | undefined) {
  return getTaskStatusMeta(status).label
}

export function taskStatusColor(status: string | null | undefined) {
  return getTaskStatusMeta(status).color
}

export function taskStatusGroup(status: string | null | undefined) {
  return getTaskStatusMeta(status).group
}

export function taskStatusMatchesGroup(status: string | null | undefined, group: TaskStatusGroup) {
  return taskStatusGroup(status) === group
}

export function isTaskCompleted(status: string | null | undefined) {
  return getTaskStatusMeta(status).isTerminal
}
