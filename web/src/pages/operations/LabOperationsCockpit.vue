<template>
  <div ref="cockpitViewport" class="cockpit-viewport">
    <div class="cockpit-page" :style="cockpitPageStyle">
    <header class="cockpit-header">
      <div class="brand-lockup">
        <span class="brand-mark"><ExperimentOutlined /></span>
        <h1>数字实验室运行态势总览</h1>
      </div>
      <div class="header-meta">
        <span class="date-time"><CalendarOutlined />{{ now.format('YYYY-MM-DD') }}<strong>{{ now.format('HH:mm:ss') }}</strong></span>
        <span class="header-divider"></span>
        <a-dropdown placement="bottomRight">
          <button class="user-menu" type="button"><UserOutlined />{{ currentUserLabel }}<DownOutlined /></button>
          <template #overlay>
            <a-menu @click="handleUserMenu">
              <a-menu-item key="logout">退出登录</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </div>
    </header>

    <main class="cockpit-shell" :class="{ 'is-loading': isLoading }">
      <section class="kpi-grid" aria-label="核心运营指标">
        <article v-for="item in kpis" :key="item.label" class="kpi-card">
          <span class="kpi-icon" :class="item.tone"><component :is="item.icon" /></span>
          <div><span>{{ item.label }}</span><strong>{{ item.value }}<small>{{ item.unit }}</small></strong></div>
        </article>
      </section>

      <section class="overview-grid">
        <article class="instrument-panel panel-frame">
          <h2>药物分析研究所主要仪器</h2>
          <div v-if="instruments.length" class="instrument-grid">
            <article
              v-for="item in instruments"
              :key="item.id"
              class="instrument-card"
              :class="instrumentCardClass(item.code)"
            >
              <div class="instrument-heading">
                <div><span>{{ item.code }}</span><strong>{{ item.name }}</strong><small>{{ item.model || '型号未设置' }}</small></div>
                <em :class="statusClass(item)">{{ statusText(item) }}</em>
              </div>
              <div class="instrument-picture" :class="instrumentPhotoClass(item.code)">
                <img
                  :src="instrumentImage(item.code)"
                  :alt="`${item.name} ${item.model || ''}`"
                  width="800"
                  height="600"
                  decoding="async"
                />
              </div>
              <div class="utilization-row">
                <span>利用率</span><i><b :style="{ width: `${utilizationRate(item.id)}%` }"></b></i><strong>{{ utilizationRate(item.id) }}%</strong>
              </div>
            </article>
          </div>
          <a-empty v-else description="暂无可用仪器" />
        </article>

        <aside class="summary-column">
          <section class="summary-card ranking-card">
            <h2>利用率 TOP3</h2>
            <ol>
              <li v-for="(item, index) in topInstruments" :key="item.instrument_id">
                <span :class="`rank-${index + 1}`">{{ index + 1 }}</span>
                <p><small>{{ item.instrument_code || '-' }}</small><span>{{ item.instrument_name }}</span></p>
                <strong>{{ roundedRate(item.actual_utilization_rate) }}%</strong>
              </li>
            </ol>
          </section>
          <section class="summary-card instrument-feed-card">
            <h2>仪器实时运行信息</h2>
            <div v-if="instruments.length" class="instrument-feed-viewport" :style="{ '--feed-scroll-duration': feedScrollDuration }">
              <div class="instrument-feed-track">
                <div v-for="copyIndex in 2" :key="`feed-copy-${copyIndex}`" class="instrument-feed-copy">
                  <article v-for="item in instruments" :key="`feed-${copyIndex}-${item.id}`" class="instrument-feed-item">
                    <header>
                      <span class="feed-status-dot" :class="statusClass(item)"></span>
                      <div><strong>{{ item.code }}</strong><span>{{ item.name }}</span></div>
                      <em :class="statusClass(item)">{{ statusText(item) }}</em>
                    </header>
                    <dl>
                      <div><dt>品牌型号</dt><dd>{{ item.model || '未填写型号' }}</dd></div>
                      <div><dt>位置分组</dt><dd>{{ item.location || '-' }} · {{ groupText(item.group) }}</dd></div>
                      <div><dt>当前项目</dt><dd>{{ item.current_project || '暂无运行项目' }}</dd></div>
                      <div><dt>当前任务</dt><dd>{{ taskOwnerText(item.current_task, item.current_user, '未占用') }}</dd></div>
                      <div><dt>预计完成</dt><dd>{{ formatDateTime(item.current_task_end) }}</dd></div>
                      <div><dt>下一项目</dt><dd>{{ item.next_project || '暂无待执行项目' }}</dd></div>
                      <div><dt>下一任务</dt><dd>{{ taskOwnerText(item.next_task, item.next_user, '暂无待执行任务') }}</dd></div>
                      <div><dt>预计开始</dt><dd>{{ formatDateTime(item.next_start) }}</dd></div>
                      <div><dt>仪器利用率</dt><dd>{{ utilizationRate(item.id) }}%</dd></div>
                    </dl>
                  </article>
                </div>
              </div>
            </div>
            <a-empty v-else description="暂无仪器" />
          </section>
        </aside>
      </section>

      <section class="chart-grid">
        <article class="chart-card anomaly-card">
          <header><h2>延期/异常任务预警</h2></header>
          <div
            v-if="warningTasks.length"
            class="cockpit-warning-viewport"
            :style="{ '--warning-scroll-duration': warningScrollDuration }"
          >
            <div class="cockpit-warning-track" :class="{ 'is-scrolling': isWarningScrollEnabled }">
              <div v-for="copyIndex in warningCopyCount" :key="`warning-copy-${copyIndex}`" class="cockpit-warning-copy">
                <article v-for="item in warningTasks" :key="`warning-task-${copyIndex}-${item.id}`" class="cockpit-warning-row">
                  <div class="cockpit-warning-head">
                    <strong>{{ item.project_code || '未关联项目' }}</strong>
                    <em>{{ warningTaskStatus(item) }}</em>
                  </div>
                  <span>{{ item.task_name || '-' }}</span>
                  <small>{{ item.instrument_code || item.instrument_name || '-' }} · {{ item.assignee_name || '未分配' }}</small>
                  <small>{{ warningTaskReason(item) }}</small>
                </article>
              </div>
            </div>
          </div>
          <a-empty v-else description="暂无延期/异常任务" />
        </article>

        <article class="chart-card distribution-card">
          <h2>仪器状态分布</h2>
          <div class="donut-layout">
            <div class="donut" :style="donutStyle"><strong>{{ instruments.length }}</strong><span>总数</span></div>
            <ul><li><i class="running"></i>运行中 <strong>{{ runningCount }} 台 ({{ percentage(runningCount) }}%)</strong></li><li><i class="idle"></i>空闲 <strong>{{ idleCount }} 台 ({{ percentage(idleCount) }}%)</strong></li><li><i class="maint"></i>维护/故障 <strong>{{ maintenanceCount }} 台 ({{ percentage(maintenanceCount) }}%)</strong></li></ul>
          </div>
        </article>

        <article class="chart-card completion-card">
          <h2>任务完成概览</h2>
          <div class="completion-content">
            <div class="completion-ring" :style="completionRingStyle"><div><strong>{{ completionRate }}%</strong><span>本周完成率</span></div></div>
            <ul><li><i class="done"></i>已完成任务 <strong>{{ completion.completed }}</strong><small>项</small></li><li><i class="pending"></i>待执行任务 <strong>{{ completion.pending }}</strong><small>项</small></li><li><i class="late"></i>延期任务 <strong>{{ completion.late }}</strong><small>项</small></li></ul>
            <div class="bar-chart"><span>近7天任务完成趋势</span><div class="bars"><i v-for="item in completion.days" :key="item.date" :style="{ height: `${barHeight(item.value)}%` }"><b>{{ item.value }}</b><small>{{ item.date }}</small></i></div></div>
          </div>
        </article>
      </section>
    </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs, { type Dayjs } from 'dayjs'
