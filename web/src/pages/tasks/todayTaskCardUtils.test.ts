import { describe, expect, it } from 'vitest'
import dayjs from 'dayjs'

import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { getNightRunEligibility, nightRunEndTime } from './todayTaskCardUtils'


function task(overrides: Partial<WorkspaceTask> = {}): WorkspaceTask {
  return {
    task_id: 21,
    task_name: '方法开发',
    task_type: 'FFKF_001',
    assignee_id: 4,
    assignee_name: '王思伟',
    project_id: 6,
    project_name: '双氯芬酸钠盐酸利多卡因注射液中5种亚硝胺杂质研究',
    project_code: 'XM2026186',
    execution_status: 'running',
    est_duration_hours: 62,
    task_window: {
      start: '2026-07-16T09:30:00',
      end: '2026-07-22T17:30:00',
    },
    actual_window: {
      start: '2026-07-16T09:30:00',
      end: null,
    },
    actionable_slot: {
      id: 20,
      instrument_id: 5,
      instrument_name: '三重四极液质联用仪',
      instrument_code: 'ZBYY-002-0005',
      plan_start: '2026-07-20T08:30:00',
      plan_end: '2026-07-20T22:00:00',
      actual_start: null,
      actual_end: null,
      tier: 'confirmed',
      status: 'running',
    },
    segments: [],
    delay: {
      status: 'not_delayed',
      hours: null,
      reason: null,
      reported_at: null,
    },
    resume_priority: null,
    ...overrides,
  }
}

describe('nightRunEndTime', () => {
  it('uses the current actionable segment date for a multi-day running task', () => {
    expect(nightRunEndTime(task())?.format('YYYY-MM-DD HH:mm')).toBe('2026-07-20 22:00')
  })

  it('returns null when the actionable segment has no planned end', () => {
    const withoutActionableSlot = task({ actionable_slot: null })

    expect(nightRunEndTime(withoutActionableSlot)).toBeNull()
  })
})

describe('getNightRunEligibility', () => {
  const now = dayjs('2026-07-20T15:30:00')

  it('allows a task ending at 22:00 when the workday ends at 20:00', () => {
    expect(getNightRunEligibility(task(), '20:00', now)).toEqual({
      isEligible: true,
      reason: '',
    })
  })

  it('rejects a task whose current segment ends before the workday boundary', () => {
    const earlyEnd = task({
      actionable_slot: {
        ...task().actionable_slot!,
        plan_end: '2026-07-20T19:30:00',
      },
    })

    expect(getNightRunEligibility(earlyEnd, '20:00', now).reason).toContain('不早于')
  })

  it('reports a date mismatch separately from the workday boundary', () => {
    const futureSegment = task({
      actionable_slot: {
        ...task().actionable_slot!,
        plan_start: '2026-07-21T08:30:00',
        plan_end: '2026-07-21T22:00:00',
      },
    })

    expect(getNightRunEligibility(futureSegment, '20:00', now).reason).toContain('不是今天')
  })

  it('allows an active multi-day segment to continue night running today', () => {
    const activeMultiDay = task({
      actionable_slot: {
        ...task().actionable_slot!,
        plan_start: '2026-07-20T08:30:00',
        plan_end: '2026-07-22T22:00:00',
      },
    })

    expect(getNightRunEligibility(activeMultiDay, '20:00', dayjs('2026-07-21T15:30:00'))).toEqual({
      isEligible: true,
      reason: '',
    })
  })
})
