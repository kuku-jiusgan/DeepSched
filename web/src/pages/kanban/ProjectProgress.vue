<template>
  <div class="progress-page">
    <header class="progress-header">
      <div>
        <h2>项目进度</h2>
        <p>对照计划与实际执行，优先识别交付风险</p>
      </div>
      <a-button :loading="isLoading" aria-label="刷新项目进度" @click="loadProgress">
        <template #icon><ReloadOutlined /></template>
        刷新
      </a-button>
    </header>

    <section class="summary-strip" aria-label="项目进度摘要">
      <div><span>项目总数</span><strong>{{ filteredItems.length }}</strong></div>
      <div><span>按期</span><strong class="text-success">{{ statusCount.on_time }}</strong></div>
      <div><span>有风险</span><strong class="text-warning">{{ statusCount.at_risk }}</strong></div>
      <div><span>预计逾期</span><strong class="text-danger">{{ statusCount.overdue }}</strong></div>
      <div class="summary-updated"><span>数据更新时间</span><strong>{{ formatDateTime(generatedAt) }}</strong></div>
    </section>

    <div class="progress-toolbar">
      <a-input v-model:value="keyword" allow-clear placeholder="搜索项目编号、名称或负责人" class="search-input">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select v-model:value="deliveryFilter" class="filter-select" aria-label="交付状态筛选">
        <a-select-option value="all">全部交付状态</a-select-option>
        <a-select-option value="overdue">预计逾期</a-select-option>
        <a-select-option value="at_risk">有风险</a-select-option>
        <a-select-option value="on_time">按期</a-select-option>
      </a-select>
      <div class="legend" aria-label="时间轨图例">
        <span><i class="legend-plan" />预计时间</span>
        <span><i class="legend-actual" />实际时间</span>
        <span><i class="legend-due" />交付日期</span>
        <span><i class="legend-today" />今天</span>
      </div>
    </div>

    <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" class="progress-alert" />
    <div v-if="isLoading" class="progress-loading"><a-skeleton active :paragraph="{ rows: 8 }" /></div>
    <a-empty v-else-if="!filteredItems.length" description="暂无符合条件的项目进度" class="progress-empty" />

    <div v-else class="progress-table-wrap">
      <div class="progress-table" :style="{ minWidth: `${tableMinWidth}px` }">
        <div class="table-head">
          <div class="project-column">项目</div>
          <div class="timeline-column">
            <span v-for="tick in timelineTicks" :key="tick.value" :style="{ left: `${tick.left}%` }">{{ tick.label }}</span>
          </div>
          <div class="result-column">预计结果</div>
        </div>

        <template v-for="item in filteredItems" :key="item.project_id">
          <button class="project-row" type="button" :aria-expanded="expandedProjectId === item.project_id" @click="toggleProject(item.project_id)">
            <div class="project-column project-identity">
              <RightOutlined class="expand-icon" />
              <div><strong>{{ item.project_code }}</strong><span>{{ item.project_name }}</span><small>{{ item.manager_name || '未指定负责人' }} · {{ item.completed_tasks }}/{{ item.total_tasks }} 项完成</small></div>
            </div>
            <div class="timeline-column dual-track">
              <TimelineGrid :range-start="rangeStart" :range-end="rangeEnd" :plan-start="item.plan_start" :plan-end="item.plan_end" :actual-start="item.actual_start" :actual-end="item.actual_end" :actual-started-at="item.actual_started_at" :due-date="item.due_date" />
            </div>
            <div class="result-column result-cell">
              <a-tag :color="deliveryMeta[item.delivery_status].color">{{ deliveryMeta[item.delivery_status].label }}</a-tag>
              <strong>{{ formatDate(item.predicted_end) }}</strong>
              <span :class="deviationClass(item.days_delta)">{{ deviationText(item.days_delta) }}</span>
            </div>
          </button>

          <div v-if="expandedProjectId === item.project_id" class="task-panel">
            <a-spin v-if="detailLoadingId === item.project_id" tip="加载任务进度..." />
            <a-alert v-else-if="detailError" type="error" show-icon :message="detailError" />
            <template v-else-if="projectDetails[item.project_id]">
              <div v-for="task in projectDetails[item.project_id].timeline.tasks" :key="task.task_id" class="task-row">
                <div class="project-column task-identity"><span>{{ task.task_name }}</span><small>{{ task.assignee_name || '未指定执行人' }}</small></div>
                <div class="timeline-column dual-track">
                  <TimelineGrid :range-start="rangeStart" :range-end="rangeEnd" :plan-start="task.plan_start || task.expected_approval_at" :plan-end="task.plan_end || task.expected_approval_at" :actual-start="task.actual_start" :actual-end="task.actual_end" :actual-started-at="task.actual_start" :is-milestone="task.is_external_gate" />
                </div>
                <div class="result-column task-status">{{ taskStatusLabel(task.status) }}</div>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import type { PropType } from 'vue'
import { ReloadOutlined, RightOutlined, SearchOutlined } from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import { getProjectHealth, getProjectProgress } from '@/services/api'
import type { ProjectDeliveryStatus, ProjectHealth, ProjectProgressOverview } from '@/services/api'

const DAY_MS = 24 * 60 * 60 * 1000
const tableMinWidth = 1040
const items = ref<ProjectProgressOverview[]>([])
const generatedAt = ref<string | null>(null)
const isLoading = ref(false)
const errorMessage = ref('')
const keyword = ref('')
const deliveryFilter = ref<ProjectDeliveryStatus | 'all'>('all')
const expandedProjectId = ref<number | null>(null)
const detailLoadingId = ref<number | null>(null)
const detailError = ref('')
const projectDetails = ref<Record<number, ProjectHealth>>({})

