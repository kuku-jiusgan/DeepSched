<template>
  <div ref="pageRef" class="human-gantt-page" :class="{ 'is-fullscreen': isFullscreen }">
    <div class="page-header">
      <h2>人力甘特图</h2>
    </div>

    <div class="action-bar">
      <a-button-group>
        <a-button :type="viewMode === 'day' ? 'primary' : 'default'" @click="changeView('day')">日</a-button>
        <a-button :type="viewMode === 'week' ? 'primary' : 'default'" @click="changeView('week')">周</a-button>
        <a-button :type="viewMode === 'month' ? 'primary' : 'default'" @click="changeView('month')">月</a-button>
      </a-button-group>
      <a-button @click="goPrevious"><LeftOutlined /></a-button>
      <span class="period-label">{{ periodLabel }}</span>
      <a-button @click="goFollowing"><RightOutlined /></a-button>
      <a-button @click="returnToday">今天</a-button>
      <a-button @click="toggleFullscreen">
        <component :is="isFullscreen ? FullscreenExitOutlined : FullscreenOutlined" />
        {{ isFullscreen ? '退出全屏' : '全屏' }}
      </a-button>
      <a-input
        v-model:value="filterKeyword"
        allowClear
        class="people-search"
        placeholder="搜索人员姓名"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
    </div>

    <div v-if="loading" class="loading-state"><a-spin size="large" /></div>
    <div v-else-if="!visibleUsers.length" class="empty-state">
      <TeamOutlined class="empty-icon" />
      <strong>{{ filterKeyword ? '没有匹配的人员' : '暂无人员数据' }}</strong>
      <span>{{ filterKeyword ? '请调整姓名关键词' : '请先在“用户管理”中添加人员' }}</span>
    </div>

    <div v-else ref="containerRef" class="gantt-container">
      <div ref="leftRef" class="gantt-left">
        <div class="gantt-header-cell">人员</div>
        <div
          v-for="row in flatRows"
          :key="`left-${row.key}`"
          class="person-row"
          :class="{ 'is-subrow': !row.isFirstLane, 'is-last': row.isLastLane }"
        >
          <template v-if="row.isFirstLane">
            <div class="person-heading">
              <span class="person-name">{{ row.user.display_name }}</span>
              <span v-if="row.user.is_active === false" class="inactive-chip">已停用</span>
            </div>
            <div class="person-meta">
              <span>{{ row.user.role }}</span>
              <span v-if="row.laneCount > 1" class="parallel-chip">{{ row.laneCount }} 项并行</span>
            </div>
          </template>
        </div>
      </div>

      <div ref="rightRef" class="gantt-right" @scroll="syncVerticalScroll">
        <div class="timeline-header" :style="{ width: `${totalWidth}px` }">
          <div
            v-for="column in timeColumns"
            :key="column.key"
            class="timeline-column-header"
            :class="{ 'is-weekend': column.isWeekend, 'is-today': column.isToday }"
            :style="{ width: `${columnWidth}px` }"
          >
            <span class="column-primary">{{ column.label }}</span>
            <span v-if="column.subLabel" class="column-secondary">{{ column.subLabel }}</span>
          </div>
        </div>

        <div class="timeline-body" :style="{ width: `${totalWidth}px` }">
          <div
            v-for="row in flatRows"
            :key="`timeline-${row.key}`"
            class="timeline-row"
            :class="{ 'is-last': row.isLastLane }"
          >
            <div
              v-for="column in timeColumns"
              :key="`${row.key}-${column.key}`"
              class="timeline-cell"
              :class="{ 'is-weekend': column.isWeekend, 'is-today': column.isToday }"
              :style="{ width: `${columnWidth}px` }"
            />
            <div
              v-for="slot in getSlotsForRow(row)"
              :key="slot.id"
              class="gantt-bar"
              :class="[`status-${displayStatus(slot)}`, { 'has-delay': hasDelay(slot) }]"
              :style="getBarStyle(slot, row)"
              @mouseenter="showTooltip(slot, $event)"
              @mouseleave="hideTooltip"
            >
              <span v-if="hasDelay(slot)" class="bar-delay-segment" :style="getDelaySegmentStyle(slot)">
                <span class="delay-badge">延</span>
              </span>
              <span class="bar-status-dot" />
              <span class="bar-label">
                <span class="bar-project">{{ slot.project_code || slot.project_name || '未关联项目' }}</span>
                <span class="bar-task">{{ slot.task_name || '-' }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="hoveredSlot" class="gantt-tooltip" :style="tooltipStyle">
      <div class="tooltip-title">{{ hoveredSlot.task_name || '-' }}</div>
      <div class="tooltip-row"><span>负责人</span>{{ hoveredSlot.assignee_name || '-' }}</div>
      <div class="tooltip-row"><span>项目</span>{{ getProjectText(hoveredSlot) }}</div>
      <div class="tooltip-row"><span>任务类型</span>{{ getTaskTypeLabel(hoveredSlot.task_type) }}</div>
      <div class="tooltip-row"><span>仪器</span>{{ getInstrumentText(hoveredSlot) }}</div>
      <div class="tooltip-row"><span>开始</span>{{ formatDateTime(hoveredSlot.plan_start) }}</div>
      <div class="tooltip-row"><span>结束</span>{{ formatDateTime(hoveredSlot.plan_end) }}</div>
      <div class="tooltip-row"><span>状态</span>{{ getStatusLabel(displayStatus(hoveredSlot)) }}</div>
      <div v-if="hasDelay(hoveredSlot)" class="tooltip-row is-delay">
        <span>延期</span>{{ getDelayText(hoveredSlot) }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, type CSSProperties } from 'vue'
import { message } from 'ant-design-vue'
import {
  FullscreenExitOutlined,
  FullscreenOutlined,
  LeftOutlined,
  RightOutlined,
  SearchOutlined,
  TeamOutlined,
} from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import { getTaskTypes, getTimeslots, getUserDirectory, type TaskTypeConfig, type UserDirectoryEntry } from '@/services/api'
import type { TimeSlot } from '@/types'
import { taskStatusLabel } from '@/utils/statusMeta'
import { useHumanGantt, type HumanGanttViewMode } from './useHumanGantt'
import { centerGanttTimelineOnCurrentTime, scrollGanttTimelineToStart } from './ganttTimelineScroll'

const COLUMN_WIDTH = 140
const REFRESH_INTERVAL_MS = 30_000
const API_LOCAL_DATETIME_FORMAT = 'YYYY-MM-DDTHH:mm:ss'
const HIDDEN_HUMAN_GANTT_ROLES = new Set(['系统管理员', '项目管理员'])

const loading = ref(true)
const users = ref<UserDirectoryEntry[]>([])
const slots = ref<TimeSlot[]>([])
const taskTypeMap = ref<Record<string, string>>({})
const filterKeyword = ref('')
const isFullscreen = ref(false)
const hoveredSlot = ref<TimeSlot | null>(null)
const tooltipX = ref(0)
const tooltipY = ref(0)
const containerRef = ref<HTMLElement | null>(null)
const pageRef = ref<HTMLElement | null>(null)
const leftRef = ref<HTMLElement | null>(null)
const rightRef = ref<HTMLElement | null>(null)

const filteredUsers = computed(() => {
  const keyword = filterKeyword.value.trim().toLowerCase()
  if (!keyword) return users.value
  return users.value.filter(user =>
    user.display_name.toLowerCase().includes(keyword),
  )
})

const {
  viewMode,
  cursorDate,
  periodStart,
  periodEnd,
  periodLabel,
  timeColumns,
  totalWidth,
  visibleUsers,
  flatRows,
  getSlotsForRow,
  getBarStyle,
  switchView,
  goPrev,
  goNext,
  goToday,
} = useHumanGantt(slots, filteredUsers, {
  colWidth: COLUMN_WIDTH,
  rowHeight: 42,
  includeInactiveUsers: true,
})

const columnWidth = COLUMN_WIDTH
const tooltipStyle = computed(() => ({ left: `${tooltipX.value}px`, top: `${tooltipY.value}px` }))

async function changeView(mode: HumanGanttViewMode) {
  switchView(mode)
  await fetchData(true)
  await scrollToRelevantTime()
}

function goPrevious() {
  goPrev()
  fetchData(true)
  scrollToTimelineStart()
}

function goFollowing() {
  goNext()
  fetchData(true)
  scrollToTimelineStart()
}

async function returnToday() {
  goToday()
  await fetchData(true)
  await scrollToRelevantTime()
}

function scrollToTimelineStart() {
  return scrollGanttTimelineToStart(rightRef)
}

async function scrollToRelevantTime() {
  if (viewMode.value === 'day') {
    await centerGanttTimelineOnCurrentTime(rightRef, COLUMN_WIDTH)
    return
  }
  await nextTick()
  const timeline = rightRef.value
  if (!timeline) return
  const now = dayjs()
  if (!now.isAfter(periodStart.value) || !now.isBefore(periodEnd.value)) {
    timeline.scrollLeft = 0
    return
  }
  const columnIndex = now.startOf('day').diff(periodStart.value.startOf('day'), 'day')
  const currentPosition = (columnIndex + 0.5) * COLUMN_WIDTH
  timeline.scrollLeft = Math.max(0, currentPosition - timeline.clientWidth / 2)
}

function syncVerticalScroll() {
  if (leftRef.value && rightRef.value) leftRef.value.scrollTop = rightRef.value.scrollTop
}

function toggleFullscreen() {
  if (!isFullscreen.value) {
    pageRef.value?.requestFullscreen().catch(() => message.error('浏览器未允许进入全屏'))
    return
  }
  document.exitFullscreen()
}

function onFullscreenChange() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

function showTooltip(slot: TimeSlot, event: MouseEvent) {
  hoveredSlot.value = slot
  tooltipX.value = Math.min(event.clientX + 12, window.innerWidth - 280)
  tooltipY.value = Math.max(12, Math.min(event.clientY - 90, window.innerHeight - 260))
}

function hideTooltip() {
  hoveredSlot.value = null
}

function getProjectText(slot: TimeSlot) {
  return [slot.project_code, slot.project_name].filter(Boolean).join(' · ') || '-'
}

function getInstrumentText(slot: TimeSlot) {
  return [slot.instrument_code, slot.instrument_name].filter(Boolean).join(' · ') || '-'
}

function getTaskTypeLabel(code?: string | null) {
  return code ? taskTypeMap.value[code] || code : '-'
}

function getStatusLabel(status: string) {
  return taskStatusLabel(status)
}

function displayStatus(slot: TimeSlot) {
  return slot.execution_status || slot.task_status || slot.status
}

function hasDelay(slot: TimeSlot) {
  return Boolean(slot.delay_reason) || Boolean(slot.delay_hours)
}

function getDelaySegmentStyle(slot: TimeSlot): CSSProperties {
  if (!slot.delay_hours || slot.delay_hours <= 0) return { display: 'none' }
  const start = dayjs(slot.plan_start)
  const end = dayjs(slot.plan_end)
  const durationSeconds = end.diff(start, 'second', true)
  if (durationSeconds <= 0) return { display: 'none' }
  const delaySeconds = Math.min(durationSeconds, slot.delay_hours * 3600)
  const widthPercent = delaySeconds / durationSeconds * 100
  return { left: `${100 - widthPercent}%`, width: `${widthPercent}%`, minWidth: '8px' }
}

function getDelayText(slot: TimeSlot) {
  const duration = slot.delay_hours ? `${slot.delay_hours} 小时` : ''
  return [duration, slot.delay_reason || '未填写原因'].filter(Boolean).join(' · ')
}

function formatDateTime(value: string) {
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

function isHumanGanttUserVisible(user: UserDirectoryEntry) {
  const userRoles = [user.role, ...(user.roles ?? [])]
  return !userRoles.some(role => HIDDEN_HUMAN_GANTT_ROLES.has(role))
}

async function fetchData(silent = false) {
  if (isFetching) return
  isFetching = true
  if (!silent) loading.value = true
  try {
    const [userList, timeslotList, taskTypes] = await Promise.all([
      getUserDirectory(),
      getTimeslots({
        start_date: periodStart.value.format(API_LOCAL_DATETIME_FORMAT),
        end_date: periodEnd.value.format(API_LOCAL_DATETIME_FORMAT),
      }),
      getTaskTypes(),
    ])
    const visibleUserList = userList.filter(isHumanGanttUserVisible)
    const visibleUserIds = new Set(visibleUserList.map(user => user.id))
    users.value = visibleUserList
    slots.value = timeslotList.filter(
      slot => slot.assignee_id != null && visibleUserIds.has(slot.assignee_id),
    )
    taskTypeMap.value = Object.fromEntries(taskTypes.map((type: TaskTypeConfig) => [type.code, type.name]))
  } catch {
    if (!silent) message.error('加载人力甘特图失败')
  } finally {
    isFetching = false
    if (!silent) loading.value = false
  }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null
let isFetching = false

onMounted(() => {
  fetchData()
  refreshTimer = setInterval(() => fetchData(true), REFRESH_INTERVAL_MS)
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
})
</script>

<style scoped src="./HumanGantt.css"></style>
