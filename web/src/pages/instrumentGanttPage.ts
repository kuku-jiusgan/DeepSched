
import { ref, computed, nextTick } from 'vue'
import type { CSSProperties, Component } from 'vue'
import { LeftOutlined, RightOutlined, FullscreenOutlined, FullscreenExitOutlined, ExperimentOutlined, EditOutlined, CheckSquareOutlined, DotChartOutlined, FileTextOutlined } from '@ant-design/icons-vue'
import type { Instrument, InstrumentBridgeReservation, TimeSlot, PendingApprovalSegment } from '@/types'
import { taskStatusLabel } from '@/utils/statusMeta'
import dayjs from 'dayjs'
import { centerGanttTimelineOnCurrentTime } from './kanban/ganttTimelineScroll'
import { useGanttAutoScroll } from './kanban/useGanttAutoScroll'
import { useInstrumentGanttData, type InstrumentGanttViewMode } from './useInstrumentGanttData'
import { buildTrailingDelayRanges } from './instrumentGanttDelayRanges'
import { displayStartForNonCompletedSlot } from './instrumentGanttSlotTiming'
import { mergeContinuousSlots } from './instrumentGanttSlotMerge'

const LEFT_WIDTH = 250
const HEADER_HEIGHT = 50
const WEEK_QUARTER_ROW_HEIGHT = 42
const WEEK_SEGMENT_COUNT = 3
const WEEK_SEGMENT_HOURS = 8
const WEEK_SEGMENT_SECONDS = WEEK_SEGMENT_HOURS * 60 * 60
const WORKDAY_START_HOUR = 8
const WORKDAY_START_MINUTE = 30
const WORKDAY_END_HOUR = 20
const ENTITY_ROW_HEIGHT = 72
const MIN_COL_WIDTH = 72
const WEEK_BAR_ICON_WIDTH = 32
const WEEK_BAR_PROJECT_WIDTH = 52
const WEEK_BAR_FULL_WIDTH = 110
const WEEK_FRAGMENT_GAP_PX = 2
const DELAY_PROBLEM_STATUSES = new Set(['blocked', 'interrupted'])

type SlotStatusKey = 'scheduled' | 'running' | 'completed' | 'paused' | 'blocked' | 'fault'
type InstrumentStatusKey = 'idle' | 'running' | 'maintenance' | 'fault' | 'disabled' | 'unknown'

interface StatusMeta {
  key: SlotStatusKey
  label: string
}

interface InstrumentStatusMeta {
  key: InstrumentStatusKey
  label: string
}

interface WeekBarSegment {
  quarter: number
  width: number
  visibleSeconds: number
}

interface WeekBarDisplay {
  showIcon: boolean
  showStatusMarker: boolean
  showLabel: boolean
  isProjectOnly: boolean
  projectText: string
  taskText: string
}

interface GanttSlot extends TimeSlot {
  mergedSlotIds?: number[]
  renderKey?: string
  renderStart?: string
  renderEnd?: string
  originalPlanEnd?: string
  originalPlanStart?: string
  isOverdueDisplay?: boolean
  faultDescription?: string
  isBridgeReservation?: boolean
}

interface TaskTiming {
  expectedEnd: dayjs.Dayjs
  actualStart: dayjs.Dayjs | null
  actualEnd: dayjs.Dayjs | null
  taskStatus: string | null
  delayHours: number
}

const slotStatusMetaMap: Record<string, StatusMeta> = {
  scheduled: { key: 'scheduled', label: '待执行' },
  pending: { key: 'scheduled', label: '待执行' },
  running: { key: 'running', label: '运行中' },
  paused: { key: 'paused', label: taskStatusLabel('paused') },
  completed: { key: 'completed', label: '已完成' },
  blocked: { key: 'blocked', label: '已阻塞' },
  interrupted: { key: 'blocked', label: '已中断' },
  fault: { key: 'fault', label: '故障停机' },
}

const instrumentStatusMetaMap: Record<string, InstrumentStatusMeta> = {
  idle: { key: 'idle', label: '空闲' },
  running: { key: 'running', label: '运行' },
  maintenance: { key: 'maintenance', label: '维护' },
  fault: { key: 'fault', label: '故障' },
  disabled: { key: 'disabled', label: '停用' },
}

