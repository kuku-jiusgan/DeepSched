import { describe, expect, it } from 'vitest'

import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { canNightRunTask, nightRunDisabledReason, nightRunEndTime } from './todayTaskCardUtils'


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
    night_run: {
      slot_id: 22,
      start_at: '2026-07-20T20:00:00',
      is_eligible: true,
      reason: '',
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

describe('night run actions', () => {
  it('uses the backend start time even when the actual execution belongs to yesterday', () => {
    const continuing = task({ actionable_slot: {
      ...task().actionable_slot!,
      plan_start: '2026-07-19T08:30:00',
      plan_end: '2026-07-19T20:00:00',
      actual_start: '2026-07-19T08:30:00',
      status: 'running',
    } })

    expect(nightRunEndTime(continuing)?.format('YYYY-MM-DD HH:mm')).toBe('2026-07-20 20:00')
    expect(canNightRunTask(continuing)).toBe(true)
    expect(continuing.night_run?.slot_id).toBe(22)
  })

  it('uses the backend eligibility and exact error reason', () => {
    const unavailable = task({ night_run: {
      slot_id: null, start_at: null, is_eligible: false,
      reason: '当前没有可登记夜间运行的今日计划时段',
    } })

    expect(canNightRunTask(unavailable)).toBe(false)
    expect(nightRunDisabledReason(unavailable)).toBe('当前没有可登记夜间运行的今日计划时段')
    expect(nightRunEndTime(unavailable)).toBeNull()
  })

  it('disables night run when metadata is missing', () => {
    expect(canNightRunTask(task({ night_run: null }))).toBe(false)
    expect(nightRunEndTime(task({ night_run: null }))).toBeNull()
  })
})
