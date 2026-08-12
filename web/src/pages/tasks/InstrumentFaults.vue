<template>
  <div class="fault-page">
    <div class="page-header">
      <h2>故障提报</h2>
    </div>

    <div class="action-bar">
      <a-button v-operation="'create'" type="primary" danger @click="openFaultModal"><ToolOutlined /> 故障提报</a-button>
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />

    <template v-else>
      <a-tabs v-model:activeKey="activeTab" size="small">
        <a-tab-pane key="open" tab="当前故障">
          <a-empty v-if="!openFaults.length" description="暂无故障仪器" />

          <div v-else class="fault-card-grid">
            <div v-for="fault in openFaults" :key="fault.id" class="fault-card">
              <div class="fault-card-header">
                <div class="fault-instrument">
                  <div class="fault-instrument-code">{{ instrumentCode(fault.instrument_id) }}</div>
                  <div class="fault-instrument-name">{{ instrumentName(fault.instrument_id) }}</div>
                </div>
                <a-tag color="red" class="fault-status-tag">故障停机</a-tag>
              </div>

              <div class="fault-summary-grid">
                <div class="fault-summary-item">
                  <span class="fault-summary-label">提报时间</span>
                  <strong>{{ formatDateTime(fault.reported_at) }}</strong>
                </div>
                <div class="fault-summary-item highlight">
                  <span class="fault-summary-label">预计完成</span>
                  <strong>{{ fault.estimated_resolved_at ? formatDateTime(fault.estimated_resolved_at) : '-' }}</strong>
                </div>
                <div class="fault-summary-item">
                  <span class="fault-summary-label">影响任务</span>
                  <strong>{{ fault.affected_tasks?.length || 0 }} 个</strong>
                </div>
                <div class="fault-summary-item" :class="{ highlight: fault.schedule_impact?.risk_tasks }">
                  <span class="fault-summary-label">超期预警</span>
                  <strong>{{ fault.schedule_impact?.risk_tasks || 0 }} 个</strong>
                </div>
              </div>

              <div class="fault-card-desc">
                <span class="fault-desc-label">故障原因</span>
                <span class="fault-desc-text">{{ fault.description }}</span>
              </div>

              <div v-if="fault.affected_tasks?.length" class="affected-task-list">
                <div v-for="task in fault.affected_tasks" :key="task.task_id" class="affected-task-item">
                  <div class="affected-task-main">
                    <span class="affected-task-project">{{ task.project_code || task.project_name || '-' }}</span>
                    <span class="affected-task-name">{{ task.task_name }}</span>
                    <a-tag :color="task.can_shift ? 'blue' : 'red'">{{ task.can_shift ? '可后移' : '超期' }}</a-tag>
                  </div>
                  <div class="affected-task-meta">
                    <span>{{ formatDateTime(task.original_start) }} → {{ formatDateTime(task.shifted_start) }}</span>
                    <span>负责人 {{ task.assignee_name || '-' }}</span>
                  </div>
                  <div v-if="task.reason" class="affected-task-reason">{{ task.reason }}</div>
                </div>
              </div>
              <a-button
                v-operation="'resolve'"
                type="primary"
                size="small"
                class="fault-resolve-button"
                :loading="resolvingFaultId === fault.id"
                @click="handleResolveFault(fault)"
              >
                维修完成
              </a-button>
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="history" tab="维修记录">
          <a-empty v-if="!faultHistory.length" description="暂无维修记录" />

          <a-table
            v-else
            :dataSource="faultHistory"
            :columns="historyColumns"
            rowKey="id"
            size="middle"
            :pagination="{ pageSize: 12, showSizeChanger: true }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'instrument'">
                {{ instrumentLabel(record.instrument_id) }}
              </template>
              <template v-else-if="column.key === 'status'">
                <a-tag :color="record.status === 'resolved' ? 'green' : 'red'">
                  {{ record.status === 'resolved' ? '已维修' : '故障中' }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 'reported_at'">
                {{ formatDateTime(record.reported_at) }}
              </template>
              <template v-else-if="column.key === 'estimated_resolved_at'">
                {{ record.estimated_resolved_at ? formatDateTime(record.estimated_resolved_at) : '-' }}
              </template>
              <template v-else-if="column.key === 'resolved_at'">
                {{ record.resolved_at ? formatDateTime(record.resolved_at) : '-' }}
              </template>
            </template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </template>

    <a-modal
      v-model:open="faultModalOpen"
      title="故障提报"
      ok-text="提交"
      cancel-text="取消"
      :confirm-loading="faultSubmitting"
      @ok="handleSubmitFault"
    >
      <a-form layout="vertical" class="fault-form">
        <a-form-item label="故障仪器" required>
          <a-select
            v-model:value="faultForm.instrumentId"
            placeholder="选择仪器"
            show-search
            :filter-option="filterInstrumentOption"
          >
            <a-select-option v-for="instrument in instruments" :key="instrument.id" :value="instrument.id">
              {{ instrument.code }} · {{ instrument.name }}
            </a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="故障情况" required>
          <a-textarea
            v-model:value="faultForm.description"
            :rows="3"
            placeholder="填写故障现象或影响范围"
          />
        </a-form-item>
        <a-form-item label="预计维修完成时间" required>
          <a-date-picker
            v-model:value="faultForm.estimatedResolvedAt"
            show-time
            :minute-step="30"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择预计完成时间"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="实际维修完成时间">
          <a-date-picker
            v-model:value="faultForm.resolvedAt"
            show-time
            :minute-step="30"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择实际完成时间"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ToolOutlined } from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import {
  getInstruments,
  getInstrumentFaults,
  getOpenInstrumentFaults,
  reportInstrumentFault,
  resolveInstrumentFault,
  type Instrument,
  type InstrumentFault,
} from '@/services/api'