const deliveryMeta: Record<ProjectDeliveryStatus, { label: string; color: string }> = {
  on_time: { label: '按期', color: 'green' }, at_risk: { label: '有风险', color: 'orange' }, overdue: { label: '预计逾期', color: 'red' },
}
const filteredItems = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return items.value.filter(item => (deliveryFilter.value === 'all' || item.delivery_status === deliveryFilter.value)
    && (!query || [item.project_code, item.project_name, item.manager_name || ''].some(value => value.toLowerCase().includes(query))))
})
const statusCount = computed(() => ({
  on_time: filteredItems.value.filter(item => item.delivery_status === 'on_time').length,
  at_risk: filteredItems.value.filter(item => item.delivery_status === 'at_risk').length,
  overdue: filteredItems.value.filter(item => item.delivery_status === 'overdue').length,
}))
const allDates = computed(() => items.value.flatMap(item => [item.plan_start, item.plan_end, item.actual_start, item.actual_end, item.due_date, item.predicted_end]).filter((value): value is string => Boolean(value)))
const rangeStart = computed(() => dayjs(allDates.value.length ? Math.min(...allDates.value.map(value => dayjs(value).valueOf()), dayjs().valueOf()) : dayjs()).startOf('day').subtract(2, 'day').toISOString())
const rangeEnd = computed(() => dayjs(allDates.value.length ? Math.max(...allDates.value.map(value => dayjs(value).valueOf()), dayjs().valueOf()) : dayjs()).endOf('day').add(2, 'day').toISOString())
const timelineTicks = computed(() => Array.from({ length: 6 }, (_, index) => {
  const ratio = index / 5
  const value = dayjs(rangeStart.value).valueOf() + (dayjs(rangeEnd.value).valueOf() - dayjs(rangeStart.value).valueOf()) * ratio
  return { value, left: ratio * 100, label: dayjs(value).format('MM-DD') }
}))

async function loadProgress() {
  isLoading.value = true; errorMessage.value = ''
  try { const result = await getProjectProgress(); items.value = result.items; generatedAt.value = result.generated_at }
  catch { errorMessage.value = '项目进度加载失败，请稍后重试。' }
  finally { isLoading.value = false }
}
async function toggleProject(projectId: number) {
  if (expandedProjectId.value === projectId) { expandedProjectId.value = null; return }
  expandedProjectId.value = projectId; detailError.value = ''
  if (projectDetails.value[projectId]) return
  detailLoadingId.value = projectId
  try { projectDetails.value[projectId] = await getProjectHealth(projectId) }
  catch { detailError.value = '任务进度加载失败，请稍后重试。' }
  finally { detailLoadingId.value = null }
}
function formatDate(value: string | null) { return value ? dayjs(value).format('YYYY-MM-DD') : '未排程' }
function formatDateTime(value: string | null) { return value ? dayjs(value).format('MM-DD HH:mm') : '-' }
function deviationText(days: number) { return days > 0 ? `晚 ${days} 天` : days < 0 ? `提前 ${Math.abs(days)} 天` : '与交付日一致' }
function deviationClass(days: number) { return days > 0 ? 'text-danger' : days < 0 ? 'text-success' : '' }
function taskStatusLabel(status: string) { return ({ pending: '待开始', scheduled: '已排程', running: '进行中', completed: '已完成', done: '已完成', paused: '已暂停', blocked: '受阻' } as Record<string, string>)[status] || status }
onMounted(loadProgress)

const TimelineGrid = defineComponent({
  props: {
    rangeStart: { type: String, required: true }, rangeEnd: { type: String, required: true },
    planStart: String as PropType<string | null>, planEnd: String as PropType<string | null>,
    actualStart: String as PropType<string | null>, actualEnd: String as PropType<string | null>,
    actualStartedAt: String as PropType<string | null>, dueDate: String as PropType<string | null>, isMilestone: Boolean,
  },
  setup(props) {
    const position = (value?: string) => value ? Math.max(0, Math.min(100, (dayjs(value).valueOf() - dayjs(props.rangeStart).valueOf()) / Math.max(DAY_MS, dayjs(props.rangeEnd).valueOf() - dayjs(props.rangeStart).valueOf()) * 100)) : 0
    const barStyle = (start?: string, end?: string) => ({ left: `${position(start)}%`, width: `${Math.max(0.35, position(end) - position(start))}%` })
    return () => h('div', { class: 'timeline-grid' }, [
      h('i', { class: 'today-marker', style: { left: `${position(dayjs().toISOString())}%` } }),
      props.dueDate ? h('i', { class: 'due-marker', style: { left: `${position(props.dueDate)}%` }, title: `交付日期 ${dayjs(props.dueDate).format('YYYY-MM-DD')}` }) : null,
      props.planStart && props.planEnd ? h('i', { class: ['track-bar', 'plan-bar', { 'milestone-bar': props.isMilestone }], style: barStyle(props.planStart, props.planEnd), title: `预计 ${dayjs(props.planStart).format('MM-DD HH:mm')} - ${dayjs(props.planEnd).format('MM-DD HH:mm')}` }) : h('span', { class: 'no-plan' }, '未排程'),
      props.actualStart && props.actualEnd ? h('i', { class: 'track-bar actual-bar', style: barStyle(props.actualStart, props.actualEnd), title: `实际 ${dayjs(props.actualStart).format('MM-DD HH:mm')} - ${dayjs(props.actualEnd).format('MM-DD HH:mm')}` }) : null,
      props.actualStartedAt ? h('i', { class: 'actual-start-marker', style: { left: `${position(props.actualStartedAt)}%` }, title: `已于 ${dayjs(props.actualStartedAt).format('MM-DD HH:mm')} 开始` }) : null,
    ])
  },
})
</script>

<style scoped src="./ProjectProgress.css"></style>
