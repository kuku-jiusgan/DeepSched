import dayjs from 'dayjs'
import type { TimeSlot } from '@/types'
import { taskStatusLabel } from '@/utils/statusMeta'

/** 悬浮框里的时间文案。

    时间槽按天被切成多段，同一个框里同时存在两种口径：任务整体的执行起止，和当前
    这一段的计划与实际。以前两者混在同一行——"实际开始"取任务级、"实际结束"任务
    没完成时又退回段级——同一行的含义会随任务状态跳变，看起来对不上。这里把两种
    口径各自算清楚，由界面分组展示，任何一行都不跨口径兜底。 */

export interface TooltipSlot extends TimeSlot {
  originalPlanStart?: string
  originalPlanEnd?: string
}

const DATE_TIME = 'MM-DD HH:mm:ss'
const PLAN_TIME = 'MM-DD HH:mm'

export function taskActualStartText(slot: TooltipSlot): string {
  return slot.task_actual_start
    ? dayjs(slot.task_actual_start).format(DATE_TIME)
    : '未开始'
}

export function taskActualEndText(slot: TooltipSlot): string {
  if (slot.task_actual_end) return dayjs(slot.task_actual_end).format(DATE_TIME)
  // 任务级结束只在任务完成后才有值，缺它就是"还没做完"，不能拿这一段的结束顶上。
  const label = taskStatusLabel(slot.task_status)
  return label ? `未完成（${label}）` : '未完成'
}

export function slotPlanRangeText(slot: TooltipSlot): string {
  const start = dayjs(slot.originalPlanStart || slot.plan_start).format(PLAN_TIME)
  const end = dayjs(slot.originalPlanEnd || slot.plan_end).format(PLAN_TIME)
  return `${start} – ${end}`
}

export function slotActualRangeText(slot: TooltipSlot): string {
  if (!slot.actual_start) return '未开始'
  const start = dayjs(slot.actual_start).format(PLAN_TIME)
  if (!slot.actual_end) return `${start} – 进行中`
  return `${start} – ${dayjs(slot.actual_end).format(PLAN_TIME)}`
}

/** 仪器故障和桥接占位没有任务执行流水，套这套分组只会显示一排"未开始"。 */
export function showsExecutionSections(slot: TooltipSlot & { isBridgeReservation?: boolean }): boolean {
  return slot.status !== 'fault' && !slot.isBridgeReservation
}
