<template>
  <div class="gantt-page" :class="{ 'is-fullscreen': isFullscreen }">
    <div class="page-header"><h2>项目甘特图</h2></div>

    <div class="action-bar" :class="{ 'is-screen-toolbar': isFullscreen }">
      <a-button-group>
        <a-button :type="viewMode === 'day' ? 'primary' : 'default'" @click="switchView('day')">日</a-button>
        <a-button :type="viewMode === 'week' ? 'primary' : 'default'" @click="switchView('week')">周</a-button>
        <a-button :type="viewMode === 'month' ? 'primary' : 'default'" @click="switchView('month')">月</a-button>
      </a-button-group>
      <a-button @click="goPrev"><LeftOutlined /></a-button>
      <span style="font-weight: 600; min-width: 160px; text-align: center">{{ periodLabel }}</span>
      <a-button @click="goNext"><RightOutlined /></a-button>
      <a-button @click="goToday">今天</a-button>
      <a-button @click="toggleFullscreen">
        <component :is="isFullscreen ? FullscreenExitOutlined : FullscreenOutlined" />
        {{ isFullscreen ? '退出全屏' : '全屏' }}
      </a-button>
      <a-input v-model:value="filterKeyword" placeholder="搜索项目编号/名称" allowClear style="width: 200px" />
      
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />

    <div v-else-if="!filteredProjects.length" style="padding: 80px; text-align: center; color: #94a3b8">
      暂无仪器数据，请先在「项目台账管理」中添加仪器并生成排程
    </div>

    <div v-else class="gantt-container" :class="{ 'is-week-view': viewMode === 'week' }" ref="containerRef">
      <div class="gantt-left">
        <div class="gantt-header-cell">项目</div>
        <div v-for="row in flatRows" :key="'l-' + row.inst.id + '-q' + row.quarter"
          class="gantt-left-row" :class="{ 'is-subrow': row.isSubrow, 'is-last': row.isLast }"
          :style="getProjectRowStyle(row.inst.id)">
          <template v-if="!row.isSubrow || viewMode !== 'week'">
            <div class="proj-name">{{ row.inst.name }}</div>
            <div class="proj-code">{{ row.inst.code }}</div>
          </template>
        </div>
      </div>

      <div class="gantt-right">
        <div class="gantt-timeline-header" :style="{ width: totalWidth + 'px' }">
          <div v-for="col in timeColumns" :key="col.key" class="gantt-col-header" :style="{ width: colWidth + 'px' }"
            :class="{ 'is-weekend': col.isWeekend, 'is-today': col.isToday }">
            <div class="col-label-primary">{{ col.label }}</div>
            <div v-if="col.subLabel" class="col-label-sub">{{ col.subLabel }}</div>
          </div>
        </div>
        <div class="gantt-timeline-body" :style="{ width: totalWidth + 'px' }">
          <div v-for="row in flatRows" :key="'r-' + row.inst.id + '-q' + row.quarter"
            class="gantt-entity-row" :class="{ 'is-subrow': row.isSubrow, 'is-last': row.isLast }"
            :style="getProjectRowStyle(row.inst.id)">
            <div v-for="col in timeColumns" :key="col.key" class="gantt-grid-cell"
              :style="{ width: colWidth + 'px' }" :class="{ 'is-weekend': col.isWeekend, 'is-today': col.isToday }" />
            <div v-for="slot in getSlotsForQuarter(row.inst.id, row.quarter)" :key="slot.id"
              class="gantt-bar" :class="getBarClasses(slot)"
              :style="getBarStyle(slot, row.quarter)"
              @mouseenter="e => showTooltip(slot, e)"
              @mouseleave="hideTooltip">
              <span v-if="hasDelay(slot)" class="bar-delay-segment" :style="getDelaySegmentStyle(slot, row.quarter)">
                <span class="bar-delay-badge">延</span>
              </span>
              <span class="bar-tag">
                <span class="bar-status-dot"></span>
                <component :is="getTaskIcon(slot.task_type)" />
              </span>
              <span class="bar-label">
                <span class="bar-instrument">{{ getBarInstrumentText(slot) }}</span>
                <span class="bar-task">{{ getBarTaskText(slot) }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="hoveredSlot" class="gantt-tooltip" :style="tooltipStyle">
      <div class="tooltip-title">{{ hoveredSlot.task_name }}</div>
      <div class="tooltip-row"><span>工序</span>{{ getTaskTypeLabel(hoveredSlot.task_type) }}</div>
      <div v-if="hoveredSlot.task_type !== 'approval_gate'" class="tooltip-row"><span>所需仪器</span>{{ getBarInstrumentText(hoveredSlot) }}</div>
      <div v-if="hoveredSlot.task_type !== 'approval_gate'" class="tooltip-row"><span>负责人</span>{{ hoveredSlot.assignee_name || '-' }}</div>
      <div class="tooltip-row"><span>开始</span>{{ dayjs(hoveredSlot.plan_start).format('MM-DD HH:mm') }}</div>
      <div class="tooltip-row"><span>结束</span>{{ dayjs(hoveredSlot.plan_end).format('MM-DD HH:mm') }}</div>
      <div class="tooltip-row"><span>状态</span>{{ statusLabel(hoveredSlot.status) }}</div>
      <div v-if="hoveredSlot.task_type === 'approval_gate'" class="tooltip-row"><span>最迟签批</span>{{ hoveredSlot.approval_latest_at ? dayjs(hoveredSlot.approval_latest_at).format('MM-DD HH:mm') : '-' }}</div>
      <div v-if="hoveredSlot.task_type === 'approval_gate'" class="tooltip-row"><span>解锁任务</span>{{ hoveredSlot.approval_unlock_tasks?.map(task => task.name).join('、') || '-' }}</div>
      <div v-if="hasDelay(hoveredSlot)" class="tooltip-row is-delay"><span>延期</span>{{ getDelayText(hoveredSlot) }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import type { CSSProperties } from 'vue'
import { message } from 'ant-design-vue'
import { LeftOutlined, RightOutlined, FullscreenOutlined, FullscreenExitOutlined, ExperimentOutlined, EditOutlined, CheckSquareOutlined, DotChartOutlined, FileTextOutlined } from '@ant-design/icons-vue'
import { getApprovalGates, getProjects, getTimeslots, getTaskTypes, type TaskTypeConfig } from '@/services/api'
import type { ApprovalGate, Project, TimeSlot } from '@/types'
import dayjs from 'dayjs'
import { centerGanttTimelineOnCurrentTime } from './ganttTimelineScroll'
import { buildProjectDisplaySlots, buildProjectLaneLayout } from './projectGanttLayout'

const loading = ref(true)
const route = useRoute()
const projects = ref<Project[]>([])
const slots = ref<TimeSlot[]>([])
const viewMode = ref<'day' | 'week' | 'month'>('week')
const cursorDate = ref(dayjs().startOf('week'))
const isFullscreen = ref(false)
const hoveredSlot = ref<TimeSlot | null>(null)
const tooltipX = ref(0)
const tooltipY = ref(0)
const tooltipStyle = computed(() => ({ left: tooltipX.value + 'px', top: tooltipY.value + 'px' }))
const containerRef = ref<HTMLElement | null>(null)
const colWidth = ref(140)
const rowHeight = ref(200)
const BASE_PROJECT_ROW_HEIGHT = 35
const DAY_PROJECT_ROW_HEIGHT = 48
const PROJECT_LANE_HEIGHT = 28
const DELAY_PROBLEM_STATUSES = new Set(['blocked', 'interrupted'])
const WEEK_SEGMENT_COUNT = 3
const WEEK_SEGMENT_HOURS = 8
const WEEK_SEGMENT_SECONDS = WEEK_SEGMENT_HOURS * 60 * 60
const taskTypeMap = ref<Record<string, string>>({})
const laneMap = ref<Record<number, Record<number, number>>>({})
const laneCounts = ref<Record<number, number>>({})
const filterKeyword = ref('')

const filteredProjects = computed(() => {
  const kw = filterKeyword.value.toLowerCase()
  if (!kw) return projects.value
  return projects.value.filter(p => p.code.toLowerCase().includes(kw) || p.name.toLowerCase().includes(kw))
})

const displaySlots = computed(() => buildProjectDisplaySlots(slots.value))

const flatRows = computed(() => {
  const rows: { inst: Project; quarter: number; isSubrow: boolean; isLast: boolean }[] = []
  for (const inst of filteredProjects.value) {
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
  key: string; label: string; subLabel: string; isWeekend: boolean; isToday: boolean
  start: dayjs.Dayjs; end: dayjs.Dayjs
}

const timeColumns = computed<TimeCol[]>(() => {
  const cols: TimeCol[] = []
  const today = dayjs().format('YYYY-MM-DD')
  if (viewMode.value === 'day') {
    for (let h = 0; h < 24; h++) {
      const d = cursorDate.value.hour(h)
      cols.push({
        key: 'h' + h, label: String(h).padStart(2, '0') + ':00', subLabel: '', isWeekend: false,
        isToday: d.format('YYYY-MM-DD') === today, start: d, end: d.add(1, 'hour')
      })
    }
  } else if (viewMode.value === 'week') {
    for (let i = 0; i < 7; i++) {
      const d = cursorDate.value.add(i, 'day')
      const dow = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
      cols.push({
        key: 'd' + i, label: dow, subLabel: d.format('MM/DD'),
        isWeekend: d.day() === 0 || d.day() === 6,
        isToday: d.format('YYYY-MM-DD') === today, start: d.startOf('day'), end: d.endOf('day')
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
        isToday: d.format('YYYY-MM-DD') === today, start: d.startOf('day'), end: d.endOf('day')
      })
    }
  }
  return cols
})

function computeLanes() {
  const layout = buildProjectLaneLayout(
    filteredProjects.value.map(project => project.id),
    displaySlots.value,
  )
  laneMap.value = layout.laneMap
  laneCounts.value = layout.laneCounts
}

function getProjectRowStyle(projectId: number) {
  return { height: `${getProjectRowHeight(projectId)}px` }
}

function getProjectRowHeight(projectId: number) {
  const laneCount = laneCounts.value[projectId] || 1
  const minimumHeight = viewMode.value === 'day' ? DAY_PROJECT_ROW_HEIGHT : BASE_PROJECT_ROW_HEIGHT
  return Math.max(minimumHeight, laneCount * PROJECT_LANE_HEIGHT + 4)
}

function getProjectLaneStyle(slot: TimeSlot) {
  const projectId = slot.project_id
  const lane = projectId ? (laneMap.value[projectId] || {})[slot.id] || 0 : 0
  const laneCount = projectId ? laneCounts.value[projectId] || 1 : 1
  const laneHeight = (getProjectRowHeight(projectId || 0) - 4) / laneCount
  return {
    top: `${lane * laneHeight + 2}px`,
    height: `${laneHeight - 2}px`,
  }
}

function getSegmentStartHour(segment: number) {
  return segment * WEEK_SEGMENT_HOURS
}

function getSegmentEndHour(segment: number) {
  return Math.min(24, getSegmentStartHour(segment) + WEEK_SEGMENT_HOURS)
}

function getSlotsForQuarter(instId: number, quarter: number) {
  if (viewMode.value !== 'week') return getSlotsForProject(instId)
  const cols = timeColumns.value
  return getSlotsForProject(instId).filter(s => {
    const start = dayjs(s.plan_start)
    const end = dayjs(s.plan_end)
    for (const col of cols) {
      const dayStart = col.start
      const qStart = dayStart.hour(getSegmentStartHour(quarter))
      const qEnd = dayStart.hour(getSegmentEndHour(quarter))
      if (end.isAfter(qStart) && start.isBefore(qEnd)) return true
    }
    return false
  })
}

function getSlotsForProject(instId: number) {
  return displaySlots.value.filter(s => s.project_id === instId)
}

function getBarStyle(slot: TimeSlot, quarter?: number) {
  const start = dayjs(slot.plan_start)
  const end = dayjs(slot.plan_end)
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

  // In week view with quarters, position within the quarter sub-row
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
    const barLeft = (barStartCol + firstOffset) * cw
    const barRight = (barEndCol + lastOffset) * cw
    return {
      left: barLeft + 'px',
      width: Math.max(3, barRight - barLeft) + 'px',
      ...getProjectLaneStyle(slot),
    }
  }
  
  return {
    left: left + 'px',
    width: Math.max(3, right - left) + 'px',
    ...getProjectLaneStyle(slot),
  }
}

const taskIconMap: Record<string, any> = {
  FFKF_001: ExperimentOutlined,
  QCFA_001: EditOutlined,
  FFYZ_001: CheckSquareOutlined,
  SJCL_001: DotChartOutlined,
  ZXBG_001: FileTextOutlined,
}
function getTaskIcon(code: string | null | undefined) { return code ? (taskIconMap[code] || null) : null }
function getTaskTypeLabel(code: string | null | undefined) { return code ? (taskTypeMap.value[code] || code) : '' }
function getBarInstrumentText(slot: TimeSlot) {
  if (slot.task_type === 'approval_gate') return '方案签批'
  return slot.instrument_code || slot.instrument_name || '未指定仪器'
}
function getBarTaskText(slot: TimeSlot) {
  if (slot.task_type === 'approval_gate') return statusLabel(slot.status)
  const taskName = slot.task_name || '-'
  const ownerName = slot.assignee_name || '-'
  const delayText = hasDelay(slot) ? ` · 延期${slot.delay_hours || ''}h` : ''
  return `${taskName} · ${ownerName}${delayText}`
}
function getBarClasses(slot: TimeSlot) {
  const status = slot.status === 'pending'
    ? 'scheduled'
    : slot.status === 'interrupted' ? 'blocked' : slot.status
  return [`status-${status}`, { 'has-delay': hasDelay(slot) }]
}
function hasDelay(slot: TimeSlot) {
  const delayHours = Number(slot.delay_hours || 0)
  const hasDelayReport = Boolean(slot.delay_reason) || delayHours > 0
  if (!hasDelayReport) return false
  if (DELAY_PROBLEM_STATUSES.has(slot.status)) return true
  return slot.status === 'running' && Boolean(slot.actual_start)
}
function getDelaySegmentStyle(slot: TimeSlot, quarter?: number): CSSProperties {
  if (!slot.delay_hours || slot.delay_hours <= 0) return { display: 'none' }
  const start = dayjs(slot.plan_start)
  const end = dayjs(slot.plan_end)
  const delayStart = end.subtract(slot.delay_hours, 'hour')
  const visibleRange = getVisibleBarRange(start, end, quarter)
  if (!visibleRange) return { display: 'none' }
  if (isFullDelaySlot(slot)) return { left: '0', width: '100%' }

  const [visibleStart, visibleEnd] = visibleRange
  const segmentStart = delayStart.isAfter(visibleStart) ? delayStart : visibleStart
  const segmentEnd = end.isBefore(visibleEnd) ? end : visibleEnd
  if (!segmentEnd.isAfter(segmentStart)) return { display: 'none' }

  const visibleSeconds = visibleEnd.diff(visibleStart, 'second', true)
  const leftPercent = segmentStart.diff(visibleStart, 'second', true) / Math.max(1, visibleSeconds) * 100
  const widthPercent = segmentEnd.diff(segmentStart, 'second', true) / Math.max(1, visibleSeconds) * 100
  return {
    left: `${leftPercent}%`,
    width: `${widthPercent}%`,
    minWidth: '8px',
    maxWidth: `calc(${100 - leftPercent}% - 1px)`,
  }
}
function isFullDelaySlot(slot: TimeSlot) {
  if (!slot.delay_hours || slot.delay_hours <= 0) return false
  const slotStart = dayjs(slot.plan_start)
  const slotEnd = dayjs(slot.plan_end)
  const slotDurationHours = slotEnd.diff(slotStart, 'hour', true)
  if (slot.delay_hours >= slotDurationHours) return true
  return slots.value.some(other =>
    other.id !== slot.id &&
    other.task_id === slot.task_id &&
    Boolean(other.plan_end) &&
    !dayjs(other.plan_end).isAfter(slotStart)
  )
}
function getVisibleBarRange(start: dayjs.Dayjs, end: dayjs.Dayjs, quarter?: number): [dayjs.Dayjs, dayjs.Dayjs] | null {
  const cols = timeColumns.value
  if (!cols.length) return null
  let visibleStart = start.isAfter(cols[0].start) ? start : cols[0].start
  let visibleEnd = end.isBefore(cols[cols.length - 1].end) ? end : cols[cols.length - 1].end

  if (viewMode.value === 'week' && quarter !== undefined) {
    let quarterStart: dayjs.Dayjs | null = null
    let quarterEnd: dayjs.Dayjs | null = null
    for (const col of cols) {
      const qStart = col.start.hour(getSegmentStartHour(quarter))
      const qEnd = col.start.hour(getSegmentEndHour(quarter))
      if (end.isAfter(qStart) && start.isBefore(qEnd)) {
        if (!quarterStart) quarterStart = qStart
        quarterEnd = qEnd
      }
    }
    if (!quarterStart || !quarterEnd) return null
    visibleStart = visibleStart.isAfter(quarterStart) ? visibleStart : quarterStart
    visibleEnd = visibleEnd.isBefore(quarterEnd) ? visibleEnd : quarterEnd
  }

  return visibleEnd.isAfter(visibleStart) ? [visibleStart, visibleEnd] : null
}
function getDelayText(slot: TimeSlot) {
  const hoursText = slot.delay_hours ? `${slot.delay_hours}h` : ''
  return [hoursText, slot.delay_reason || '未填写原因'].filter(Boolean).join(' · ')
}

function statusLabel(s: string) {
  const m: Record<string, string> = { scheduled: '待处理', pending: '待处理', running: '运行中', completed: '已完成', blocked: '已延期', interrupted: '已延期', approval_waiting: '等待客户签批', approval_approved: '客户已同意', approval_risk: '签批已影响结题' }
  return m[s] || s
}

function approvalGateSlot(gate: ApprovalGate): TimeSlot | null {
  if (!gate.submitted_at) return null
  const end = gate.approved_at || gate.expected_approval_at
  if (!end || !dayjs(end).isAfter(dayjs(gate.submitted_at))) return null
  const status = gate.risk_status === 'deadline_risk' || gate.risk_status === 'overdue'
    ? 'approval_risk'
    : gate.gate_status === 'approved' ? 'approval_approved' : 'approval_waiting'
  return {
    id: -gate.id,
    task_id: gate.id,
    instrument_id: 0,
    plan_start: gate.submitted_at,
    plan_end: end,
    tier: 'forecast',
    status,
    task_name: gate.name,
    task_type: 'approval_gate',
    project_code: gate.project_code,
    project_name: gate.project_name,
    project_id: gate.project_id,
    assignee_id: gate.assignee_id || null,
    assignee_name: gate.assignee_name || undefined,
    approval_gate_status: gate.gate_status,
    approval_risk_status: gate.risk_status,
    approval_latest_at: gate.latest_approval_at,
    approval_unlock_tasks: gate.unlock_tasks,
  }
}

function showTooltip(slot: TimeSlot, e: MouseEvent) {
  hoveredSlot.value = slot
  tooltipX.value = e.clientX + 12
  tooltipY.value = e.clientY - 100
}
function hideTooltip() { hoveredSlot.value = null }

async function switchView(mode: 'day' | 'week' | 'month') {
  viewMode.value = mode
  if (mode === 'month') cursorDate.value = dayjs().startOf('month')
  else if (mode === 'week') cursorDate.value = dayjs().startOf('week')
  else cursorDate.value = dayjs().startOf('day')
  await recalc()
  if (mode === 'day') await centerGanttTimelineOnCurrentTime(containerRef, colWidth)
}

function goPrev() {
  if (viewMode.value === 'day') cursorDate.value = cursorDate.value.subtract(1, 'day')
  else if (viewMode.value === 'week') cursorDate.value = cursorDate.value.subtract(1, 'week')
  else cursorDate.value = cursorDate.value.subtract(1, 'month')
}

function goNext() {
  if (viewMode.value === 'day') cursorDate.value = cursorDate.value.add(1, 'day')
  else if (viewMode.value === 'week') cursorDate.value = cursorDate.value.add(1, 'week')
  else cursorDate.value = cursorDate.value.add(1, 'month')
}

async function goToday() {
  if (viewMode.value === 'month') cursorDate.value = dayjs().startOf('month')
  else if (viewMode.value === 'week') cursorDate.value = dayjs().startOf('week')
  else cursorDate.value = dayjs().startOf('day')
  await recalc()
  if (viewMode.value === 'day') await centerGanttTimelineOnCurrentTime(containerRef, colWidth)
}

async function recalc() {
  await nextTick()
  await new Promise<void>(resolve => {
    setTimeout(() => {
      if (containerRef.value && containerRef.value.clientHeight > 0) {
        computeLanes()
        rowHeight.value = 35
        const available = containerRef.value.clientWidth - 160 - 2
        const cols = viewMode.value === 'day' ? 24 : viewMode.value === 'week' ? 7 : cursorDate.value.daysInMonth()
        colWidth.value = Math.max(60, available / cols)
      }
      resolve()
    }, 50)
  })
}

async function fetchData(silent = false) {
  if (!silent) loading.value = true
  try {
    const [insts, timeslots, types, gatePage] = await Promise.all([getProjects(), getTimeslots(), getTaskTypes(), getApprovalGates({ page_size: 500 })])
    projects.value = insts
    const requestedProjectId = Number(route.query.project_id)
    if (requestedProjectId) {
      const requestedProject = insts.find(project => project.id === requestedProjectId)
      if (requestedProject) filterKeyword.value = requestedProject.code
    }
    const gateSlots = gatePage.items.map(approvalGateSlot).filter((slot): slot is TimeSlot => slot !== null)
    slots.value = [...timeslots, ...gateSlots].filter((s: TimeSlot) => typeof s.project_id === 'number' && s.project_id > 0)
    const map: Record<string, string> = {}
    types.forEach((t: TaskTypeConfig) => { map[t.code] = t.name })
    map.approval_gate = '方案签批'
    taskTypeMap.value = map
  } catch { if (!silent) message.error('加载数据失败') }
  finally {
    if (!silent) loading.value = false
    await nextTick()
    await recalc()
    if (viewMode.value === 'day') await centerGanttTimelineOnCurrentTime(containerRef, colWidth)
  }
}

let resizeObserver: ResizeObserver | null = null

let refreshTimer: ReturnType<typeof setInterval> | null = null

function toggleFullscreen() {
  if (!isFullscreen.value) {
    document.documentElement.requestFullscreen()
      .catch(() => message.error('浏览器未允许进入全屏'))
    return
  }
  document.exitFullscreen()
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
  recalc()
}

onMounted(() => {
  fetchData()
  refreshTimer = setInterval(() => fetchData(true), 30000)
  window.addEventListener('resize', recalc)
  document.addEventListener('fullscreenchange', onFullscreenChange)
  nextTick(() => {
    if (containerRef.value) {
      resizeObserver = new ResizeObserver(() => {
        if (containerRef.value && containerRef.value.clientHeight > 0) {
          recalc()
        }
      })
      resizeObserver.observe(containerRef.value)
    }
  })
})
onUnmounted(() => {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
  window.removeEventListener('resize', recalc)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null }
})
</script>

<style scoped src="./ProjectGantt.css"></style>