import { CalendarOutlined, ClockCircleOutlined, DashboardOutlined, DownOutlined, ExperimentOutlined, ThunderboltOutlined, ToolOutlined, UserOutlined } from '@ant-design/icons-vue'
import { getDashboard, getInstruments, getLabStatus, getTimeslots, getUtilization, type LabStatusInstrument } from '@/services/api'
import type { DashboardData, Instrument, TimeSlot, UtilizationStats } from '@/types'
import { isTaskCompleted, taskStatusLabel } from '@/utils/statusMeta'
import './LabOperationsCockpit.css'

interface CockpitInstrument extends LabStatusInstrument { model: string | null; availability_status: Instrument['availability_status'] }
const router = useRouter()
const now = ref(dayjs())
const isLoading = ref(true)
const cockpitViewport = ref<HTMLElement | null>(null)
const cockpitScale = ref(1)
const cockpitPageHeight = ref(929)
const instruments = ref<CockpitInstrument[]>([])
const utilization = ref<UtilizationStats[]>([])
const slots = ref<TimeSlot[]>([])
const dashboard = ref<DashboardData | null>(null)
let clockTimer: ReturnType<typeof setInterval> | undefined
let dataTimer: ReturnType<typeof setInterval> | undefined
let cockpitResizeObserver: ResizeObserver | undefined
const COCKPIT_DESIGN_WIDTH = 1540
const COCKPIT_DESIGN_HEIGHT = 929
const MIN_COCKPIT_SCALE = 0.78
const MAX_COCKPIT_SCALE = 1.35
const WARNING_SCROLL_THRESHOLD = 1
const WARNING_SCROLL_SECONDS_PER_ITEM = 6
const WARNING_SCROLL_MIN_SECONDS = 18
const COCKPIT_INSTRUMENT_CODES = [
  'ZBYY-002-0001',
  'ZBYY-002-0002',
  'ZBYY-002-0004',
  'ZBYY-002-0005',
  'ZBYY-002-0006',
  'ZBYY-002-0007',
  'ZBYY-002-0011',
] as const
const COCKPIT_INSTRUMENT_CODE_SET = new Set<string>(COCKPIT_INSTRUMENT_CODES)
const COCKPIT_INSTRUMENT_ORDER = new Map<string, number>(COCKPIT_INSTRUMENT_CODES.map((code, index) => [code, index]))
const INSTRUMENT_IMAGES: Record<string, string> = {
  'ZBYY-002-0001': '/assets/instruments/ab-api5500.webp',
  'ZBYY-002-0002': '/assets/instruments/agilent-7000b.webp',
  'ZBYY-002-0004': '/assets/instruments/shimadzu-lcms-8050-0004.webp',
  'ZBYY-002-0005': '/assets/instruments/shimadzu-lcms-8050-0005.webp',
  'ZBYY-002-0006': '/assets/instruments/agilent-7800-icp-ms.webp',
  'ZBYY-002-0007': '/assets/instruments/shimadzu-gcms-tq8050-nx.webp',
  'ZBYY-002-0011': '/assets/instruments/ab-api5500-plus.webp',
}

