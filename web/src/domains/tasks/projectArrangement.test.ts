import dayjs from 'dayjs'
import { describe, expect, it } from 'vitest'
import type { ProjectArrangementItem } from '@/services/api'
import { buildProjectArrangementDays, projectArrangementActualText } from './projectArrangement'

function item(overrides: Partial<ProjectArrangementItem> = {}): ProjectArrangementItem {
  return {
    slot_id: 1,
    task_id: 1,
    task_name: '方法验证',
    top_level_task_name: null,
    plan_order: 1,
    task_status: 'scheduled',
    slot_status: 'scheduled',
    delay_status: 'not_delayed',
    assignee_id: 1,
    assignee_name: '分析员',
    instrument_id: 1,
    instrument_code: 'LCMS-01',
    instrument_name: '液质联用仪',
    plan_start: '2026-08-08T08:30:00',
    plan_end: '2026-08-08T12:00:00',
    actual_start: null,
    actual_end: null,
    is_external_gate: false,
    expected_approval_at: null,
    ...overrides,
  }
}

function displayItem(value: ProjectArrangementItem, now: string) {
  return buildProjectArrangementDays([value], dayjs(now))[0].items[0]
}

describe('buildProjectArrangementDays', () => {
  it('groups only arranged dates in chronological order', () => {
    const days = buildProjectArrangementDays([
      item({ slot_id: 2, plan_start: '2026-08-09T08:30:00', plan_end: '2026-08-09T12:00:00' }),
      item(),
    ], dayjs('2026-08-07T08:00:00'))

    expect(days.map(day => day.key)).toEqual(['2026-08-08', '2026-08-09'])
  })

  it('places approval gates by expected date and undated tasks last', () => {
    const days = buildProjectArrangementDays([
      item({
        slot_id: null,
        task_id: 2,
        task_name: '方案签批',
        plan_start: null,
        plan_end: null,
        slot_status: null,
        is_external_gate: true,
        expected_approval_at: '2026-08-10T15:00:00',
      }),
      item({ slot_id: null, task_id: 3, plan_start: null, plan_end: null, slot_status: null }),
    ], dayjs('2026-08-07T08:00:00'))

    expect(days.map(day => day.key)).toEqual(['2026-08-10', 'unscheduled'])
  })

  it('marks unfinished tasks after their planned end as overdue', () => {
    const [day] = buildProjectArrangementDays([item()], dayjs('2026-08-09T08:00:00'))

    expect(day.items[0].isOverdue).toBe(true)
    expect(day.items[0].dailyState).toBe('missed')
    expect(projectArrangementActualText(day.items[0])).toBe('当日实际：未执行')
  })

  it('shows a running continuation as active on its current day', () => {
    const running = displayItem(item({
      task_status: 'running',
      slot_status: 'running',
      plan_start: '2026-08-07T08:30:00',
      plan_end: '2026-08-07T20:00:00',
      actual_start: null,
    }), '2026-08-07T14:00:00')

    expect(running.dailyState).toBe('continuing')
    expect(projectArrangementActualText(running)).toBe('当日实际：运行中（延续）')
  })

  it('keeps future slots running when the task status is running', () => {
    const future = displayItem(item({
      task_status: 'running',
      slot_status: 'running',
      plan_start: '2026-08-10T08:30:00',
      plan_end: '2026-08-10T20:00:00',
    }), '2026-08-07T14:00:00')

    expect(future.dailyState).toBe('running')
    expect(projectArrangementActualText(future)).toBe('当日实际：运行中（延续）')
  })

  it('shows the actual window and completion state for an executed slot', () => {
    const completed = displayItem(item({
      task_status: 'running',
      slot_status: 'completed',
      actual_start: '2026-08-08T08:45:00',
      actual_end: '2026-08-08T12:30:00',
    }), '2026-08-09T08:00:00')

    expect(completed.dailyState).toBe('completed')
    expect(completed.isOverdue).toBe(false)
    expect(projectArrangementActualText(completed)).toBe('当日实际：08:45–12:30')
  })

  it('keeps approval gates outside daily execution semantics', () => {
    const approval = displayItem(item({
      slot_id: null,
      task_status: 'waiting_external',
      slot_status: null,
      plan_start: null,
      plan_end: null,
      is_external_gate: true,
      expected_approval_at: '2026-08-10T15:00:00',
    }), '2026-08-07T14:00:00')

    expect(approval.dailyState).toBe('approval')
    expect(projectArrangementActualText(approval)).toBe('签批：等待客户')
  })
})