export function useInstrumentGanttPage() {
const viewMode = ref<InstrumentGanttViewMode>('week')
const cursorDate = ref(dayjs().startOf('week'))
const isFullscreen = ref(false)
const hoveredSlot = ref<GanttSlot | null>(null)
const pinnedSlotId = ref<number | null>(null)
const tooltipX = ref(0)
const tooltipY = ref(0)
const tooltipStyle = computed(() => ({ left: tooltipX.value + 'px', top: tooltipY.value + 'px' }))
const containerRef = ref<HTMLElement | null>(null)
const { pendingSegments, bridgeReservations, faults, instruments, loadData: fetchData, loading, slots, taskTypeMap } = useInstrumentGanttData({
  viewMode,
  cursorDate,
  afterLoad: async () => {
    await nextTick()
    await recalc()
    if (viewMode.value === 'day') await centerGanttTimelineOnCurrentTime(containerRef, colWidth)
    if (isFullscreen.value && autoScrollEnabled.value) scheduleAutoScrollStart()
  },
})
const {
  autoScrollEnabled,
  hasVerticalOverflow,
  getMaxVerticalScroll,
  scheduleAutoScrollStart,
  toggleFullscreen,
} = useGanttAutoScroll({
  containerRef,
  isFullscreen,
  recalculate: recalc,
  refresh: fetchData,
})
const leftRef = ref<HTMLElement | null>(null)
const rightRef = ref<HTMLElement | null>(null)
const colWidth = ref(140)
const rowHeight = ref(WEEK_QUARTER_ROW_HEIGHT)
const laneMap = ref<Record<number, Record<number, number>>>({})
const laneCounts = ref<Record<number, number>>({})
let recalcPromise: Promise<void> | null = null

const flatRows = computed(() => {
  const rows: { inst: Instrument; quarter: number; isSubrow: boolean; isLast: boolean }[] = []
  for (const inst of instruments.value) {
    const qCount = viewMode.value === 'week' ? WEEK_SEGMENT_COUNT : 1
    for (let q = 0; q < qCount; q++) {
      rows.push({ inst, quarter: q, isSubrow: viewMode.value === 'week' && q > 0, isLast: q === qCount - 1 })
    }
  }
  return rows
})

const totalWidth = computed(() => colWidth.value * timeColumns.value.length)

const periodLabel = computed(() => {
  const start = cursorDate.value
  if (viewMode.value === 'day') return start.format('YYYY年MM月DD日')
  if (viewMode.value === 'week') {
    const end = start.add(6, 'day')
    return `${start.format('MM/DD')} - ${end.format('MM/DD')}`
  }
  return start.format('YYYY年MM月')
})

interface TimeCol {
  key: string; label: string; subLabel: string; isWeekend: boolean; isToday: boolean; isCurrent: boolean
  start: dayjs.Dayjs; end: dayjs.Dayjs
}

const timeColumns = computed<TimeCol[]>(() => {
  const cols: TimeCol[] = []
  const now = dayjs()
  const today = now.format('YYYY-MM-DD')
  if (viewMode.value === 'day') {
    for (let h = 0; h < 24; h++) {
      const d = cursorDate.value.hour(h)
      cols.push({
        key: 'h' + h, label: String(h).padStart(2, '0') + ':00', subLabel: '', isWeekend: false,
        isToday: d.format('YYYY-MM-DD') === today,
        isCurrent: d.format('YYYY-MM-DD') === today && h === now.hour(),
        start: d,
        end: d.add(1, 'hour')
      })
    }
  } else if (viewMode.value === 'week') {
    for (let i = 0; i < 7; i++) {
      const d = cursorDate.value.add(i, 'day')
      const dow = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
      cols.push({
        key: 'd' + i, label: dow, subLabel: d.format('MM/DD'),
        isWeekend: d.day() === 0 || d.day() === 6,
        isToday: d.format('YYYY-MM-DD') === today,
        isCurrent: d.format('YYYY-MM-DD') === today,
        start: d.startOf('day'),
        end: d.endOf('day')
      })
    }
  } else {
    const daysInMonth = cursorDate.value.daysInMonth()
    for (let i = 0; i < daysInMonth; i++) {
      const d = cursorDate.value.date(i + 1)
      const dow = ['日', '一', '二', '三', '四', '五', '六'][d.day()]
      cols.push({
        key: 'd' + i, label: (i + 1) + '日', subLabel: '周' + dow,
        isWeekend: d.day() === 0 || d.day() === 6,
        isToday: d.format('YYYY-MM-DD') === today,
        isCurrent: d.format('YYYY-MM-DD') === today,
        start: d.startOf('day'),
        end: d.endOf('day')
      })
    }
  }
  return cols
})

const taskTimingMap = computed(() => {
  const timingMap = new Map<number, TaskTiming>()
  for (const slot of slots.value) {
    const planEnd = dayjs(slot.plan_end)
    const actualStart = slot.actual_start ? dayjs(slot.actual_start) : null
    const actualEnd = slot.actual_end ? dayjs(slot.actual_end) : null
    const delayHours = Number(slot.delay_hours || 0)
    const current = timingMap.get(slot.task_id)
    timingMap.set(slot.task_id, {
      expectedEnd: !current || planEnd.isAfter(current.expectedEnd) ? planEnd : current.expectedEnd,
      actualStart: actualStart && (!current?.actualStart || actualStart.isBefore(current.actualStart))
        ? actualStart
        : (current?.actualStart || null),
      actualEnd: actualEnd && (!current?.actualEnd || actualEnd.isAfter(current.actualEnd))
        ? actualEnd
        : (current?.actualEnd || null),
      taskStatus: slot.task_status || current?.taskStatus || null,
      delayHours: Math.max(current?.delayHours || 0, delayHours),
    })
  }
  return timingMap
})

const taskDelayRangesMap = computed(() => {
  const result = new Map<number, Array<[dayjs.Dayjs, dayjs.Dayjs]>>()
  for (const [taskId, timing] of taskTimingMap.value) {
    const delayHours = timing.delayHours
    if (delayHours <= 0) continue
    const taskRanges = slots.value
      .filter(slot => slot.task_id === taskId)
      .map(slot => ({ start: dayjs(slot.plan_start), end: dayjs(slot.plan_end) }))
    result.set(taskId, buildTrailingDelayRanges(taskRanges, delayHours * 60))
  }
  return result
})

const displaySlots = computed<GanttSlot[]>(() =>
  mergeContinuousSlots(
    splitSlotsAroundExecutedOccupancy(toDisplaySlots([...slots.value, ...faultDisplaySlots.value, ...bridgeDisplaySlots.value])),
    hasDelay,
  )
    .filter(slot =>
      dayjs(slot.plan_end).isAfter(dayjs(slot.plan_start))
      || (slot.status === 'running' && slot.actual_start && !slot.actual_end),
    ),
)

const slotsByInstrument = computed(() => {
  const map = new Map<number, GanttSlot[]>()
  for (const slot of displaySlots.value) {
    if (!slot.instrument_id) continue
    const items = map.get(slot.instrument_id) || []
    items.push(slot)
    map.set(slot.instrument_id, items)
  }
  return map
})

const slotsByQuarter = computed(() => {
  const map = new Map<string, GanttSlot[]>()
  if (viewMode.value !== 'week') return map
  for (const slot of displaySlots.value) {
    if (!slot.instrument_id) continue
    for (let quarter = 0; quarter < WEEK_SEGMENT_COUNT; quarter++) {
      const fragments = buildQuarterFragments(slot, quarter)
      if (!fragments.length) continue
      const key = quarterSlotKey(slot.instrument_id, quarter)
      const items = map.get(key) || []
      items.push(...fragments)
      map.set(key, items)
    }
  }
  return map
})

const faultDisplaySlots = computed<TimeSlot[]>(() =>
  faults.value
    .map(faultToDisplaySlot)
    .filter((slot): slot is TimeSlot => Boolean(slot)),
)

const bridgeDisplaySlots = computed<GanttSlot[]>(() =>
  bridgeReservations.value.map(bridge => {
    const sourceSlot = slots.value
      .filter(slot => slot.task_id === bridge.task_id && slot.status !== 'cancelled')
      .sort((a, b) => dayjs(b.plan_start).valueOf() - dayjs(a.plan_start).valueOf())[0]
    return {
    id: -bridge.id, task_id: bridge.task_id, instrument_id: bridge.instrument_id,
    plan_start: bridge.plan_start, plan_end: bridge.plan_end,
    actual_start: sourceSlot?.actual_start, actual_end: sourceSlot?.actual_end,
    tier: sourceSlot?.tier || 'confirmed', status: sourceSlot?.status || 'scheduled',
    execution_status: sourceSlot?.execution_status || sourceSlot?.status || 'scheduled', is_night_run: false,
    task_name: bridge.task_name, task_type: bridge.task_type, task_status: 'scheduled',
    project_id: bridge.project_id, project_code: bridge.project_code, project_name: bridge.project_name || undefined,
    assignee_id: bridge.assignee_id || null, assignee_name: bridge.assignee_name || undefined,
    isBridgeReservation: true, renderKey: `bridge-${bridge.id}`,
    }
  }),
)

function computeLanes() {
  const map: Record<number, Record<number, number>> = {}
  const counts: Record<number, number> = {}
  for (const inst of instruments.value) {
    const instSlots = displaySlots.value.filter(s => s.instrument_id === inst.id).sort((a, b) => dayjs(a.plan_start).valueOf() - dayjs(b.plan_start).valueOf())
    const lanes: { end: dayjs.Dayjs }[] = []
    const assign: Record<number, number> = {}
    for (const slot of instSlots) {
      const start = dayjs(slot.plan_start)
      let placed = false
      for (let i = 0; i < lanes.length; i++) {
        if (start.isAfter(lanes[i].end) || start.isSame(lanes[i].end)) {
          lanes[i].end = dayjs(slot.plan_end)
          assign[slot.id] = i
          placed = true
          break
        }
      }
      if (!placed) {
        assign[slot.id] = lanes.length
        lanes.push({ end: dayjs(slot.plan_end) })
      }
    }
    map[inst.id] = assign
    counts[inst.id] = Math.max(1, lanes.length)
  }
  laneMap.value = map
  laneCounts.value = counts
}

function getLeftRowStyle(row: { isSubrow: boolean }): CSSProperties {
  if (viewMode.value === 'week' && !row.isSubrow) {
    return { height: rowHeight.value * WEEK_SEGMENT_COUNT + 'px' }
  }
  if (viewMode.value === 'week' && row.isSubrow) {
    return { height: '0', overflow: 'hidden', padding: '0', border: 'none' }
  }
  return { height: Math.max(12, rowHeight.value) + 'px' }
}

function getSegmentStartHour(quarter: number) {
  return quarter * WEEK_SEGMENT_HOURS
}

function getSegmentEndHour(quarter: number) {
  return Math.min(24, getSegmentStartHour(quarter) + WEEK_SEGMENT_HOURS)
}

function getSegmentLabel(quarter: number) {
  const start = String(getSegmentStartHour(quarter)).padStart(2, '0')
  const end = String(getSegmentEndHour(quarter)).padStart(2, '0')
  return `${start}-${end}`
}

function getBarClasses(slot: GanttSlot, quarter?: number) {
  const statusMeta = getSlotStatusMeta(displayStatus(slot))
  return [
    'status-' + statusMeta.key,
    {
      'is-compact': isCompactBar(slot, quarter),
      'has-delay': hasDelay(slot),
      'is-bridge-reservation': Boolean(slot.isBridgeReservation),
    },
  ]
}

function displayStatus(slot: GanttSlot) {
  if (slot.execution_status) return slot.execution_status
  const taskStatus = taskTimingMap.value.get(slot.task_id)?.taskStatus
  if (taskStatus === 'running' && ['blocked', 'interrupted'].includes(slot.status)) return 'running'
  return slot.status
}

function getSlotsForQuarter(instId: number, quarter: number) {
  if (viewMode.value !== 'week') return getSlotsForInstrument(instId)
  return slotsByQuarter.value.get(quarterSlotKey(instId, quarter)) || []
}

function getSlotsForInstrument(instId: number) {
  return (slotsByInstrument.value.get(instId) || []).filter(slot => {
    // Zero-length execution anchors are retained by the API for audit/history,
    // but must not render as a second bar in the instrument gantt.
    return dayjs(slot.plan_end).isAfter(dayjs(slot.plan_start))
      || (slot.status === 'running' && slot.actual_start && !slot.actual_end)
  })
}

function quarterSlotKey(instId: number, quarter: number) {
  return `${instId}:${quarter}`
}

function buildQuarterFragments(slot: GanttSlot, quarter: number) {
  const start = dayjs(slot.plan_start)
  const end = dayjs(slot.plan_end)
  const fragments: GanttSlot[] = []
  for (const col of timeColumns.value) {
    const dayStart = col.start
    const segmentStart = dayStart.hour(getSegmentStartHour(quarter))
    const segmentEnd = dayStart.hour(getSegmentEndHour(quarter))
    const qStart = slot.is_night_run
      ? segmentStart
      : (segmentStart.isBefore(dayStart.hour(WORKDAY_START_HOUR).minute(WORKDAY_START_MINUTE))
        ? dayStart.hour(WORKDAY_START_HOUR).minute(WORKDAY_START_MINUTE)
        : segmentStart)
    const qEnd = slot.is_night_run
      ? segmentEnd
      : (segmentEnd.isAfter(dayStart.hour(WORKDAY_END_HOUR))
        ? dayStart.hour(WORKDAY_END_HOUR)
        : segmentEnd)
    if (!qEnd.isAfter(qStart)) continue
    if (!end.isAfter(qStart) || !start.isBefore(qEnd)) continue
    const fragmentStart = start.isAfter(qStart) ? start : qStart
    const fragmentEnd = end.isBefore(qEnd) ? end : qEnd
    fragments.push({
      ...slot,
      renderKey: `${slot.id}-${quarter}-${col.key}`,
      renderStart: fragmentStart.toISOString(),
      renderEnd: fragmentEnd.toISOString(),
    })
  }
  return fragments
}

/** 待方案签批的工时段：按工作日历铺在该仪器最后一个已排时间块之后。
    后端算好起止时刻，前端按普通时间块的方式渲染，长度即真实占用跨度，
    一眼能看出这些活会做到哪一天。每个项目单独一段，不合并。 */
function getPendingSegments(instrumentId: number, quarter?: number) {
  return pendingSegments.value
    .filter(segment => segment.instrument_id === instrumentId)
    .map(segment => ({
      segment,
      style: getBarStyle(
        {
          id: -(segment.task_id * 100 + segment.segment_index),
          instrument_id: instrumentId,
          plan_start: segment.plan_start,
          plan_end: segment.plan_end,
        } as unknown as GanttSlot,
        quarter,
      ),
    }))
    .filter(item => !('display' in item.style))
}

function pendingSegmentTitle(segment: PendingApprovalSegment) {
  const start = dayjs(segment.plan_start).format('MM-DD HH:mm')
  const end = dayjs(segment.plan_end).format('MM-DD HH:mm')
  return `${segment.project_code} ${segment.project_name}\n${segment.task_name} ${segment.hours}h\n待方案签批后排入，预计 ${start} → ${end}`
}

function getBarStyle(slot: GanttSlot, quarter?: number) {
  const start = dayjs(slot.renderStart || slot.plan_start)
  const end = dayjs(slot.renderEnd || slot.plan_end)
  const cols = timeColumns.value
  const cw = colWidth.value

  let startCol = -1, endCol = -1
  for (let i = 0; i < cols.length; i++) {
    if (startCol === -1 && end.isAfter(cols[i].start) && start.isBefore(cols[i].end)) startCol = i
    if (end.isAfter(cols[i].start) && start.isBefore(cols[i].end)) endCol = i
  }
  if (startCol === -1 || endCol === -1) return { display: 'none' }

  const colStart = cols[startCol].start
  const colDuration = cols[startCol].end.diff(colStart, 'second', true)
  const startOffset = Math.max(0, start.diff(colStart, 'second', true)) / Math.max(1, colDuration)

  const endColStart = cols[endCol].start
  const endColDuration = cols[endCol].end.diff(endColStart, 'second', true)
  const endOffset = Math.min(1, end.diff(endColStart, 'second', true) / Math.max(1, endColDuration))

  const left = (startCol + startOffset) * cw
  const right = (endCol + endOffset) * cw

  // In week view, position within the current 8-hour segment row.
  if (viewMode.value === 'week' && quarter !== undefined) {
    let barStartCol = -1, barEndCol = -1
    for (let i = 0; i < cols.length; i++) {
      const dayStart = cols[i].start
      const qStart = dayStart.hour(getSegmentStartHour(quarter))
      const qEnd = dayStart.hour(getSegmentEndHour(quarter))
      if (end.isAfter(qStart) && start.isBefore(qEnd)) {
        if (barStartCol === -1) barStartCol = i
        barEndCol = i
      }
    }
    if (barStartCol === -1) return { display: 'none' }
    const firstDayStart = cols[barStartCol].start
    const firstQStart = firstDayStart.hour(getSegmentStartHour(quarter))
    const firstQEnd = firstDayStart.hour(getSegmentEndHour(quarter))
    const clampedStart = start.isBefore(firstQStart) ? firstQStart : start
    const firstOffset = clampedStart.diff(firstQStart, 'second', true) / WEEK_SEGMENT_SECONDS
    const lastDayStart = cols[barEndCol].start
    const lastQStart = lastDayStart.hour(getSegmentStartHour(quarter))
    const lastQEnd = lastDayStart.hour(getSegmentEndHour(quarter))
    const clampedEnd = end.isAfter(lastQEnd) ? lastQEnd : end
    const lastOffset = clampedEnd.diff(lastQStart, 'second', true) / WEEK_SEGMENT_SECONDS
    const barLeft = (barStartCol + firstOffset) * cw + WEEK_FRAGMENT_GAP_PX / 2
    const barRight = (barEndCol + lastOffset) * cw - WEEK_FRAGMENT_GAP_PX / 2
    return {
      left: barLeft + 'px',
      width: Math.max(3, barRight - barLeft) + 'px',
      top: '4px',
      height: Math.max(28, rowHeight.value - 8) + 'px',
    }
  }

  const instrumentId = slot.instrument_id
  if (instrumentId === null) return { display: 'none' }
  const lane = (laneMap.value[instrumentId] || {})[slot.id] || 0
  const laneCount = laneCounts.value[instrumentId] || 1
  const laneH = Math.max(30, Math.floor((rowHeight.value - 8) / laneCount))
  const top = lane * laneH + 4

  return { left: left + 'px', width: Math.max(3, right - left) + 'px', top: top + 'px', height: Math.max(24, laneH - 4) + 'px' }
}

const taskIconMap: Record<string, Component> = {
  FFKF_001: ExperimentOutlined,
  QCFA_001: EditOutlined,
  FFYZ_001: CheckSquareOutlined,
  SJCL_001: DotChartOutlined,
  ZXBG_001: FileTextOutlined,
}
function getTaskIcon(code: string | null | undefined) { return code ? (taskIconMap[code] || null) : null }
function getTaskTypeLabel(code: string | null | undefined) { return code ? (taskTypeMap.value[code] || code) : '' }
function getSlotStatusMeta(status: string): StatusMeta {
  return slotStatusMetaMap[status] || (status === 'delayed'
    ? { key: 'blocked', label: '延期' }
    : { key: 'scheduled', label: status || '待执行' })
}
function getInstrumentStatusMeta(status: string): InstrumentStatusMeta {
  return instrumentStatusMetaMap[status] || { key: 'unknown', label: status || '未知' }
}
function getBarProjectText(slot: TimeSlot) {
  if (slot.status === 'fault') return '故障'
  return slot.project_code || '-'
}
function getBarTaskText(slot: TimeSlot) {
  if (slot.status === 'fault') return (slot as GanttSlot).faultDescription || '仪器故障'
  const taskName = slot.task_name || '-'
  const ownerName = slot.assignee_name || '-'
  const delayText = hasDelay(slot) ? ` · 延期${slot.delay_hours || ''}h` : ''
  return `${taskName} · ${ownerName}${delayText}`
}
function isCompactBar(slot: TimeSlot, quarter?: number) {
  return getRenderedBarWidth(slot, quarter) < WEEK_BAR_PROJECT_WIDTH
}

function hasDelay(slot: TimeSlot) {
  if ((slot as GanttSlot).isBridgeReservation) return false
  const slotStart = dayjs(slot.plan_start)
  const slotEnd = dayjs(slot.plan_end)
  return getDelayRanges(slot).some(([start, end]) => slotEnd.isAfter(start) && slotStart.isBefore(end))
}

function hasPausedExecution(slot: TimeSlot) {
  if ((slot as GanttSlot).isBridgeReservation) return false
  const timing = taskTimingMap.value.get(slot.task_id)
  return slot.status === 'paused' && Boolean(timing?.actualStart && timing.actualEnd)
}

function getPausedExecutionStyle(slot: TimeSlot, quarter?: number): CSSProperties {
  const visibleRange = getVisibleSlotRange(slot, quarter)
  const timing = taskTimingMap.value.get(slot.task_id)
  if (!visibleRange || !timing?.actualStart || !timing.actualEnd) return { display: 'none' }
  const overlapStart = timing.actualStart.isAfter(visibleRange[0]) ? timing.actualStart : visibleRange[0]
  const overlapEnd = timing.actualEnd.isBefore(visibleRange[1]) ? timing.actualEnd : visibleRange[1]
  if (!overlapEnd.isAfter(overlapStart)) return { display: 'none' }
  const visibleMinutes = visibleRange[1].diff(visibleRange[0], 'minute', true)
  return {
    left: `${overlapStart.diff(visibleRange[0], 'minute', true) / visibleMinutes * 100}%`,
    width: `${overlapEnd.diff(overlapStart, 'minute', true) / visibleMinutes * 100}%`,
  }
}

function getDelaySegmentStyle(slot: TimeSlot, quarter?: number): CSSProperties {
  const visibleRange = getVisibleSlotRange(slot, quarter)
  if (!visibleRange) return { display: 'none' }
  const delayRange = getDelayRanges(slot).find(
    ([start, end]) => visibleRange[1].isAfter(start) && visibleRange[0].isBefore(end),
  )
  if (!delayRange) return { display: 'none' }
  const overlapStart = delayRange[0].isAfter(visibleRange[0]) ? delayRange[0] : visibleRange[0]
  const overlapEnd = delayRange[1].isBefore(visibleRange[1]) ? delayRange[1] : visibleRange[1]
  if (!overlapEnd.isAfter(overlapStart)) return { display: 'none' }
  const visibleMinutes = visibleRange[1].diff(visibleRange[0], 'minute', true)
  const leftRatio = overlapStart.diff(visibleRange[0], 'minute', true) / visibleMinutes
  const widthRatio = overlapEnd.diff(overlapStart, 'minute', true) / visibleMinutes
  return { left: `${leftRatio * 100}%`, width: `${widthRatio * 100}%` }
}

function getDelayRanges(slot: TimeSlot): Array<[dayjs.Dayjs, dayjs.Dayjs]> {
  // 延期显示必须以正式“延期使用”申请为准。实际执行超过计划时间
  // 只代表执行结果，不应自动生成甘特图红色延期区间。
  const slotDelayHours = Number(slot.delay_hours || 0)
  if (slotDelayHours <= 0) return []
  return taskDelayRangesMap.value.get(slot.task_id) || []
}

function isTerminalTaskSlot(slot: TimeSlot, timing: TaskTiming) {
  const ganttSlot = slot as GanttSlot
  const originalEnd = dayjs(ganttSlot.originalPlanEnd || slot.plan_end)
  return originalEnd.isSame(timing.expectedEnd)
}

function getWeekBarDisplay(slot: TimeSlot, quarter?: number): WeekBarDisplay {
  const width = getRenderedBarWidth(slot, quarter)
  const projectText = getBarProjectText(slot)
  const taskText = getBarTaskText(slot)
  if (width >= WEEK_BAR_FULL_WIDTH) {
    return { showIcon: true, showStatusMarker: false, showLabel: true, isProjectOnly: false, projectText, taskText }
  }
  if (width >= WEEK_BAR_PROJECT_WIDTH) {
    return { showIcon: false, showStatusMarker: false, showLabel: true, isProjectOnly: true, projectText, taskText: '' }
  }
  if (width >= WEEK_BAR_ICON_WIDTH) {
    return { showIcon: true, showStatusMarker: false, showLabel: false, isProjectOnly: false, projectText: '', taskText: '' }
  }
  return {
    showIcon: false,
    showStatusMarker: true,
    showLabel: false,
    isProjectOnly: false,
    projectText: '',
    taskText: '',
  }
}

function getRenderedBarWidth(slot: TimeSlot, quarter?: number) {
  const style = getBarStyle(slot as GanttSlot, quarter)
  const width = typeof style.width === 'string' ? Number.parseFloat(style.width) : Number(style.width)
  return Number.isFinite(width) ? width : 0
}

function getVisibleSlotRange(slot: TimeSlot, quarter?: number): [dayjs.Dayjs, dayjs.Dayjs] | null {
  const cols = timeColumns.value
  if (!cols.length) return null
  const ganttSlot = slot as GanttSlot
  const start = dayjs(ganttSlot.renderStart || slot.plan_start)
  const end = dayjs(ganttSlot.renderEnd || slot.plan_end)
  let visibleStart = start.isAfter(cols[0].start) ? start : cols[0].start
  let visibleEnd = end.isBefore(cols[cols.length - 1].end) ? end : cols[cols.length - 1].end

  if (viewMode.value === 'week' && quarter !== undefined) {
    const matchingRanges = cols
      .map(col => [
        col.start.hour(getSegmentStartHour(quarter)),
        col.start.hour(getSegmentEndHour(quarter)),
      ] as const)
      .filter(([rangeStart, rangeEnd]) => end.isAfter(rangeStart) && start.isBefore(rangeEnd))
    if (!matchingRanges.length) return null
    const quarterStart = matchingRanges[0][0]
    const quarterEnd = matchingRanges[matchingRanges.length - 1][1]
    visibleStart = visibleStart.isAfter(quarterStart) ? visibleStart : quarterStart
    visibleEnd = visibleEnd.isBefore(quarterEnd) ? visibleEnd : quarterEnd
  }

  return visibleEnd.isAfter(visibleStart) ? [visibleStart, visibleEnd] : null
}
function getDelayText(slot: TimeSlot) {
  const hoursText = slot.delay_hours ? `${slot.delay_hours}h` : ''
  return [hoursText, slot.delay_reason || '未填写原因'].filter(Boolean).join(' · ')
}

function statusLabel(slot: TimeSlot) {
  return getSlotStatusMeta(slot.execution_status || slot.status).label
}

function showTooltip(slot: GanttSlot, e: MouseEvent) {
  if (pinnedSlotId.value !== null) return
  hoveredSlot.value = slot
  tooltipX.value = e.clientX + 12
  tooltipY.value = e.clientY - 100
}
function hideTooltip() {
  if (pinnedSlotId.value === null) hoveredSlot.value = null
}
function toggleTooltip(slot: GanttSlot, e: MouseEvent | KeyboardEvent) {
  if (pinnedSlotId.value === slot.id) {
    dismissTooltip()
    return
  }
  pinnedSlotId.value = slot.id
  hoveredSlot.value = slot
  if (e instanceof MouseEvent) {
    tooltipX.value = e.clientX + 12
    tooltipY.value = e.clientY - 100
    return
  }
  const target = e.currentTarget
  if (target instanceof HTMLElement) {
    const bounds = target.getBoundingClientRect()
    tooltipX.value = bounds.right + 12
    tooltipY.value = bounds.top
  }
}
function dismissTooltip() {
  pinnedSlotId.value = null
  hoveredSlot.value = null
}

/** 把一条显示色块按后端给出的工作日历分段切开。

    时间槽的计划／实际窗口允许跨越周末（一个周五开始、周一才结束的任务，数据上
    保留完整起止是正确的），但画出来不能横跨周末——必须拆成"周五一段 + 周一一段"，
    中间留空。分段由后端 display_spans 给出；夜间运行的槽后端返回整条不拆，因为
    那段时间仪器确实被占用着。 */
function splitByWorkingSpans(slot: GanttSlot): GanttSlot[] {
  const spans = slot.display_spans
  if (!spans?.length) return [slot]
  const start = dayjs(slot.plan_start)
  const end = dayjs(slot.plan_end)
  const pieces = spans
    .map(([spanStart, spanEnd]) => ({
      from: dayjs(spanStart).isAfter(start) ? dayjs(spanStart) : start,
      to: dayjs(spanEnd).isBefore(end) ? dayjs(spanEnd) : end,
    }))
    .filter(piece => piece.to.isAfter(piece.from))
  // 显示窗口完全落在非工作时段时不要把色块弄没了，退回原样画一条。
  if (!pieces.length) return [slot]
  return pieces.map(piece => ({
    ...slot,
    plan_start: piece.from.toISOString(),
    plan_end: piece.to.toISOString(),
  }))
}

function toDisplaySlots(sourceSlots: TimeSlot[]): GanttSlot[] {
  return sourceSlots.flatMap(slot => {
    if (slot.status === 'fault') return [{ ...slot, originalPlanStart: slot.plan_start, originalPlanEnd: slot.plan_end }]
    if (slot.status !== 'completed') {
      const originalPlanEnd = dayjs(slot.plan_end)
      const originalPlanStart = slot.plan_start
      const timing = taskTimingMap.value.get(slot.task_id)
      const isTerminal = Boolean(timing && isTerminalTaskSlot(slot, timing))
      const taskActualEnd = isTerminal ? timing?.actualEnd : null
      const displayEnd = taskActualEnd?.isAfter(originalPlanEnd)
        ? taskActualEnd
        : (['paused', 'interrupted'].includes(slot.status) && slot.actual_end
          ? dayjs(slot.actual_end)
          : originalPlanEnd)
      const displayStart = dayjs(displayStartForNonCompletedSlot(slot))
      const displaySlot = {
        ...slot,
        originalPlanStart,
        originalPlanEnd: slot.plan_end,
        plan_start: displayStart.toISOString(),
      }
      return [{ ...displaySlot, plan_end: displayEnd.toISOString() }]
    }
    // Night-run slots represent a separately recorded instrument occupancy.
    // They do not own task-level actual timestamps, so retain their planned
    // interval instead of hiding them with incomplete completed slots.
    if (slot.is_night_run) {
      return [{
        ...slot,
        originalPlanStart: slot.plan_start,
        originalPlanEnd: slot.plan_end,
      }]
    }
    if (!slot.actual_start || !slot.actual_end) return []
    return [{
      ...slot,
      originalPlanStart: slot.plan_start,
      originalPlanEnd: slot.plan_end,
      plan_start: slot.actual_start,
      plan_end: slot.actual_end,
    }]
  }).flatMap(splitByWorkingSpans)
}

function splitSlotsAroundExecutedOccupancy(sourceSlots: GanttSlot[]): GanttSlot[] {
  const executedSlots = sourceSlots
    .filter(slot => slot.status === 'completed' && slot.instrument_id && slot.plan_start && slot.plan_end)
    .map(slot => ({
      slot,
      start: dayjs(slot.plan_start),
      end: dayjs(slot.plan_end),
    }))
    .filter(item => item.end.isAfter(item.start))

  return sourceSlots.flatMap(slot => {
    if (slot.status === 'completed' || slot.status === 'fault' || slot.isOverdueDisplay || !slot.instrument_id) return [slot]
    const start = dayjs(slot.plan_start)
    const end = dayjs(slot.plan_end)
    const overlaps = executedSlots
      .filter(item =>
        item.slot.instrument_id === slot.instrument_id
        && item.slot.task_id !== slot.task_id
        && item.end.isAfter(start)
        && item.start.isBefore(end),
      )
      .sort((left, right) => left.start.valueOf() - right.start.valueOf())
    if (!overlaps.length) return [slot]

    const fragments: GanttSlot[] = []
    let cursor = start
    for (const overlap of overlaps) {
      const cutStart = overlap.start.isAfter(start) ? overlap.start : start
      const cutEnd = overlap.end.isBefore(end) ? overlap.end : end
      if (cutStart.isAfter(cursor)) {
        fragments.push({
          ...slot,
          renderKey: `${slot.renderKey || slot.id}-before-${cutStart.valueOf()}`,
          plan_start: cursor.toISOString(),
          plan_end: cutStart.toISOString(),
        })
      }
      if (cutEnd.isAfter(cursor)) cursor = cutEnd
    }
    if (cursor.isBefore(end)) {
      fragments.push({
        ...slot,
        renderKey: `${slot.renderKey || slot.id}-after-${cursor.valueOf()}`,
        plan_start: cursor.toISOString(),
        plan_end: end.toISOString(),
      })
    }
    return fragments.length ? fragments : [slot]
  })
}

async function switchView(mode: 'day' | 'week' | 'month') {
  viewMode.value = mode
  if (mode === 'month') cursorDate.value = dayjs().startOf('month')
  else if (mode === 'week') cursorDate.value = dayjs().startOf('week')
  else cursorDate.value = dayjs().startOf('day')
  updateRowHeight()
  await fetchData()
}

async function goPrev() {
  if (viewMode.value === 'day') cursorDate.value = cursorDate.value.subtract(1, 'day')
  else if (viewMode.value === 'week') cursorDate.value = cursorDate.value.subtract(1, 'week')
  else cursorDate.value = cursorDate.value.subtract(1, 'month')
  await fetchData()
}

async function goNext() {
  if (viewMode.value === 'day') cursorDate.value = cursorDate.value.add(1, 'day')
  else if (viewMode.value === 'week') cursorDate.value = cursorDate.value.add(1, 'week')
  else cursorDate.value = cursorDate.value.add(1, 'month')
  await fetchData()
}

async function goToday() {
  if (viewMode.value === 'month') cursorDate.value = dayjs().startOf('month')
  else if (viewMode.value === 'week') cursorDate.value = dayjs().startOf('week')
  else cursorDate.value = dayjs().startOf('day')
  await fetchData()
}

function recalc() {
  if (recalcPromise) return recalcPromise
  const currentRecalc = (async () => {
    await nextTick()
    await new Promise<void>(resolve => {
      setTimeout(resolve, 50)
    })
    if (containerRef.value && containerRef.value.clientHeight > 0) {
      computeLanes()
      updateRowHeight()
      const available = containerRef.value.clientWidth - LEFT_WIDTH - 2
      const cols = viewMode.value === 'day' ? 24 : viewMode.value === 'week' ? 7 : cursorDate.value.daysInMonth()
      colWidth.value = Math.max(MIN_COL_WIDTH, available / cols)
      getMaxVerticalScroll()
    }
  })()
  recalcPromise = currentRecalc
  void currentRecalc.finally(() => {
    if (recalcPromise === currentRecalc) recalcPromise = null
  })
  return currentRecalc
}

function updateRowHeight() {
  rowHeight.value = viewMode.value === 'week' ? WEEK_QUARTER_ROW_HEIGHT : ENTITY_ROW_HEIGHT
}

function faultToDisplaySlot(fault: (typeof faults.value)[number]): TimeSlot | null {
  if (!fault.instrument_id || !fault.reported_at) return null
  const end = fault.resolved_at || fault.estimated_resolved_at
  if (!end || !dayjs(end).isAfter(dayjs(fault.reported_at))) return null
  return {
    id: -fault.id,
    task_id: -fault.id,
    instrument_id: fault.instrument_id,
    plan_start: fault.reported_at,
    plan_end: end,
    actual_start: fault.reported_at,
    actual_end: fault.resolved_at || undefined,
    tier: 'fault',
    status: 'fault',
    execution_status: 'fault',
    is_night_run: true,
    task_name: '仪器故障',
    task_type: 'instrument_fault',
    task_status: 'fault',
    delay_status: 'not_delayed',
    project_code: '故障',
    project_name: '仪器故障',
    assignee_id: null,
    assignee_name: '-',
    project_id: null,
    faultDescription: fault.description,
  } as GanttSlot
}

return {
  FullscreenExitOutlined, FullscreenOutlined, LeftOutlined, RightOutlined,
  WEEK_SEGMENT_COUNT, autoScrollEnabled,
  colWidth, containerRef, dayjs, flatRows, getBarClasses, getBarProjectText, getBarStyle,
  getPendingSegments, pendingSegmentTitle,
  getDelaySegmentStyle, getDelayText, getInstrumentStatusMeta, getLeftRowStyle, getSegmentLabel,
  getSlotsForQuarter, getTaskIcon, getTaskTypeLabel, getWeekBarDisplay, goNext, goPrev, goToday,
  dismissTooltip, getPausedExecutionStyle, hasDelay, hasPausedExecution, hasVerticalOverflow, hideTooltip, hoveredSlot, instruments, isCompactBar, isFullscreen,
  leftRef, loading, periodLabel, rightRef, rowHeight, showTooltip, statusLabel, switchView,
  timeColumns, toggleFullscreen, toggleTooltip, tooltipStyle, totalWidth, viewMode,
}
}
