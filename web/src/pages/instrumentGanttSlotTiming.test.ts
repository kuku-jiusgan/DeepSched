import { describe, expect, it } from 'vitest'

import type { TimeSlot } from '@/types'
import { displayStartForNonCompletedSlot } from './instrumentGanttSlotTiming'


function createSlot(overrides: Partial<TimeSlot> = {}): TimeSlot {
  return {
    id: 1,
    task_id: 1,
    instrument_id: 5,
    plan_start: '2026-08-28T15:00:00',
    plan_end: '2026-08-28T20:00:00',
    actual_start: undefined,
    actual_end: undefined,
    tier: 'confirmed',
    status: 'scheduled',
    execution_status: 'scheduled',
    is_night_run: false,
    task_name: '样品检测',
    task_type: 'instrument',
    task_status: 'scheduled',
    delay_status: 'not_delayed',
    project_code: 'P-001',
    project_name: '测试项目',
    assignee_id: 1,
    assignee_name: '测试人员',
    project_id: 1,
    ...overrides,
  }
}

describe('displayStartForNonCompletedSlot', () => {
  it('keeps the planned start for an early-started running slot without an actual end', () => {
    const slot = createSlot({
      status: 'running',
      execution_status: 'running',
      actual_start: '2026-08-25T13:55:54',
    })

    expect(displayStartForNonCompletedSlot(slot)).toBe('2026-08-28T15:00:00')
  })

  it('uses the actual start when the actual execution window is closed', () => {
    const slot = createSlot({
      status: 'paused',
      actual_start: '2026-08-25T13:55:54',
      actual_end: '2026-08-25T16:00:00',
    })

    expect(displayStartForNonCompletedSlot(slot)).toBe('2026-08-25T13:55:54')
  })
})
