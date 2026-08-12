<template>
  <div class="hours-report-page">
    <header class="page-header">
      <div>
        <h2>项目工时统计报表</h2>
        <p>汇总项目总工时与实际工时，展开项目可查看任务明细。</p>
      </div>
    </header>

    <div class="report-toolbar">
      <a-input
        v-model:value="keywordInput"
        class="keyword-input"
        placeholder="项目编号、名称、客户或负责人"
        allow-clear
        @press-enter="searchReport"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-range-picker
        v-model:value="dateRange"
        :placeholder="['项目开始日期起', '项目开始日期止']"
        allow-clear
      />
      <a-button type="primary" :loading="loading" @click="searchReport">
        <template #icon><SearchOutlined /></template>
        查询
      </a-button>
      <a-button :disabled="loading" @click="resetFilters">重置</a-button>
      <a-button class="export-button" :loading="exporting" @click="exportExcel">
        <template #icon><DownloadOutlined /></template>
        导出 Excel
      </a-button>
    </div>

    <a-spin :spinning="loading">
      <section class="metric-strip" aria-label="工时汇总">
        <div class="metric-item">
          <span>项目数</span>
          <strong>{{ report?.project_count ?? 0 }}</strong>
        </div>
        <div class="metric-item">
          <span>总工时</span>
          <strong>{{ formatHours(report?.planned_hours) }}</strong>
        </div>
        <div class="metric-item">
          <span>实际工时</span>
          <strong>{{ formatHours(report?.actual_hours) }}</strong>
        </div>
        <div class="metric-item">
          <span>工时差异</span>
          <strong :class="varianceClass(totalVariance)">{{ formatSignedHours(totalVariance) }}</strong>
        </div>
      </section>

      <a-table
        class="project-hours-table"
        :columns="projectColumns"
        :data-source="report?.items ?? []"
        :pagination="{ pageSize: 10, showSizeChanger: true, showTotal: (total: number) => `共 ${total} 个项目` }"
        row-key="project_id"
        size="middle"
        :scroll="{ x: 1040 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'project'">
            <div class="project-identity">
              <strong>{{ record.project_code }}</strong>
              <span>{{ record.project_name }}</span>
            </div>
          </template>
          <template v-else-if="column.key === 'planned_hours'">
            {{ formatHours(record.planned_hours) }}
          </template>
          <template v-else-if="column.key === 'actual_hours'">
            {{ formatHours(record.actual_hours) }}
          </template>
          <template v-else-if="column.key === 'variance_hours'">
            <span :class="varianceClass(record.variance_hours)">{{ formatSignedHours(record.variance_hours) }}</span>
          </template>
        </template>

        <template #expandedRowRender="{ record }">
          <div class="task-detail-band">
            <div class="task-detail-title">任务工时明细</div>
            <a-table
              :columns="taskColumns"
              :data-source="record.tasks"
              :pagination="false"
              row-key="task_id"
              size="small"
              :scroll="{ x: 900 }"
            >
              <template #bodyCell="{ column, record: task }">
                <template v-if="column.key === 'task_name'">
                  <span class="task-name" :style="{ paddingLeft: `${task.depth * 20}px` }">
                    <span v-if="task.depth" class="task-branch">└</span>{{ task.task_name }}
                  </span>
                </template>
                <template v-else-if="column.key === 'status'">
                  <a-tag :color="taskStatusColor(task.status)">{{ taskStatusLabel(task.status) }}</a-tag>
                </template>
                <template v-else-if="column.key === 'planned_hours'">
                  {{ formatHours(task.planned_hours) }}
                </template>
                <template v-else-if="column.key === 'actual_hours'">
                  {{ formatHours(task.actual_hours) }}
                </template>
              </template>
            </a-table>
          </div>
        </template>

        <template #emptyText>
          <a-empty description="当前筛选条件下暂无项目工时数据" />
        </template>
      </a-table>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Dayjs } from 'dayjs'
import type { TableColumnsType } from 'ant-design-vue'
import { message } from 'ant-design-vue'
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { exportProjectHoursReport, getProjectHoursReport } from '@/services/api'
import type { ProjectHoursItem, ProjectHoursReport, ProjectHoursTask } from '@/types'
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'

type DateRange = [Dayjs, Dayjs] | null

const loading = ref(false)
const exporting = ref(false)
const dateRange = ref<DateRange>(null)
const appliedDateRange = ref<DateRange>(null)
const keywordInput = ref('')
const appliedKeyword = ref('')
const report = ref<ProjectHoursReport | null>(null)

