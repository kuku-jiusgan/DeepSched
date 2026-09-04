import { describe, expect, it } from 'vitest'
import {
  showsExecutionSections,
  slotActualRangeText,
  slotPlanRangeText,
  taskActualEndText,
  taskActualStartText,
  type TooltipSlot,
} from './instrumentGanttTooltipText'

function slot(overrides: Partial<TooltipSlot>): TooltipSlot {
  return {
    id: 1,
    task_id: 416,
    instrument_id: 4,
    plan_start: '2026-09-02T08:30:00',
    plan_end: '2026-09-02T20:00:00',
    is_night_run: false,
    tier: 'confirmed',
    status: 'completed',
    task_name: '方法开发',
    task_status: 'paused',
    delay_status: 'not_delayed',
    ...overrides,
  } as TooltipSlot
}

describe('仪器甘特图悬浮框时间文案', () => {
  it('任务整体的实际开始取任务级，不退回本段', () => {
    const item = slot({
      task_actual_start: '2026-08-31T15:56:34',
      actual_start: '2026-09-02T08:30:00',
    })

    expect(taskActualStartText(item)).toBe('08-31 15:56:34')
  })

  it('任务没完成时实际结束按任务状态给文字，不拿本段结束兜底', () => {
    const item = slot({
      task_actual_end: null,
      actual_end: '2026-09-03T04:00:00',
      task_status: 'paused',
    })

    expect(taskActualEndText(item)).toBe('未完成（已暂停）')
  })

  it('任务完成后实际结束取任务级', () => {
    const item = slot({ task_actual_end: '2026-09-05T17:20:10', task_status: 'completed' })

    expect(taskActualEndText(item)).toBe('09-05 17:20:10')
  })

  it('本段实际按这一条时间槽算，未开始和进行中各有文案', () => {
    expect(slotActualRangeText(slot({ actual_start: null, actual_end: null }))).toBe('未开始')
    expect(
      slotActualRangeText(slot({ actual_start: '2026-09-02T08:30:00', actual_end: null })),
    ).toBe('09-02 08:30 – 进行中')
    expect(
      slotActualRangeText(
        slot({ actual_start: '2026-09-02T08:30:00', actual_end: '2026-09-02T20:00:00' }),
      ),
    ).toBe('09-02 08:30 – 09-02 20:00')
  })

  it('计划区间用色块被改写前的原始计划值', () => {
    const item = slot({
      plan_start: '2026-09-02T08:30:00',
      plan_end: '2026-09-03T04:00:00',
      originalPlanStart: '2026-09-02T08:30:00',
      originalPlanEnd: '2026-09-02T20:00:00',
    })

    expect(slotPlanRangeText(item)).toBe('09-02 08:30 – 09-02 20:00')
  })

  it('仪器故障和桥接占位不套这套分组', () => {
    expect(showsExecutionSections(slot({}))).toBe(true)
    expect(showsExecutionSections(slot({ status: 'fault' }))).toBe(false)
    expect(showsExecutionSections({ ...slot({}), isBridgeReservation: true })).toBe(false)
  })
})
