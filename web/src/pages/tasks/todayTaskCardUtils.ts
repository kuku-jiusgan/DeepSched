import dayjs from 'dayjs'
import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { taskStatusLabel } from '@/utils/statusMeta'
import {
  actionableSlotId,
  canCompleteWorkspaceTask,
  canPauseWorkspaceTask,
  canStartWorkspaceTask,
  isExceptionConfirmTask,
  isTaskClosed,
  isTaskDue,
} from '@/domains/tasks/workspaceTask'

export { isExceptionConfirmTask }

interface StoredUser {
  display_name?: string
  username?: string
}

export function normalizeWorkdayEndTime(value: unknown) {
  if (typeof value === 'string' && /^\d{2}:\d{2}$/.test(value)) return value
  if (typeof value === 'number' && Number.isFinite(value)) {
    const totalMinutes = Math.round(value * 60)
    const hours = Math.floor(totalMinutes / 60).toString().padStart(2, '0')
    const minutes = (totalMinutes % 60).toString().padStart(2, '0')
    return `${hours}:${minutes}`
  }
  return null
}

export function formatHours(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export function currentUserName() {
  const rawUser = localStorage.getItem('user')
  if (!rawUser) return '当前用户'
  try {
    const user = JSON.parse(rawUser) as StoredUser
    return user.display_name || user.username || '当前用户'
  } catch {
    return '当前用户'
  }
}

export function isTodayTask(task: WorkspaceTask, now: dayjs.Dayjs = dayjs()) {
  if (isTaskClosed(task)) return false
  if (['running', 'paused'].includes(task.execution_status)) return true
  return isTaskDue(task, now)
}

export function isCompletionConfirmTask(task: WorkspaceTask, now: dayjs.Dayjs = dayjs()) {
  return isTodayTask(task, now) && !isExceptionConfirmTask(task, now)
}

export function isWorkspaceExceptionConfirmTask(task: WorkspaceTask, now: dayjs.Dayjs = dayjs()) {
  return !isTaskClosed(task) && isExceptionConfirmTask(task, now)
}

export function canStartTask(task: WorkspaceTask) {
  return canStartWorkspaceTask(task)
}

export function canCompleteTask(task: WorkspaceTask) {
  return canCompleteWorkspaceTask(task)
}

export function canPauseTask(task: WorkspaceTask) {
  return canPauseWorkspaceTask(task)
}

export function formatTaskTime(value: string | dayjs.Dayjs | null, fallback: string) {
  return value ? dayjs(value).format('HH:mm') : fallback
}

function formatTaskDateTime(value: string | dayjs.Dayjs | null, fallback: string) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : fallback
}

export function nightRunEndTime(task: WorkspaceTask) {
  const start = task.night_run?.start_at
  return start ? dayjs(start) : null
}

export function canNightRunTask(task: WorkspaceTask) {
  return task.night_run?.is_eligible === true
}

export function nightRunDisabledReason(task: WorkspaceTask) {
  return task.night_run?.reason || ''
}

export function parseNightClock(baseTime: dayjs.Dayjs, value: string) {
  if (!value) return null
  const cleanValue = value.trim()
  const isNextDay = cleanValue.startsWith('次日')
  const match = cleanValue.replace('次日', '').trim().match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (hour > 23 || minute > 59) return null
  let parsedTime = baseTime.hour(hour).minute(minute).second(0).millisecond(0)
  if (isNextDay || parsedTime.isBefore(baseTime)) parsedTime = parsedTime.add(1, 'day')
  return parsedTime
}

export function maxNightRunHours(earliestStart: string, latestEnd: string) {
  const start = parseNightClock(dayjs(), earliestStart)
  if (!start) return 0
  const end = parseNightClock(start, latestEnd)
  if (!end || !end.isAfter(start)) return 0
  return Math.floor(end.diff(start, 'minute') / 30) * 0.5
}

export function isHalfHourDuration(value: unknown) {
  return typeof value === 'number' && Number.isInteger(value * 2)
}

export function statusLabel(status: string) {
  return taskStatusLabel(status)
}

export function scheduleText(task: WorkspaceTask) {
  const startText = formatTaskDateTime(task.task_window.start, '---- -- -- --:--')
  const endText = formatTaskDateTime(task.task_window.end, '---- -- -- --:--')
  return `${startText}–${endText}`
}

export function actualText(task: WorkspaceTask) {
  return `${formatTaskDateTime(task.actual_window.start, '---- -- -- --:--')}–${formatTaskDateTime(task.actual_window.end, '---- -- -- --:--')}`
}

export function formatProjectText(task: WorkspaceTask) {
  const parts = [task.project_code, task.project_name].filter(Boolean)
  return parts.length ? parts.join(' · ') : '未关联项目'
}

export function formatInstrumentText(task: WorkspaceTask) {
  const slot = task.actionable_slot
  if (slot?.instrument_code && slot.instrument_name) return `${slot.instrument_code} · ${slot.instrument_name}`
  return slot?.instrument_code || slot?.instrument_name || '未指定仪器'
}

export function getDelayText(task: WorkspaceTask) {
  if (!task.delay.reason && !task.delay.hours) return ''
  const hoursText = task.delay.hours ? `${task.delay.hours}h` : ''
  return [hoursText, task.delay.reason || '未填写原因'].filter(Boolean).join(' · ')
}
