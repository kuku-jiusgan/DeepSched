import dayjs from 'dayjs'
import type { TimeSlot } from '@/types'

export interface MergeableSlot extends TimeSlot {
  mergedSlotIds?: number[]
  isOverdueDisplay?: boolean
  isBridgeReservation?: boolean
}

/** 同一任务在同一台仪器上首尾相接的色块合成一条，避免按天切出来的相邻块画成一串碎条。 */
export function mergeContinuousSlots<T extends MergeableSlot>(
  sourceSlots: T[],
  hasDelay: (slot: TimeSlot) => boolean,
): MergeableSlot[] {
  const sortedSlots = sourceSlots
    .filter((slot): slot is T & { instrument_id: number } => slot.instrument_id !== null)
    .sort((a, b) => {
      if (a.instrument_id !== b.instrument_id) return a.instrument_id - b.instrument_id
      const startDiff = dayjs(a.plan_start).valueOf() - dayjs(b.plan_start).valueOf()
      if (startDiff !== 0) return startDiff
      return a.id - b.id
    })

  const merged: MergeableSlot[] = []
  for (const slot of sortedSlots) {
    const lastSlot = merged[merged.length - 1]
    if (lastSlot && canMergeSlots(lastSlot, slot, hasDelay)) {
      lastSlot.plan_end = slot.plan_end
      lastSlot.actual_end = slot.actual_end || lastSlot.actual_end
      lastSlot.mergedSlotIds = [...(lastSlot.mergedSlotIds || [lastSlot.id]), slot.id]
      continue
    }
    merged.push({ ...slot, mergedSlotIds: [slot.id] })
  }
  return merged
}

export function canMergeSlots(
  current: MergeableSlot,
  next: TimeSlot,
  hasDelay: (slot: TimeSlot) => boolean,
): boolean {
  const nextSlot = next as MergeableSlot
  if (current.isBridgeReservation || nextSlot.isBridgeReservation) return false
  // 夜间运行不能和白天的块合并。合并会把整条按前一块的白天属性来画，而白天的块
  // 要被裁到 08:30–20:00 之内：20:00 之后那段就整个消失了，图上看起来当天 20 点
  // 就收工，实际结束时间却写着次日凌晨。它本来就是单独记录的一段仪器占用。
  if (Boolean(current.is_night_run) !== Boolean(nextSlot.is_night_run)) return false
  return current.instrument_id === next.instrument_id
    && current.task_id === next.task_id
    && current.status === next.status
    && current.tier === next.tier
    && !current.isOverdueDisplay
    && !nextSlot.isOverdueDisplay
    && !hasDelay(current)
    && !hasDelay(next)
    && dayjs(current.plan_end).isSame(dayjs(next.plan_start))
}
