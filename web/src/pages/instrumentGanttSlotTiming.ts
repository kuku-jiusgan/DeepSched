import type { TimeSlot } from '@/types'


export function displayStartForNonCompletedSlot(slot: TimeSlot): string {
  return slot.actual_start && slot.actual_end
    ? slot.actual_start
    : slot.plan_start
}