const currentUserLabel = computed(() => { try { const user = JSON.parse(localStorage.getItem('user') || '{}') as { display_name?: string; username?: string }; return user.display_name || user.username || '系统管理员' } catch { return '系统管理员' } })
const cockpitPageStyle = computed(() => ({
  zoom: cockpitScale.value,
  width: `${COCKPIT_DESIGN_WIDTH}px`,
  minWidth: `${COCKPIT_DESIGN_WIDTH}px`,
  height: `${cockpitPageHeight.value}px`,
  minHeight: `${COCKPIT_DESIGN_HEIGHT}px`,
}))
const runningCount = computed(() => instruments.value.filter(item => statusClass(item) === 'running').length)
const idleCount = computed(() => instruments.value.filter(item => statusClass(item) === 'idle').length)
const maintenanceCount = computed(() => instruments.value.filter(item => ['maint', 'fault'].includes(statusClass(item))).length)
const delayedCount = computed(() => dashboard.value?.delayed_tasks || 0)
const kpis = computed(() => [
  { label: '仪器总数', value: instruments.value.length, unit: '台', icon: ExperimentOutlined, tone: 'blue' },
  { label: '运行中', value: runningCount.value, unit: '台', icon: ThunderboltOutlined, tone: 'green' },
  { label: '空闲仪器', value: idleCount.value, unit: '台', icon: DashboardOutlined, tone: 'blue' },
  { label: '维护/故障', value: maintenanceCount.value, unit: '台', icon: ToolOutlined, tone: 'orange' },
  { label: '延期任务', value: delayedCount.value, unit: '项', icon: ClockCircleOutlined, tone: 'purple' },
])
const cockpitInstrumentIds = computed(() => new Set(instruments.value.map(item => item.id)))
const topInstruments = computed(() => utilization.value
  .filter(item => cockpitInstrumentIds.value.has(item.instrument_id))
  .sort((a, b) => b.actual_utilization_rate - a.actual_utilization_rate)
  .slice(0, 3))