const projectColumns: TableColumnsType<ProjectHoursItem> = [
  { title: '项目', key: 'project', width: 250, fixed: 'left' },
  { title: '客户', dataIndex: 'client_name', key: 'client_name', width: 150, ellipsis: true },
  { title: '负责人', dataIndex: 'manager_name', key: 'manager_name', width: 120 },
  { title: '任务数', dataIndex: 'task_count', key: 'task_count', width: 90, align: 'right' },
  { title: '总工时(h)', dataIndex: 'planned_hours', key: 'planned_hours', width: 130, align: 'right', sorter: (a, b) => a.planned_hours - b.planned_hours },
  { title: '实际工时(h)', dataIndex: 'actual_hours', key: 'actual_hours', width: 140, align: 'right', sorter: (a, b) => a.actual_hours - b.actual_hours },
  { title: '差异(h)', dataIndex: 'variance_hours', key: 'variance_hours', width: 120, align: 'right', sorter: (a, b) => a.variance_hours - b.variance_hours },
]

const taskColumns: TableColumnsType<ProjectHoursTask> = [
  { title: '顶级任务', dataIndex: 'top_level_task_name', key: 'top_level_task_name', width: 210, ellipsis: true },
  { title: '任务名称', dataIndex: 'task_name', key: 'task_name', width: 280 },
  { title: '负责人', dataIndex: 'assignee_name', key: 'assignee_name', width: 120 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 120 },
  { title: '总工时(h)', dataIndex: 'planned_hours', key: 'planned_hours', width: 120, align: 'right' },
  { title: '实际工时(h)', dataIndex: 'actual_hours', key: 'actual_hours', width: 130, align: 'right' },
]

const totalVariance = computed(() => (report.value?.actual_hours ?? 0) - (report.value?.planned_hours ?? 0))

function queryParams() {
  const params: { start_date?: string; end_date?: string; keyword?: string } = {}
  if (appliedDateRange.value) {
    params.start_date = appliedDateRange.value[0].format('YYYY-MM-DD')
    params.end_date = appliedDateRange.value[1].format('YYYY-MM-DD')
  }
  if (appliedKeyword.value) params.keyword = appliedKeyword.value
  return Object.keys(params).length ? params : undefined
}

function searchReport() {
  appliedKeyword.value = keywordInput.value.trim()
  appliedDateRange.value = dateRange.value
  loadReport()
}

function resetFilters() {
  keywordInput.value = ''
  appliedKeyword.value = ''
  dateRange.value = null
  appliedDateRange.value = null
  loadReport()
}

async function loadReport() {
  loading.value = true
  try {
    report.value = await getProjectHoursReport(queryParams())
  } catch {
    message.error('项目工时报表加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function exportExcel() {
  exporting.value = true
  try {
    const blob = await exportProjectHoursReport(queryParams())
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `项目工时统计报表-${new Date().toISOString().slice(0, 10)}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
    message.success('Excel 报表已导出')
  } catch {
    message.error('Excel 导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

function formatHours(value: number | null | undefined) {
  return `${(value ?? 0).toFixed(2)}h`
}

function formatSignedHours(value: number) {
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${value.toFixed(2)}h`
}

function varianceClass(value: number) {
  return value > 0 ? 'variance-over' : value < 0 ? 'variance-under' : ''
}

onMounted(loadReport)
</script>

<style scoped>
.hours-report-page { min-width: 0; }
.page-header { margin-bottom: 20px; padding-right: 180px; }
.page-header h2 { margin: 0; color: #172033; font-size: 22px; font-weight: 650; }
.page-header p { margin: 6px 0 0; color: #667085; font-size: 13px; }
.report-toolbar { display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 8px 0; border-top: 1px solid #e5e7eb; }
.keyword-input { width: min(320px, 100%); }
.export-button { margin-left: auto; }
.metric-strip { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); border: 1px solid #dfe3e8; border-radius: 6px; margin: 12px 0 16px; background: #fff; }
.metric-item { min-width: 0; padding: 14px 18px; border-right: 1px solid #e5e7eb; }
.metric-item:last-child { border-right: 0; }
.metric-item span { display: block; color: #667085; font-size: 12px; }
.metric-item strong { display: block; margin-top: 6px; color: #172033; font-size: 22px; font-weight: 650; }
.project-identity { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.project-identity strong { color: #1d4ed8; font-size: 13px; }
.project-identity span { overflow: hidden; color: #344054; text-overflow: ellipsis; white-space: nowrap; }
.task-detail-band { margin: -16px -16px; padding: 12px 16px 16px 48px; background: #f8fafc; }
.task-detail-title { margin-bottom: 10px; color: #344054; font-size: 13px; font-weight: 600; }
.task-name { display: inline-flex; align-items: center; min-width: 0; }
.task-branch { margin-right: 6px; color: #98a2b3; }
.variance-over { color: #c2410c !important; }
.variance-under { color: #067647 !important; }
@media (max-width: 768px) {
  .page-header { padding-right: 0; }
  .report-toolbar { align-items: stretch; flex-direction: column; }
  .keyword-input { width: 100%; }
  .export-button { margin-left: 0; }
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-item:nth-child(2) { border-right: 0; }
  .metric-item:nth-child(-n + 2) { border-bottom: 1px solid #e5e7eb; }
  .task-detail-band { padding-left: 16px; }
}
</style>
