import { describe, expect, it } from 'vitest'
import type { TimeSlot } from '@/types'
import { mergeContinuousSlots, type MergeableSlot } from './instrumentGanttSlotMerge'

const noDelay = () => false

function slot(overrides: Partial<MergeableSlot>): MergeableSlot {
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
    assignee_id: 4,
    project_id: 1,
    ...overrides,
  } as MergeableSlot
}

describe('仪器甘特图色块合并', () => {
  it('夜间运行不与紧接着的白天时间槽合并', () => {
    const daytime = slot({ id: 5121 })
    const nightRun = slot({
      id: 5699,
      plan_start: '2026-09-02T20:00:00',
      plan_end: '2026-09-03T04:00:00',
      is_night_run: true,
    })

    const merged = mergeContinuousSlots([daytime, nightRun], noDelay)

    // 合并成一条的话，整条会按白天块的规则被裁到 20:00，20:00 之后那段就没了。
    expect(merged).toHaveLength(2)
    expect(merged[0].plan_end).toBe('2026-09-02T20:00:00')
    expect(merged[1].is_night_run).toBe(true)
    expect(merged[1].plan_end).toBe('2026-09-03T04:00:00')
  })

  it('同为白天且首尾相接的时间槽仍然合并成一条', () => {
    const morning = slot({ id: 1, plan_end: '2026-09-02T12:00:00' })
    const afternoon = slot({
      id: 2,
      plan_start: '2026-09-02T12:00:00',
      plan_end: '2026-09-02T20:00:00',
    })

    const merged = mergeContinuousSlots([morning, afternoon], noDelay)

    expect(merged).toHaveLength(1)
    expect(merged[0].plan_end).toBe('2026-09-02T20:00:00')
    expect(merged[0].mergedSlotIds).toEqual([1, 2])
  })

  it('两段相接的夜间运行仍然合并成一条', () => {
    const first = slot({
      id: 1,
      plan_start: '2026-09-02T20:00:00',
      plan_end: '2026-09-03T00:00:00',
      is_night_run: true,
    })
    const second = slot({
      id: 2,
      plan_start: '2026-09-03T00:00:00',
      plan_end: '2026-09-03T04:00:00',
      is_night_run: true,
    })

    const merged = mergeContinuousSlots([first, second], noDelay)

    expect(merged).toHaveLength(1)
    expect(merged[0].plan_end).toBe('2026-09-03T04:00:00')
  })

  it('报延期的时间槽不参与合并', () => {
    const first = slot({ id: 1, plan_end: '2026-09-02T12:00:00' })
    const second = slot({ id: 2, plan_start: '2026-09-02T12:00:00' })
    const delayed = (item: TimeSlot) => item.id === 2

    expect(mergeContinuousSlots([first, second], delayed)).toHaveLength(2)
  })
})
