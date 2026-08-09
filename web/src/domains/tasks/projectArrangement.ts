import dayjs, { type Dayjs } from 'dayjs'
import type { ProjectArrangementItem } from '@/services/api'

export type ProjectArrangementDailyState =
  | 'running'
  | 'continuing'
  | 'completed'
  | 'missed'
  | 'pending'
  | 'unscheduled'
  | 'approval'

export interface ProjectArrangementDisplayItem extends ProjectArrangementItem {
  displayDate: Dayjs | null
  dailyState: ProjectArrangementDailyState
  isOverdue: boolean
  isUnscheduled: boolean
}

export interface ProjectArrangementDay {
  key: string
  date: Dayjs | null
  items: ProjectArrangementDisplayItem[]
  isUnscheduled: boolean
}

export function buildProjectArrangementDays(
  items: ProjectArrangementItem[],
  now: Dayjs = dayjs(),
): ProjectArrangementDay[] {
  const dated = items
    .filter(item => item.plan_start || item.expected_approval_at || item.actual_start)
    .map(item => toDisplayItem(item, now))
    .sort(compareItems)
  const unscheduled = items
    .filter(item => !item.plan_start && !item.expected_approval_at && !item.actual_start)
    .map(item => toDisplayItem(item, now))
    .sort(compareItems)
  const days: ProjectArrangementDay[] = []
  for (const item of dated) {
    const date = item.displayDate!.startOf('day')
    const key = date.format('YYYY-MM-DD')
    const current = days.find(day => day.key === key)
    if (current) current.items.push(item)
    else days.push({ key, date, items: [item], isUnscheduled: false })
  }
  if (unscheduled.length) days.push({ key: 'unscheduled', date: null, items: unscheduled, isUnscheduled: true })
  return days
}

export function projectArrangementActualText(
  item: ProjectArrangementDisplayItem,
) {
  if (item.dailyState === 'approval') {
    return ['done', 'completed'].includes(item.task_status) ? '签批：已完成' : '签批：等待客户'
  }
  if (item.dailyState === 'unscheduled') return '当日实际：未排程'
  if (['running', 'continuing'].includes(item.dailyState)) return '当日实际：运行中（延续）'
  if (item.dailyState === 'missed') return '当日实际：未执行'
  if (item.dailyState === 'pending') return '当日实际：待执行'
  const actualStart = dayjs(item.actual_start).format('HH:mm')
  const actualEnd = item.actual_end ? dayjs(item.actual_end).format('HH:mm') : '进行中'
  return `当日实际：${actualStart}–${actualEnd}`
}

function toDisplayItem(item: ProjectArrangementItem, now: Dayjs): ProjectArrangementDisplayItem {
  const anchor = item.plan_start || item.expected_approval_at || item.actual_start
  const dailyState = dailyExecutionState(item, now)
  const isApprovalOverdue = item.is_external_gate
    && item.expected_approval_at !== null
    && dayjs(item.expected_approval_at).isBefore(now)
    && !['done', 'completed'].includes(item.task_status)
  return {
    ...item,
    displayDate: anchor ? dayjs(anchor) : null,
    dailyState,
    isUnscheduled: !item.plan_start && !item.expected_approval_at,
    isOverdue: dailyState === 'missed' || isApprovalOverdue,
  }
}

function dailyExecutionState(
  item: ProjectArrangementItem,
  now: Dayjs,
): ProjectArrangementDailyState {
  if (item.is_external_gate) return 'approval'
  if (!item.plan_start) return 'unscheduled'
  if (item.actual_start) return item.actual_end ? 'completed' : 'running'
  const planDate = dayjs(item.plan_start)
  if (item.task_status === 'running') return planDate.isSame(now, 'day') ? 'continuing' : 'running'
  if (planDate.isAfter(now, 'day')) return 'pending'
  if (planDate.isSame(now, 'day')) return item.task_status === 'running' ? 'continuing' : 'pending'
  return 'missed'
}

function compareItems(left: ProjectArrangementDisplayItem, right: ProjectArrangementDisplayItem) {
  const leftDate = left.displayDate?.valueOf() || Number.MAX_SAFE_INTEGER
  const rightDate = right.displayDate?.valueOf() || Number.MAX_SAFE_INTEGER
  return leftDate - rightDate || left.plan_order - right.plan_order || left.task_id - right.task_id || (left.slot_id || 0) - (right.slot_id || 0)
}
