import dayjs, { type Dayjs } from 'dayjs'
import type { AgendaItem } from '@/services/workspaceApi'

const WORKDAY_START_HOUR = 8
const WORKDAY_START_MINUTE = 30
const WORKDAY_END_HOUR = 20
const COMPLETED_STATUSES = new Set(['completed', 'done'])
const ACTIVITY_END_STATUSES = new Set(['paused', 'interrupted'])
const CARRYOVER_TASK_STATUSES = new Set(['paused', 'blocked', 'interrupted'])

export interface AgendaDisplayItem extends AgendaItem {
  displayStart: Dayjs
  displayEnd: Dayjs
  hasConflict: boolean
  isOverdue: boolean
  isTodayActivity: boolean
}

export interface AgendaDay {
  key: string
  date: Dayjs
  items: AgendaDisplayItem[]
}

export function buildAgendaDays(
  items: AgendaItem[],
  startDate: Dayjs,
  endDate: Dayjs,
  today: Dayjs = dayjs(),
): AgendaDay[] {
  const days: AgendaDay[] = []
  const todayStart = today.startOf('day')
  for (let cursor = startDate.startOf('day'); !cursor.isAfter(endDate, 'day'); cursor = cursor.add(1, 'day')) {
    const dayStart = cursor.startOf('day')
    const dayEnd = cursor.add(1, 'day').startOf('day')
    const dayItems = buildDayItems(items, dayStart, dayEnd, todayStart)
      .map(({ item, isOverdue, isTodayActivity }) => buildDisplayItem(item, dayStart, dayEnd, isOverdue, isTodayActivity))
      .sort(compareAgendaItems)
    markConflicts(dayItems)
    days.push({ key: dayStart.format('YYYY-MM-DD'), date: dayStart, items: dayItems })
  }
  return days
}

function buildDayItems(items: AgendaItem[], dayStart: Dayjs, dayEnd: Dayjs, todayStart: Dayjs) {
  const plannedItems = items.filter(item => hasPlannedOverlap(item, dayStart, dayEnd))
  const plannedTaskIds = new Set(plannedItems.map(item => item.task_id))
  const latestPlanEndByTask = buildLatestPlanEndByTask(items)
  const projectedItems = selectCanonicalProjectionItems(
    items.filter(item => isTaskProjectedOnDay(item, dayStart, todayStart, latestPlanEndByTask)),
  ).filter(item => !plannedTaskIds.has(item.task_id))
  return [
    ...plannedItems.map(item => ({ item, isOverdue: false, isTodayActivity: false })),
    ...projectedItems.map(item => {
      const isTodayActivity = isTodayActivityOnDay(item, dayStart, todayStart)
      return {
        item,
        isOverdue: !isOngoingRunningOnDay(item, dayStart)
          && isOverdueOnDay(item, dayStart, todayStart, latestPlanEndByTask),
        isTodayActivity: isTodayActivity || isCurrentCarryoverOnDay(item, dayStart, todayStart),
      }
    }),
  ]
}

function hasPlannedOverlap(item: AgendaItem, dayStart: Dayjs, dayEnd: Dayjs) {
  return dayjs(item.plan_end).isAfter(dayStart) && dayjs(item.plan_start).isBefore(dayEnd)
}

function isOverdueOnDay(
  item: AgendaItem,
  dayStart: Dayjs,
  todayStart: Dayjs,
  latestPlanEndByTask: Map<number, Dayjs>,
) {
  const status = agendaExecutionStatus(item)
  const latestPlanEnd = latestPlanEndByTask.get(item.task_id) || dayjs(item.plan_end)
  return (
    dayStart.isSame(todayStart, 'day')
    && status !== 'running'
    && !COMPLETED_STATUSES.has(status)
    && !COMPLETED_STATUSES.has(item.task_status)
    && !latestPlanEnd.isAfter(dayStart)
  )
}

function isTaskProjectedOnDay(
  item: AgendaItem,
  dayStart: Dayjs,
  todayStart: Dayjs,
  latestPlanEndByTask: Map<number, Dayjs>,
) {
  return isOverdueOnDay(item, dayStart, todayStart, latestPlanEndByTask)
    || isTodayActivityOnDay(item, dayStart, todayStart)
    || isCurrentCarryoverOnDay(item, dayStart, todayStart)
}

function isCurrentCarryoverOnDay(item: AgendaItem, dayStart: Dayjs, todayStart: Dayjs) {
  return dayStart.isSame(todayStart, 'day')
    && CARRYOVER_TASK_STATUSES.has(item.task_status)
    && !COMPLETED_STATUSES.has(item.task_status)
}

function buildLatestPlanEndByTask(items: AgendaItem[]) {
  const latestByTask = new Map<number, Dayjs>()
  for (const item of items) {
    const planEnd = dayjs(item.task_plan_end || item.plan_end)
    const current = latestByTask.get(item.task_id)
    if (!current || planEnd.isAfter(current)) latestByTask.set(item.task_id, planEnd)
  }
  return latestByTask
}