const utilizationMap = computed(() => new Map(utilization.value.map(item => [item.instrument_id, roundedRate(item.actual_utilization_rate)])))
const feedScrollDuration = computed(() => `${Math.max(instruments.value.length * 9, 42)}s`)
const warningTasks = computed(() => {
  const latestSlotByTask = new Map<number, TimeSlot>()
  for (const slot of slots.value.filter(isWarningTask)) {
    const current = latestSlotByTask.get(slot.task_id)
    if (!current || dayjs(slot.plan_end).isAfter(current.plan_end)) latestSlotByTask.set(slot.task_id, slot)
  }
  return [...latestSlotByTask.values()].sort((left, right) => warningSortTime(right) - warningSortTime(left))
})
const isWarningScrollEnabled = computed(() => warningTasks.value.length > WARNING_SCROLL_THRESHOLD)
const warningCopyCount = computed(() => isWarningScrollEnabled.value ? 2 : 1)
const warningScrollDuration = computed(() => `${Math.max(WARNING_SCROLL_MIN_SECONDS, warningTasks.value.length * WARNING_SCROLL_SECONDS_PER_ITEM)}s`)

const completion = computed(() => {
  const start = dayjs().startOf('day').subtract(6, 'day')
  const recent = slots.value.filter(slot => dayjs(slot.actual_end || slot.plan_end).isAfter(start))
  const taskIds = new Set(recent.map(slot => slot.task_id))
  const completedTaskIds = new Set(
    recent
      .filter(slot => isTaskCompleted(displayTaskStatus(slot)))
      .map(slot => slot.task_id),
  )
  const pendingTaskIds = new Set(
    recent
      .filter(slot => ['pending', 'ready', 'scheduled', 'running', 'frozen', 'waiting_external'].includes(displayTaskStatus(slot)))
      .map(slot => slot.task_id),
  )
  for (const taskId of completedTaskIds) pendingTaskIds.delete(taskId)
  const completedAtByTask = new Map<number, Dayjs>()
  for (const slot of recent.filter(item => isTaskCompleted(displayTaskStatus(item)))) {
    const completedAt = dayjs(slot.actual_end || slot.plan_end)
    const current = completedAtByTask.get(slot.task_id)
    if (!current || completedAt.isAfter(current)) completedAtByTask.set(slot.task_id, completedAt)
  }
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = start.add(index, 'day')
    return { date: date.format('MM-DD'), value: [...completedAtByTask.values()].filter(value => value.isSame(date, 'day')).length }
  })
  return { completed: completedTaskIds.size, pending: pendingTaskIds.size, late: delayedCount.value, total: taskIds.size, days }
})
const completionRate = computed(() => completion.value.total ? Math.round(completion.value.completed / completion.value.total * 100) : 100)
const completionRingStyle = computed(() => ({ background: `conic-gradient(#2878ef 0 ${completionRate.value}%, #e7eef8 ${completionRate.value}% 100%)` }))
const donutStyle = computed(() => { const running = percentage(runningCount.value); const idle = running + percentage(idleCount.value); return { background: `conic-gradient(#32b86b 0 ${running}%, #4388ef ${running}% ${idle}%, #f59a3c ${idle}% 100%)` } })

