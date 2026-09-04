import { ref, type Ref } from 'vue'
import { message } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import {
  getInstrumentFaults,
  getInstrumentBridgeReservations,
  getInstruments,
  getPendingApprovalSegments,
  getTaskTypes,
  getTimeslots,
} from '@/services/api'
import type { Instrument, InstrumentBridgeReservation, InstrumentFault, PendingApprovalSegment, TimeSlot } from '@/types'

export type InstrumentGanttViewMode = 'day' | 'week' | 'month'

interface InstrumentGanttDataOptions {
  viewMode: Ref<InstrumentGanttViewMode>
  cursorDate: Ref<Dayjs>
  afterLoad: () => void | Promise<void>
}

const REQUEST_TIMEOUT_MS = 20_000

function visibleRange(viewMode: InstrumentGanttViewMode, cursorDate: Dayjs) {
  const start = viewMode === 'month'
    ? cursorDate.startOf('month')
    : viewMode === 'week'
      ? cursorDate.startOf('week')
      : cursorDate.startOf('day')
  const end = viewMode === 'month'
    ? start.add(1, 'month')
    : viewMode === 'week'
      ? start.add(1, 'week')
      : start.add(1, 'day')
  return { start_date: start.toISOString(), end_date: end.toISOString() }
}

export function useInstrumentGanttData(options: InstrumentGanttDataOptions) {
  const loading = ref(true)
  const instruments = ref<Instrument[]>([])
  const slots = ref<TimeSlot[]>([])
  const faults = ref<InstrumentFault[]>([])
  const bridgeReservations = ref<InstrumentBridgeReservation[]>([])
  const taskTypeMap = ref<Record<string, string>>({})
  // 方案签批通过前，下游任务不进排程也不落地时间槽。甘特图要在已排时间块
  // 之后把这部分待排工时显式列出来，否则排程看起来会比真实情况乐观。
  const pendingSegments = ref<PendingApprovalSegment[]>([])
  let activeRequest: Promise<void> | null = null
  let isReloadPending = false

  async function loadData(silent = false) {
    if (activeRequest) {
      isReloadPending = true
      await activeRequest
      return
    }
    activeRequest = performLoad(silent).finally(() => {
      activeRequest = null
      if (isReloadPending) {
        isReloadPending = false
        void loadData(true)
      }
    })
    return activeRequest
  }

  async function performLoad(silent: boolean) {
    if (!silent) loading.value = true
    try {
      const range = visibleRange(options.viewMode.value, options.cursorDate.value)
      // 待签批工时段以前是等前面全部返回之后再单独发一次，白白多串一个来回。
      // 它和其余请求之间没有依赖，一起并发即可。
      const [[timeslots, reservations], [instrumentItems, faultItems, types], pending] = await Promise.all([
        Promise.all([getTimeslots(range, REQUEST_TIMEOUT_MS), getInstrumentBridgeReservations(range, REQUEST_TIMEOUT_MS)]),
        Promise.all([getInstruments(), getInstrumentFaults(), getTaskTypes()]),
        getPendingApprovalSegments(),
      ])
      pendingSegments.value = pending
      slots.value = timeslots
      bridgeReservations.value = reservations
      instruments.value = instrumentItems
      faults.value = faultItems
      taskTypeMap.value = Object.fromEntries(types.map(type => [type.code, type.name]))
    } catch (error: unknown) {
      if (!silent) {
        const isTimeout = error instanceof Error && error.message.toLowerCase().includes('timeout')
        message.error(isTimeout ? '甘特图数据加载超时，请稍后重试' : '甘特图数据加载失败')
      }
    } finally {
      if (!silent) loading.value = false
      await options.afterLoad()
    }
  }

  return { pendingSegments, bridgeReservations, faults, instruments, loadData, loading, slots, taskTypeMap }
}