function isTodayActivityOnDay(item: AgendaItem, dayStart: Dayjs, todayStart: Dayjs) {
  const status = agendaExecutionStatus(item)
  if (!dayStart.isSame(todayStart, 'day')) return false
  if (COMPLETED_STATUSES.has(status) || COMPLETED_STATUSES.has(item.task_status)) return false
  const startedToday = status === 'running'
    && item.actual_start !== null
    && dayjs(item.actual_start).isSame(dayStart, 'day')
  const endedToday = ACTIVITY_END_STATUSES.has(status)
    && item.actual_end !== null
    && dayjs(item.actual_end).isSame(dayStart, 'day')
  return startedToday || endedToday
}

function isOngoingRunningOnDay(item: AgendaItem, dayStart: Dayjs) {
  return agendaExecutionStatus(item) === 'running'
    && item.actual_start !== null
    && dayjs(item.actual_start).isBefore(dayStart.add(1, 'day'))
    && (item.actual_end === null || !dayjs(item.actual_end).isBefore(dayStart))
}

function selectCanonicalProjectionItems(items: AgendaItem[]) {
  const canonicalByTask = new Map<number, AgendaItem>()
  for (const item of items) {
    const current = canonicalByTask.get(item.task_id)
    if (!current || isMoreRelevantProjectionItem(item, current)) {
      canonicalByTask.set(item.task_id, item)
    }
  }
  return [...canonicalByTask.values()]
}

function isMoreRelevantProjectionItem(candidate: AgendaItem, current: AgendaItem) {
  const candidateHasStarted = candidate.actual_start !== null
  const currentHasStarted = current.actual_start !== null
  if (candidateHasStarted !== currentHasStarted) return candidateHasStarted
  if (candidateHasStarted && currentHasStarted) {
    return dayjs(candidate.actual_start).isBefore(dayjs(current.actual_start))
  }
  const candidateEnd = dayjs(candidate.plan_end).valueOf()
  const currentEnd = dayjs(current.plan_end).valueOf()
  return candidateEnd > currentEnd || (candidateEnd === currentEnd && candidate.slot_id > current.slot_id)
}

function compareAgendaItems(left: AgendaDisplayItem, right: AgendaDisplayItem) {
  const leftPriority = left.isOverdue ? 0 : left.isTodayActivity ? 1 : 2
  const rightPriority = right.isOverdue ? 0 : right.isTodayActivity ? 1 : 2
  if (leftPriority !== rightPriority) return leftPriority - rightPriority
  return left.displayStart.valueOf() - right.displayStart.valueOf()
}

function buildDisplayItem(
  item: AgendaItem,
  dayStart: Dayjs,
  dayEnd: Dayjs,
  isOverdue: boolean,
  isTodayActivity: boolean,
): AgendaDisplayItem {
  const itemStart = dayjs(item.plan_start)
  const itemEnd = dayjs(item.plan_end)
  if (isOverdue || isTodayActivity) {
    return {
      ...item,
      displayStart: dayStart.hour(WORKDAY_START_HOUR).minute(WORKDAY_START_MINUTE),
      displayEnd: dayStart.hour(WORKDAY_END_HOUR),
      hasConflict: false,
      isOverdue,
      isTodayActivity,
    }
  }
  const isCrossDayCompleted = agendaExecutionStatus(item) === 'completed' && !itemStart.isSame(itemEnd, 'day')
  const visibleStart = isCrossDayCompleted
    ? dayStart.hour(WORKDAY_START_HOUR).minute(WORKDAY_START_MINUTE)
    : dayStart
  const visibleEnd = isCrossDayCompleted ? dayStart.hour(WORKDAY_END_HOUR) : dayEnd
  return {
    ...item,
    displayStart: itemStart.isAfter(visibleStart) ? itemStart : visibleStart,
    displayEnd: itemEnd.isBefore(visibleEnd) ? itemEnd : visibleEnd,
    hasConflict: false,
    isOverdue: false,
    isTodayActivity,
  }
}

function markConflicts(items: AgendaDisplayItem[]) {
  const scheduledItems = items.filter(item => !item.isOverdue && !item.isTodayActivity)
  for (let index = 0; index < scheduledItems.length; index += 1) {
    for (let nextIndex = index + 1; nextIndex < scheduledItems.length; nextIndex += 1) {
      if (!hasPlannedTimeConflict(scheduledItems[index], scheduledItems[nextIndex])) break
      scheduledItems[index].hasConflict = true
      scheduledItems[nextIndex].hasConflict = true
    }
  }
}

function hasPlannedTimeConflict(left: AgendaDisplayItem, right: AgendaDisplayItem) {
  const leftEnd = left.displayEnd.startOf('minute')
  const rightStart = right.displayStart.startOf('minute')
  return rightStart.isBefore(leftEnd)
}

export function agendaTaskName(item: AgendaItem) {
  return item.top_level_task_name && item.top_level_task_name !== item.task_name
    ? `${item.top_level_task_name}·${item.task_name}`
    : item.task_name
}

function agendaExecutionStatus(item: AgendaItem) {
  return item.execution_status || item.slot_status
}
