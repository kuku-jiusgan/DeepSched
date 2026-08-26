<template>
  <div>
    <div class="page-header">
      <h2>仪表盘</h2>
    </div>

    <div class="action-bar">
      <a-range-picker
        v-model:value="dateRange"
        :disabled-date="disabledFutureDate"
        :allow-clear="false"
        format="YYYY-MM-DD"
      />
      <a-button type="primary" :loading="loading" @click="loadDashboard">查询</a-button>
      <a-button @click="resetRange">最近 7 天</a-button>
    </div>

    <div class="stat-grid">
      <div v-for="(item, i) in stats" :key="i" class="stat-card" :class="{ 'stat-card-clickable': !!item.link }" @click="item.link && router.push(item.link)">
        <div>
          <div class="stat-card-label">{{ item.title }}</div>
          <div class="stat-card-value" :style="item.danger ? { color: '#dc2626' } : {}">{{ item.value }}</div>
          <div v-if="item.suffix" class="stat-card-suffix">{{ item.suffix }}</div>
        </div>
        <div class="stat-card-icon" :style="item.danger ? { background: '#fef2f2', color: '#dc2626' } : {}">
          <component :is="item.icon" />
        </div>
      </div>
    </div>

    <a-row :gutter="[16, 16]">
      <a-col :span="24">
        <div class="chart-card utilization-card">
          <div class="chart-card-header">
            <span>仪器使用率</span>
            <div class="utilization-legend">
              <span><i class="legend-dot expected"></i>预期使用率</span>
              <span><i class="legend-dot actual"></i>实际使用率</span>
            </div>
          </div>
          <div class="chart-card-body">
            <a-empty v-if="!utilization.length" description="暂无数据" />
            <div v-else class="utilization-chart">
              <div v-for="item in utilization" :key="item.instrument_id" class="utilization-item">
                <div class="bar-area">
                  <div class="bar-pair">
                    <a-tooltip :title="`预期使用率 ${rateText(item.expected_utilization_rate)}（计划 ${item.scheduled_hours}h / 可用 ${item.total_available_hours}h）`">
                      <div class="bar expected" :style="{ height: barHeight(item.expected_utilization_rate) }"></div>
                    </a-tooltip>
                    <a-tooltip :title="`实际使用率 ${rateText(item.actual_utilization_rate)}（实际 ${item.actual_run_hours}h / 可用 ${item.total_available_hours}h）`">
                      <div class="bar actual" :style="{ height: barHeight(item.actual_utilization_rate) }"></div>
                    </a-tooltip>
                  </div>
                </div>
                <div class="rate-line">{{ rateText(item.expected_utilization_rate) }} / {{ rateText(item.actual_utilization_rate) }}</div>
                <div class="instrument-code">{{ instrumentCode(item) }}</div>
                <a-tooltip :title="`${item.instrument_name}（${item.instrument_code || '-'}）`">
                  <div class="instrument-name">{{ item.instrument_name }}</div>
                </a-tooltip>
              </div>
            </div>
          </div>
        </div>
      </a-col>
    </a-row>

    <a-card title="仪器利用率详情" style="margin-top: 16px">
      <a-table :dataSource="utilization" :columns="utilColumns" rowKey="instrument_id"
        :pagination="false" size="small" />
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ExperimentOutlined, ProjectOutlined, ClockCircleOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import { getDashboard, getUtilization } from '@/services/api'
import type { DashboardData, UtilizationStats } from '@/types'
import dayjs, { type Dayjs } from 'dayjs'

const data = ref<DashboardData | null>(null)
const utilization = ref<UtilizationStats[]>([])
const loading = ref(false)
const dateRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(7, 'day'), dayjs()])
const router = useRouter()

onMounted(loadDashboard)

async function loadDashboard() {
  loading.value = true
  try {
    const params = rangeParams()
    const [d, u] = await Promise.all([getDashboard(params), getUtilization(params)])
    data.value = d
    utilization.value = u
  } catch {
    message.error('加载仪表盘数据失败')
  } finally {
    loading.value = false
  }
}

function rangeParams() {
  const [start, end] = dateRange.value
  return {
    start_date: start.format('YYYY-MM-DDTHH:mm:ss'),
    end_date: end.format('YYYY-MM-DDTHH:mm:ss'),
  }
}

function disabledFutureDate(current: Dayjs) {
  return current && current.isAfter(dayjs(), 'day')
}

function resetRange() {
  dateRange.value = [dayjs().subtract(7, 'day'), dayjs()]
  loadDashboard()
}

function clampRate(value: number) {
  return Math.max(0, Math.min(100, Number(value) || 0))
}

function rateText(value: number) {
  return `${clampRate(value)}%`
}

function barHeight(value: number) {
  return `${Math.max(4, clampRate(value) * 1.9)}px`
}

function instrumentCode(item: UtilizationStats) {
  return item.instrument_code || `ID-${item.instrument_id}`
}

const utilColumns = [
  { title: '仪器编码', dataIndex: 'instrument_code', key: 'code', width: 130 },
  { title: '仪器名称', dataIndex: 'instrument_name', key: 'name' },
  { title: '预期使用率', dataIndex: 'expected_utilization_rate', key: 'expectedRate' },
  { title: '实际使用率', dataIndex: 'actual_utilization_rate', key: 'actualRate' },
  { title: '计划占用 (h)', dataIndex: 'scheduled_hours', key: 'scheduled' },
  { title: '实际占用 (h)', dataIndex: 'actual_run_hours', key: 'actual' },
]

const stats = computed(() => [
  { title: '仪器总数', value: data.value?.total_instruments || 0, suffix: '活跃 ' + (data.value?.active_instruments || 0), icon: ExperimentOutlined, danger: false, link: '/projects/resource-ledger' },
  { title: '项目总数', value: data.value?.total_projects || 0, suffix: '活跃 ' + (data.value?.active_projects || 0), icon: ProjectOutlined, danger: false, link: '/projects/ledger' },
  { title: '平均利用率', value: (data.value?.avg_utilization || 0) + '%', suffix: '', icon: ThunderboltOutlined, danger: false },
  { title: '延期任务', value: data.value?.delayed_tasks || 0, suffix: '', icon: ClockCircleOutlined, danger: !!(data.value?.delayed_tasks), link: '/tasks/workspace' },
])
</script>

<style scoped>
.utilization-card :deep(.chart-card-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.utilization-legend {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-weight: 400;
}

.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 5px;
}

.legend-dot.expected {
  background: #8fb7e8;
}

.legend-dot.actual {
  background: #165c4a;
}

.utilization-chart {
  min-height: 310px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 16px;
  align-items: end;
}

.utilization-item {
  min-width: 0;
  text-align: center;
}

.bar-area {
  height: 210px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.bar-pair {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 7px;
}

.bar {
  width: 22px;
  min-height: 4px;
  border-radius: 4px 4px 0 0;
}

.bar.expected {
  background: #8fb7e8;
}

.bar.actual {
  background: #165c4a;
}

.rate-line {
  margin-top: 8px;
  color: var(--color-text-primary);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.instrument-name {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.instrument-code {
  display: inline-block;
  max-width: 100%;
  margin-top: 6px;
  padding: 1px 5px;
  color: #334155;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .utilization-card :deep(.chart-card-header) {
    align-items: flex-start;
    flex-direction: column;
  }

  .utilization-chart {
    grid-template-columns: repeat(auto-fit, minmax(82px, 1fr));
  }
}
</style>
