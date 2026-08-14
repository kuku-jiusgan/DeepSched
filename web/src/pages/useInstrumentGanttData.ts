import { ref, type Ref } from 'vue'
import { message } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import {
  getInstrumentFaults,
  getInstruments,
  getTaskTypes,
  getTimeslots,
} from '@/services/api'
import type { Instrument, InstrumentFault, TimeSlot } from '@/types'

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
  const taskTypeMap = ref<Record<string, string>>({})
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
      const [timeslots, [instrumentItems, faultItems, types]] = await Promise.all([
        getTimeslots(range, REQUEST_TIMEOUT_MS),
        Promise.all([getInstruments(), getInstrumentFaults(), getTaskTypes()]),
      ])
      slots.value = timeslots
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

  return { faults, instruments, loadData, loading, slots, taskTypeMap }
}