function statusClass(item: CockpitInstrument) { const value = item.status.toLowerCase(); if (value.includes('fault')) return 'fault'; if (value.includes('maint')) return 'maint'; return item.current_task || value === 'running' ? 'running' : 'idle' }
function statusText(item: CockpitInstrument) { return ({ running: '运行中', idle: '空闲', maint: '维护中', fault: '故障' } as const)[statusClass(item) as 'running' | 'idle' | 'maint' | 'fault'] }
function groupText(group: string) { return group === 'GTI_Group' ? '基因毒组' : group === 'Quality_Group' ? '质量组' : group || '未分组' }
function taskOwnerText(task: string | null | undefined, owner: string | null | undefined, fallback: string) { return task ? `${task} · ${owner || '未分配'}` : fallback }
function formatDateTime(value: string | null | undefined) { return value ? dayjs(value).format('MM-DD HH:mm') : '-' }
function percentage(value: number) { return instruments.value.length ? Math.round(value / instruments.value.length * 1000) / 10 : 0 }
function roundedRate(value: number) { return Math.max(0, Math.min(100, Math.round(Number(value) || 0))) }
function utilizationRate(id: number) { return utilizationMap.value.get(id) || 0 }
function instrumentImage(code: string) { return INSTRUMENT_IMAGES[code] || '/assets/instruments/ab-api5500.webp' }
function instrumentCardClass(code: string) {
  if (code === 'ZBYY-002-0007') return 'instrument-card-wide'
  if (code === 'ZBYY-002-0011') return 'instrument-card-wider'
  return ''
}
function instrumentPhotoClass(code: string) {
  const classes = [`instrument-photo-${code.slice(-4)}`]
  if (code === 'ZBYY-002-0006') classes.push('instrument-photo-needs-cleanup')
  return classes
}
function isWarningTask(slot: TimeSlot) {
  const executionStatus = displayTaskStatus(slot)
  return slot.delay_status === 'delayed' && !isTaskCompleted(executionStatus)
}
function warningSortTime(slot: TimeSlot) { return dayjs(slot.delay_reported_at || slot.plan_end || slot.plan_start).valueOf() }
function warningTaskStatus(slot: TimeSlot) { const delayText = slot.delay_hours && slot.delay_hours > 0 ? `延期 ${slot.delay_hours}h` : `延期 ${formatDelayDuration(slot)}`; return `${taskStatusText(displayTaskStatus(slot))} · ${delayText}` }
function warningTaskReason(slot: TimeSlot) { return slot.delay_reason || `计划结束 ${formatDateTime(slot.plan_end)}，${taskStatusText(displayTaskStatus(slot))}` }
function taskStatusText(status: string) { return taskStatusLabel(status) }
function displayTaskStatus(slot: TimeSlot) { return slot.execution_status || slot.task_status || slot.status }
function formatDelayDuration(slot: TimeSlot) { const end = slot.actual_end ? dayjs(slot.actual_end) : now.value; const minutes = Math.max(1, end.diff(dayjs(slot.plan_end), 'minute')); const hours = Math.floor(minutes / 60); const remainingMinutes = minutes % 60; return hours ? `${hours}小时${remainingMinutes ? `${remainingMinutes}分钟` : ''}` : `${minutes}分钟` }
function barHeight(value: number) { const max = Math.max(...completion.value.days.map(item => item.value), 1); return Math.max(8, value / max * 78) }
function handleUserMenu({ key }: { key: string }) { if (key === 'logout') { localStorage.removeItem('token'); localStorage.removeItem('user'); router.push('/login') } }
function updateCockpitScale(width: number, height: number) {
  const widthScale = width / COCKPIT_DESIGN_WIDTH
  const nextScale = Math.max(MIN_COCKPIT_SCALE, Math.min(MAX_COCKPIT_SCALE, widthScale))
  const nextPageHeight = Math.max(COCKPIT_DESIGN_HEIGHT, height / nextScale)
  // Ignore sub-pixel observer noise so zoom changes cannot trigger a resize loop.
  if (Math.abs(cockpitScale.value - nextScale) < 0.001 && Math.abs(cockpitPageHeight.value - nextPageHeight) < 1) return
  cockpitScale.value = nextScale
  cockpitPageHeight.value = nextPageHeight
}
function mergeInstruments(statusList: LabStatusInstrument[], baseList: Instrument[]) {
  const base = new Map(baseList.map(item => [item.id, item]))
  return statusList
    .filter(item => COCKPIT_INSTRUMENT_CODE_SET.has(item.code) && base.get(item.id)?.availability_status === 'available')
    .map(item => ({ ...item, model: base.get(item.id)?.model || null, availability_status: 'available' as const }))
    .sort((left, right) => (COCKPIT_INSTRUMENT_ORDER.get(left.code) ?? 0) - (COCKPIT_INSTRUMENT_ORDER.get(right.code) ?? 0))
}
async function loadData() {
  const requests = [
    getDashboard().then(value => { dashboard.value = value }),
    Promise.all([getLabStatus(), getInstruments({ include_unavailable: true })])
      .then(([statusList, baseList]) => { instruments.value = mergeInstruments(statusList, baseList) }),
    getUtilization().then(value => { utilization.value = value }),
    getTimeslots().then(value => { slots.value = value }),
  ]
  if (isLoading.value) void Promise.race(requests).finally(() => { isLoading.value = false })
  await Promise.allSettled(requests)
}
onMounted(() => {
  loadData()
  clockTimer = setInterval(() => { now.value = dayjs() }, 1000)
  dataTimer = setInterval(loadData, 60000)
  if (cockpitViewport.value) {
    updateCockpitScale(cockpitViewport.value.clientWidth, cockpitViewport.value.clientHeight)
    cockpitResizeObserver = new ResizeObserver((entries) => {
      const viewport = entries[0]?.contentRect
      updateCockpitScale(viewport?.width || COCKPIT_DESIGN_WIDTH, viewport?.height || COCKPIT_DESIGN_HEIGHT)
    })
    cockpitResizeObserver.observe(cockpitViewport.value)
  }
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (dataTimer) clearInterval(dataTimer)
  cockpitResizeObserver?.disconnect()
})
</script>
