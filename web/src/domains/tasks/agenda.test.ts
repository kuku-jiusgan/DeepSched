import { describe, expect, it } from 'vitest'
import dayjs from 'dayjs'
import type { AgendaItem } from '@/services/workspaceApi'
import { agendaTaskName, buildAgendaDays } from './agenda'

function item(overrides: Partial<AgendaItem> = {}): AgendaItem {
  const base = {
    slot_id: 1,
    task_id: 1,
    task_name: '方法开发',
    top_level_task_name: 'LCMS',
    task_status: 'scheduled',
    slot_status: 'scheduled',
    execution_status: 'scheduled',
    project_id: 1,
    project_code: 'XM-001',
    project_name: '测试项目',
    instrument_id: 1,
    instrument_code: 'LCMS-01',
    instrument_name: '液质联用仪',
    plan_start: '2026-08-10T08:30:00',
    plan_end: '2026-08-10T12:30:00',
    task_plan_end: null,
    actual_start: null,
    actual_end: null,
    ...overrides,
  }
  return {
    ...base,
    execution_status: overrides.execution_status || base.slot_status,
  }
}

describe('agenda day builder', () => {
  it('keeps empty days and splits a cross-day slot into each day', () => {
    const days = buildAgendaDays(
      [item({ slot_status: 'completed', plan_start: '2026-08-10T19:00:00', plan_end: '2026-08-11T09:00:00' })],
      dayjs('2026-08-10'),
      dayjs('2026-08-12'),
    )
    expect(days.map(day => day.items.length)).toEqual([1, 1, 0])
    expect(days[1].items[0].displayStart.format('HH:mm')).toBe('08:30')
  })

  it('marks overlapping items as conflicts', () => {
    const days = buildAgendaDays(
      [item(), item({ slot_id: 2, plan_start: '2026-08-10T10:00:00', plan_end: '2026-08-10T13:00:00' })],
      dayjs('2026-08-10'),
      dayjs('2026-08-10'),
    )
    expect(days[0].items.every(entry => entry.hasConflict)).toBe(true)
  })

  it('puts unfinished overdue work on today', () => {
    const days = buildAgendaDays(
      [item({
        task_status: 'paused',
        slot_status: 'paused',
        plan_start: '2026-08-05T19:30:00',
        plan_end: '2026-08-06T16:00:00',
        actual_start: '2026-08-05T14:03:00',
      })],
      dayjs('2026-08-07'),
      dayjs('2026-08-08'),
      dayjs('2026-08-07'),
    )

    expect(days[0].items).toHaveLength(1)
    expect(days[0].items[0].isOverdue).toBe(true)
    expect(days[0].items[0].displayStart.format('HH:mm')).toBe('08:30')
    expect(days[1].items).toHaveLength(0)
  })

  it('keeps paused work and a switched-in running task on today', () => {
    const days = buildAgendaDays(
      [
        item({
          slot_id: 20,
          task_id: 20,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-05T19:30:00',
          plan_end: '2026-08-06T16:00:00',
          actual_start: '2026-08-05T14:03:00',
          actual_end: '2026-08-07T09:00:00',
        }),
        item({
          slot_id: 21,
          task_id: 21,
          task_status: 'running',
          slot_status: 'running',
          plan_start: '2026-08-20T08:30:00',
          plan_end: '2026-08-20T12:00:00',
          actual_start: '2026-08-07T09:00:00',
        }),
      ],
      dayjs('2026-08-07'),
      dayjs('2026-08-13'),
      dayjs('2026-08-07'),
    )

    expect(days[0].items.map(entry => entry.task_id)).toEqual([20, 21])
    expect(days[0].items[0].isOverdue).toBe(true)
    expect(days[0].items[1].isTodayActivity).toBe(true)
  })

  it('does not create a new slot when a running task has no plan today', () => {
    const days = buildAgendaDays(
      [item({
        task_status: 'running',
        slot_status: 'running',
        plan_start: '2026-08-07T10:30:00',
        plan_end: '2026-08-07T20:00:00',
        actual_start: '2026-08-07T20:17:00',
      })],
      dayjs('2026-08-09'),
      dayjs('2026-08-15'),
      dayjs('2026-08-09'),
    )

    expect(days[0].items).toHaveLength(0)
  })

  it('keeps paused work without marking it overdue when future slots remain', () => {
    const days = buildAgendaDays(
      [
        item({
          slot_id: 30,
          task_id: 30,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-07T20:16:00',
          plan_end: '2026-08-07T20:17:00',
          actual_start: '2026-08-07T20:16:00',
          actual_end: '2026-08-07T20:17:00',
        }),
        item({
          slot_id: 31,
          task_id: 30,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-12T13:30:00',
          plan_end: '2026-08-14T18:30:00',
        }),
      ],
      dayjs('2026-08-09'),
      dayjs('2026-08-15'),
      dayjs('2026-08-09'),
    )

    expect(days[0].items).toHaveLength(1)
    expect(days[0].items[0].isTodayActivity).toBe(true)
    expect(days[0].items[0].isOverdue).toBe(false)
    expect(days[0].items[0].slot_status).toBe('paused')
  })

  it('uses the task plan end when remaining slots are outside the visible range', () => {
    const days = buildAgendaDays(
      [item({
        task_status: 'blocked',
        slot_status: 'blocked',
        plan_start: '2026-08-07T08:30:00',
        plan_end: '2026-08-07T20:00:00',
        task_plan_end: '2026-08-20T12:30:00',
        actual_start: '2026-08-07T09:00:00',
      })],
      dayjs('2026-08-09'),
      dayjs('2026-08-15'),
      dayjs('2026-08-09'),
    )

    expect(days[0].items).toHaveLength(1)
    expect(days[0].items[0].isTodayActivity).toBe(true)
    expect(days[0].items[0].isOverdue).toBe(false)
  })

  it('collapses continuation slots for one overdue task into one today item', () => {
    const days = buildAgendaDays(
      [
        item({
          slot_id: 10,
          task_id: 9,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-05T19:30:00',
          plan_end: '2026-08-05T20:00:00',
          actual_start: '2026-08-05T14:03:00',
        }),
        item({
          slot_id: 11,
          task_id: 9,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-06T08:30:00',
          plan_end: '2026-08-06T16:00:00',
        }),
      ],
      dayjs('2026-08-07'),
      dayjs('2026-08-07'),
      dayjs('2026-08-07'),
    )

    expect(days[0].items).toHaveLength(1)
    expect(days[0].items[0].slot_id).toBe(10)
  })

  it('prioritizes overdue work before regular work with the same start time', () => {
    const days = buildAgendaDays(
      [
        item({ slot_id: 1, plan_start: '2026-08-07T08:30:00', plan_end: '2026-08-07T12:00:00' }),
        item({
          slot_id: 2,
          task_id: 2,
          task_status: 'paused',
          slot_status: 'paused',
          plan_start: '2026-08-05T19:30:00',
          plan_end: '2026-08-06T16:00:00',
          actual_start: '2026-08-05T14:03:00',
        }),
      ],
      dayjs('2026-08-07'),
      dayjs('2026-08-07'),
      dayjs('2026-08-07'),
    )

    expect(days[0].items.map(entry => entry.slot_id)).toEqual([2, 1])
  })

  it('formats the top-level task name once', () => {
    expect(agendaTaskName(item())).toBe('LCMS·方法开发')
    expect(agendaTaskName(item({ top_level_task_name: null }))).toBe('方法开发')
  })
})