const instruments = ref<Instrument[]>([])
const openFaults = ref<InstrumentFault[]>([])
const faultHistory = ref<InstrumentFault[]>([])
const loading = ref(true)
const activeTab = ref('open')
const faultModalOpen = ref(false)
const faultSubmitting = ref(false)
const resolvingFaultId = ref<number | null>(null)
const faultForm = ref({
  instrumentId: null as number | null,
  description: '',
  estimatedResolvedAt: '',
  resolvedAt: '',
})

const instrumentMap = computed(() => {
  const map: Record<number, Instrument> = {}
  instruments.value.forEach(instrument => { map[instrument.id] = instrument })
  return map
})

const historyColumns = [
  { title: '仪器', key: 'instrument', width: 220 },
  { title: '状态', key: 'status', width: 90 },
  { title: '故障情况', dataIndex: 'description', key: 'description', ellipsis: true },
  { title: '提报时间', key: 'reported_at', width: 160 },
  { title: '预计完成', key: 'estimated_resolved_at', width: 160 },
  { title: '实际完成', key: 'resolved_at', width: 160 },
]

async function fetchData() {
  loading.value = true
  try {
    const [instrumentData, openFaultData, faultHistoryData] = await Promise.all([
      getInstruments({ include_unavailable: true }),
      getOpenInstrumentFaults(),
      getInstrumentFaults(),
    ])
    instruments.value = instrumentData
    openFaults.value = openFaultData
    faultHistory.value = faultHistoryData
  } catch {
    message.error('加载故障信息失败')
  } finally {
    loading.value = false
  }
}

function openFaultModal() {
  faultForm.value = {
    instrumentId: null,
    description: '',
    estimatedResolvedAt: '',
    resolvedAt: '',
  }
  faultModalOpen.value = true
}

function toApiDateTime(value: string) {
  return value ? dayjs(value).format('YYYY-MM-DDTHH:mm:ss') : null
}

