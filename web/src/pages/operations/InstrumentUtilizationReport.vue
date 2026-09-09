<template>
  <div class="utilization-report-page">
    <header class="page-header">
      <div>
        <h2>仪器利用率报表</h2>
        <p>按统计周期查看各台仪器的可用时长、计划占用和实际运行情况。</p>
      </div>
    </header>

    <div class="report-toolbar">
      <a-range-picker v-model:value="dateRange" :disabled-date="disabledFutureDate" :allow-clear="false" format="YYYY-MM-DD" />
      <a-button type="primary" :loading="loading" @click="loadReport">查询</a-button>
      <a-button :disabled="loading" @click="resetRange">本月</a-button>
      <a-button class="export-button" :loading="exporting" @click="exportReport"><DownloadOutlined /> 导出 Excel</a-button>
    </div>

    <a-spin :spinning="loading">
      <a-table :data-source="rows" :columns="columns" row-key="instrument_id" :pagination="false" size="middle">
        <template #expandedRowRender="{ record }">
          <a-table
            :data-source="record.operators"
            :pagination="false"
            :columns="operatorColumns"
            row-key="operator_id"
            size="small"
          >
            <template #bodyCell="{ column: operatorColumn, record: operator }">
              <template v-if="operatorColumn.key === 'planned'">{{ formatHours(operator.planned_hours) }}</template>
              <template v-else-if="operatorColumn.key === 'operatorRunning'">{{ formatHours(operator.actual_run_hours) }}</template>
              <template v-else-if="operatorColumn.key === 'operatorRate'">{{ rateText(operator.utilization_rate) }}</template>
            </template>
            <template #emptyText><a-empty description="该仪器当前周期暂无人员明细" /></template>
          </a-table>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'rate'">
            <a-progress :percent="clampRate(record.actual_utilization_rate)" :stroke-color="rateColor(record.actual_utilization_rate)" size="small" />
          </template>
          <template v-else-if="column.key === 'expected'">{{ rateText(record.expected_utilization_rate) }}</template>
          <template v-else-if="column.key === 'actual'">{{ rateText(record.actual_utilization_rate) }}</template>
          <template v-else-if="column.key === 'available'">{{ formatHours(record.total_available_hours) }}</template>
          <template v-else-if="column.key === 'scheduled'">{{ formatHours(record.scheduled_hours) }}</template>
          <template v-else-if="column.key === 'running'">{{ formatHours(record.actual_run_hours) }}</template>
        </template>
        <template #emptyText><a-empty description="当前周期暂无仪器利用率数据" /></template>
      </a-table>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import dayjs, { type Dayjs } from 'dayjs'
import { message } from 'ant-design-vue'
import { DownloadOutlined } from '@ant-design/icons-vue'
import { exportInstrumentUtilizationReport, getInstrumentUtilizationReport } from '@/services/api'
import type { UtilizationStats } from '@/types'

type DateRange = [Dayjs, Dayjs]
const dateRange = ref<DateRange>([dayjs().startOf('month'), dayjs()])
const rows = ref<UtilizationStats[]>([])
const loading = ref(false)
const exporting = ref(false)
const columns = [
  { title: '仪器编码', dataIndex: 'instrument_code', key: 'code', width: 180 },
  { title: '仪器名称', dataIndex: 'instrument_name', key: 'name', width: 220 },
  { title: '可用时长(h)', key: 'available', width: 130 },
  { title: '计划占用(h)', key: 'scheduled', width: 130 },
  { title: '实际运行(h)', key: 'running', width: 130 },
  { title: '预期利用率', key: 'expected', width: 140 },
  { title: '实际利用率', key: 'actual', width: 140 },
  { title: '利用率', key: 'rate', width: 180 },
]
const operatorColumns = [
  { title: '执行人', dataIndex: 'operator_name', key: 'operator', width: 220 },
  { title: '计划占用(h)', key: 'planned', width: 160 },
  { title: '实际运行(h)', key: 'operatorRunning', width: 160 },
  { title: '利用率', key: 'operatorRate', width: 160 },
]

function queryParams() {
  return {
    start_date: dateRange.value[0].format('YYYY-MM-DD'),
    end_date: dateRange.value[1].format('YYYY-MM-DD'),
  }
}
function disabledFutureDate(current: Dayjs) { return current.isAfter(dayjs(), 'day') }
function resetRange() { dateRange.value = [dayjs().startOf('month'), dayjs()]; void loadReport() }
function clampRate(value: number) { return Math.max(0, Math.min(100, Number(value) || 0)) }
function rateText(value: number) { return `${clampRate(value)}%` }
function rateColor(value: number) { return clampRate(value) >= 80 ? '#165c4a' : clampRate(value) >= 50 ? '#d48806' : '#4388ef' }
function formatHours(value: number) { return `${Number(value || 0).toFixed(1)}h` }
async function loadReport() {
  loading.value = true
  try { rows.value = await getInstrumentUtilizationReport(queryParams()) }
  catch { message.error('仪器利用率报表加载失败，请稍后重试') }
  finally { loading.value = false }
}
async function exportReport() {
  exporting.value = true
  try {
    const blob = await exportInstrumentUtilizationReport(queryParams())
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `仪器利用率报表-${dayjs().format('YYYY-MM-DD')}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
    message.success('Excel 报表已导出')
  } catch { message.error('仪器利用率报表导出失败，请稍后重试') }
  finally { exporting.value = false }
}
onMounted(loadReport)
</script>

<style scoped>
.utilization-report-page { min-width: 0; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0; color: #172033; font-size: 22px; font-weight: 650; }
.page-header p { margin: 6px 0 0; color: #667085; font-size: 13px; }
.report-toolbar { display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 8px 0; border-top: 1px solid #e5e7eb; }
.export-button { margin-left: auto; }
@media (max-width: 768px) { .report-toolbar { align-items: stretch; flex-direction: column; } .export-button { margin-left: 0; } }
</style>