function formatDateTime(value: string) {
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

function instrumentLabel(instrumentId: number | null) {
  if (!instrumentId) return '未知仪器'
  const instrument = instrumentMap.value[instrumentId]
  return instrument ? `${instrument.code} · ${instrument.name}` : `仪器 ID ${instrumentId}（未找到基础信息）`
}

function instrumentCode(instrumentId: number | null) {
  if (!instrumentId) return '-'
  return instrumentMap.value[instrumentId]?.code || `ID ${instrumentId}`
}

function instrumentName(instrumentId: number | null) {
  if (!instrumentId) return '未知仪器'
  return instrumentMap.value[instrumentId]?.name || '未找到仪器基础信息'
}

function filterInstrumentOption(input: string, option?: { value: number; children?: string }) {
  const label = option?.children || ''
  return label.toLowerCase().includes(input.toLowerCase())
}

async function handleSubmitFault() {
  const { instrumentId, description, estimatedResolvedAt, resolvedAt } = faultForm.value
  if (!instrumentId) {
    message.warning('请选择故障仪器')
    return
  }
  if (!description.trim()) {
    message.warning('请填写故障情况')
    return
  }
  if (!estimatedResolvedAt) {
    message.warning('请选择预计维修完成时间')
    return
  }
  if (dayjs(estimatedResolvedAt).isBefore(dayjs()) || dayjs(estimatedResolvedAt).isSame(dayjs())) {
    message.warning('预计维修完成时间必须晚于当前时间')
    return
  }
  faultSubmitting.value = true
  try {
    const result = await reportInstrumentFault(instrumentId, {
      description: description.trim(),
      estimated_resolved_at: toApiDateTime(estimatedResolvedAt) || '',
      resolved_at: toApiDateTime(resolvedAt),
    })
    const impact = result.schedule_impact
    const impactText = impact && impact.affected_tasks > 0
      ? `，已后移 ${impact.affected_tasks} 个任务、超期预警 ${impact.risk_tasks || 0} 个、通知 ${impact.notified_users} 人`
      : ''
    message.success((resolvedAt ? '故障记录已归档' : '故障已提报') + impactText)
    faultModalOpen.value = false
    fetchData()
  } catch (error: unknown) {
    message.error(errorDetail(error) || '故障提报失败')
    faultModalOpen.value = false
    activeTab.value = 'open'
    fetchData()
  } finally {
    faultSubmitting.value = false
  }
}

async function handleResolveFault(fault: InstrumentFault) {
  if (!fault.instrument_id) {
    message.error('故障记录缺少仪器信息')
    return
  }
  resolvingFaultId.value = fault.id
  try {
    await resolveInstrumentFault(fault.instrument_id, fault.id)
    message.success('维修已完成')
    activeTab.value = 'history'
    fetchData()
  } catch {
    message.error('维修完成失败')
  } finally {
    resolvingFaultId.value = null
  }
}

function errorDetail(error: unknown) {
  if (
    typeof error === 'object' &&
    error !== null &&
    'response' in error &&
    typeof (error as { response?: unknown }).response === 'object' &&
    (error as { response?: unknown }).response !== null
  ) {
    const response = (error as { response: { data?: { detail?: string } } }).response
    return response.data?.detail || ''
  }
  return ''
}

onMounted(fetchData)
</script>

<style scoped>
.fault-page {
  min-height: calc(100vh - 34px);
  margin: 0;
  padding: 0;
  background: #f7f8fa;
  box-sizing: border-box;
}

.fault-page > .page-header {
  margin-bottom: 14px;
}

.fault-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
}

.fault-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: linear-gradient(180deg, #fffafa 0%, #ffffff 36%);
}

.fault-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.fault-instrument {
  min-width: 0;
}

.fault-instrument-code {
  display: inline-flex;
  max-width: 100%;
  padding: 2px 6px;
  color: #991b1b;
  background: #fee2e2;
  border-radius: 5px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4;
}

.fault-instrument-name {
  margin-top: 6px;
  color: #0f172a;
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fault-status-tag {
  flex-shrink: 0;
  margin-right: 0;
}

.fault-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px;
}

.fault-summary-item {
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #ffffff;
}

.fault-summary-item.highlight {
  border-color: #fed7aa;
  background: #fff7ed;
}

.fault-summary-label {
  display: block;
  margin-bottom: 3px;
  color: #64748b;
  font-size: 11px;
}

.fault-summary-item strong {
  display: block;
  color: #0f172a;
  font-size: 13px;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.fault-card-desc {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: 6px;
  background: #f8fafc;
  color: #475569;
  line-height: 1.5;
  font-size: 13px;
}

.fault-desc-label {
  flex-shrink: 0;
  color: #991b1b;
  font-weight: 600;
}

.fault-desc-text {
  min-width: 0;
}

.affected-task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 0;
}

.affected-task-item {
  padding: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
}

.affected-task-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.affected-task-project {
  color: #2563eb;
  font-family: monospace;
  font-size: 12px;
  font-weight: 600;
}

.affected-task-name {
  flex: 1;
  min-width: 0;
  color: #0f172a;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.affected-task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 6px;
  color: #64748b;
  font-size: 12px;
}

.affected-task-reason {
  margin-top: 6px;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.4;
}

.fault-resolve-button {
  align-self: flex-end;
}

@media (max-width: 768px) {
  .fault-card-grid {
    grid-template-columns: 1fr;
  }

  .fault-summary-grid {
    grid-template-columns: 1fr;
  }
}

.fault-form :deep(.ant-form-item:last-child) {
  margin-bottom: 0;
}
</style>
