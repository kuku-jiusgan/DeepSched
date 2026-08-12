<template>
  <div>
    <div class="page-header">
      <h2>个人工作台</h2>
    </div>

    <div class="action-bar">
      <a-tabs v-model:activeKey="activeTab" size="small" style="flex: 1">
        <a-tab-pane key="active" tab="进行中" />
        <a-tab-pane key="pending" tab="待开始" />
        <a-tab-pane key="completed" tab="已完成" />
        <a-tab-pane key="approved" tab="已签批" />
      </a-tabs>
      <div v-if="activeTab === 'pending' || activeTab === 'completed' || activeTab === 'approved'" class="workspace-project-search">
        <a-input
          v-model:value="projectKeyword"
          allow-clear
          placeholder="项目编号或项目名称"
          @press-enter="applyProjectSearch"
        />
        <a-button type="primary" @click="applyProjectSearch"><SearchOutlined /> 查询</a-button>
      </div>
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />

    <template v-else>
      <TodayTaskCards
        v-if="activeTab === 'active'"
        :tasks="tasks"
        @start="handleStart"
        @complete="handleComplete"
        @pause="handlePause"
        @refreshed="fetchData"
      >
        <template #additional-group>
          <ApprovalConfirmationGroup :approval-gates="approvalGates" @refreshed="fetchData" />
        </template>
      </TodayTaskCards>

      <a-table v-if="activeTab === 'pending' || activeTab === 'completed'" :dataSource="filtered" :columns="columns" rowKey="task_id" size="middle"
        :pagination="{ pageSize: 20, showSizeChanger: true }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <a-tag :color="statusColor(record.execution_status)">{{ statusLabel(record.execution_status) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'task_type'">
            <a-tag v-if="record.task_type" :color="taskTypeColor(record.task_type)" style="font-size: 11px">{{ taskTypeLabel(record.task_type) }}</a-tag>
            <span v-else style="color: #ccc">-</span>
          </template>
          <template v-else-if="column.key === 'task_name'">
            <span style="font-weight: 500">{{ formatTaskName(record) }}</span>
          </template>
          <template v-else-if="column.key === 'project'">
            <span style="font-family: monospace; font-size: 12px; color: #2563eb">{{ record.project_code }}</span>
            <span style="margin-left: 6px; font-size: 12px; color: #64748b">{{ record.project_name }}</span>
          </template>
          <template v-else-if="column.key === 'instrument'">
            {{ formatInstrument(record) }}
          </template>
          <template v-else-if="column.key === 'plan_start'">
            {{ formatTaskPlanStart(record) }}
          </template>
          <template v-else-if="column.key === 'plan_end'">
            {{ formatTaskPlanEnd(record) }}
          </template>
          <template v-else-if="column.key === 'actual_start'">
            {{ formatTaskActualStart(record) }}
          </template>
          <template v-else-if="column.key === 'actual_end'">
            {{ formatTaskActualEnd(record) }}
          </template>
          <template v-else-if="column.key === 'estimated_hours'">
            {{ formatHours(record.est_duration_hours) }}
          </template>
          <template v-else-if="column.key === 'actual_hours'">
            {{ formatHours(record.actual_duration_hours) }}
          </template>
          <template v-else-if="column.key === 'actions'">
            <template v-if="!record.actionable_slot">
              <a-tag color="default">未排程</a-tag>
            </template>
            <a-space v-else :size="4">
              <a-button
                v-operation="'start'"
                v-if="canStartWorkspaceTask(record)"
                type="primary"
                size="small"
                class="task-action-button task-action-button-start"
                @click="handleStart(record)"
                :loading="actingId === record.actionable_slot?.id"
              >
                <PlayCircleOutlined /> {{ workspaceActionStatus(record) === 'paused' ? '恢复' : '开始' }}
              </a-button>
              <a-button
                v-operation="'complete'"
                v-if="canCompleteWorkspaceTask(record)"
                size="small"
                class="task-action-button task-action-button-complete"
                :loading="actingId === record.actionable_slot?.id"
                @click="handleComplete(record)"
              >
                <CheckCircleOutlined /> 完成
              </a-button>

            </a-space>
          </template>
        </template>
      </a-table>

      <a-table
        v-else-if="activeTab === 'approved'"
        :dataSource="filteredApprovedGates"
        :columns="approvedColumns"
        rowKey="id"
        size="middle"
        :scroll="{ x: 1080 }"
        :pagination="{ pageSize: 20, showSizeChanger: true }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'project'">
            <span class="approved-project-code">{{ record.project_code }}</span>
            <span class="approved-project-name">{{ record.project_name }}</span>
          </template>
          <template v-else-if="column.key === 'approved_at'">
            {{ formatApprovedDateTime(record.approved_at) }}
          </template>
          <template v-else-if="column.key === 'schedule_result'">
            <a-tooltip :title="record.schedule_message || scheduleLabel(record.schedule_status)">
              <span class="approved-cell-ellipsis">{{ record.schedule_message || scheduleLabel(record.schedule_status) }}</span>
            </a-tooltip>
          </template>
          <template v-else-if="column.key === 'approval_note'">
            <a-tooltip :title="record.approval_note || '-'">
              <span class="approved-cell-ellipsis">{{ record.approval_note || '-' }}</span>
            </a-tooltip>
          </template>
        </template>
        <template #emptyText><a-empty description="暂无已签批方案" /></template>
      </a-table>
    </template>

    <a-modal
      v-model:open="releaseConfirmOpen"
      title="是否释放仪器？"
      :closable="!releaseSubmitting"
      :keyboard="!releaseSubmitting"
      :mask-closable="false"
      :footer="null"
      @cancel="closeReleaseConfirm"
    >
      <p>{{ releaseConfirmContent }}</p>
      <div class="release-confirm-actions">
        <a-button :disabled="releaseSubmitting" @click="closeReleaseConfirm">取消</a-button>
        <a-button :loading="releaseSubmitting" @click="confirmTaskCompletion(false)">仅标记完成</a-button>
        <a-button type="primary" :loading="releaseSubmitting" @click="confirmTaskCompletion(true)">
          释放仪器并前移后续任务
        </a-button>
      </div>
    </a-modal>
    <PauseTaskModal
      v-model:open="pauseModalOpen"
      :task="pauseTaskRecord"
      @completed="fetchData"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircleOutlined, PlayCircleOutlined, SearchOutlined } from '@ant-design/icons-vue'
import {
  getApprovalGates, startTask, completeTask, getTaskTypes,
} from '@/services/api'
import { getMyTasks } from '@/services/workspaceApi'
import type { ApprovalGate } from '@/types'
import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import {
  actionableSlotId,
  canCompleteWorkspaceTask,
  canStartWorkspaceTask,
  isWorkspaceActiveTask,
  isWorkspacePendingTask,
  workspaceActionStatus,
} from '@/domains/tasks/workspaceTask'
import { isTaskCompleted, taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'
import TodayTaskCards from './TodayTaskCards.vue'
import ApprovalConfirmationGroup from './ApprovalConfirmationGroup.vue'
import PauseTaskModal from './PauseTaskModal.vue'
import './workspaceActionButtons.css'
import dayjs from 'dayjs'

const tasks = ref<WorkspaceTask[]>([])
const approvalGates = ref<ApprovalGate[]>([])
const approvedGates = ref<ApprovalGate[]>([])
const loading = ref(true)
const activeTab = ref<string>('active')
const projectKeyword = ref('')
const appliedProjectKeyword = ref('')
const actingId = ref<number | null>(null)
const releaseConfirmOpen = ref(false)
const releaseSubmitting = ref(false)
const releaseConfirmTask = ref<WorkspaceTask | null>(null)
const pauseModalOpen = ref(false)
const pauseTaskRecord = ref<WorkspaceTask | null>(null)
let isFetching = false

const EARLY_RELEASE_THRESHOLD_MINUTES = 30


const cardTasks = computed(() => {
  if (activeTab.value === 'active') {
    return tasks.value.filter(task => isWorkspaceActiveTask(task))
  }
  if (activeTab.value === 'pending') {
    return tasks.value.filter(task => isWorkspacePendingTask(task))
  }
  return []
})

const filtered = computed(() => {
  let result: WorkspaceTask[]
  if (activeTab.value === 'completed') {
    result = tasks.value.filter(task => isTaskCompleted(task.execution_status))
  } else {
    result = cardTasks.value
  }
  const keyword = appliedProjectKeyword.value.trim().toLowerCase()
  if (!keyword) return result
  return result.filter(task => `${task.project_code || ''} ${task.project_name || ''}`.toLowerCase().includes(keyword))
})

const filteredApprovedGates = computed(() => {
  const keyword = appliedProjectKeyword.value.trim().toLowerCase()
  if (!keyword) return approvedGates.value
  return approvedGates.value.filter(gate =>
    `${gate.project_code || ''} ${gate.project_name || ''}`.toLowerCase().includes(keyword),
  )
})

function applyProjectSearch() {
  appliedProjectKeyword.value = projectKeyword.value
}


const taskTypeMap = ref<Record<string, string>>({})

async function loadTaskTypes() {
  try {
    const types = await getTaskTypes()
    const map: Record<string, string> = {}
    types.forEach(t => { map[t.code] = t.name })
    taskTypeMap.value = map
  } catch { /* ignore */ }
}

function statusColor(s: string) {
  return taskStatusColor(s)
}

function taskTypeLabel(code: string | null) { return code ? (taskTypeMap.value[code] || code) : '' }
function taskTypeColor(code: string | null) {
  if (!code) return '#94a3b8'
      const m: Record<string, string> = { FFKF_001: '#8b5cf6', QCFA_001: '#f59e0b', FFYZ_001: '#10b981', SJCL_001: '#3b82f6', ZXBG_001: '#ef4444' }
  return m[code] || '#94a3b8'
}
function statusLabel(s: string) {
  return taskStatusLabel(s)
}

function formatDateTime(value: string | null | undefined) {
  return value ? dayjs(value).format('MM-DD HH:mm') : '-'
}

function formatInstrument(record: WorkspaceTask) {
  const slot = record.actionable_slot
  if (slot?.instrument_code && slot.instrument_name) {
    return `${slot.instrument_code} · ${slot.instrument_name}`
  }
  return slot?.instrument_code || slot?.instrument_name || '-'
}

function formatTaskName(record: WorkspaceTask) {
  return record.top_level_task_name && record.top_level_task_name !== record.task_name
    ? `${record.top_level_task_name}·${record.task_name || '未命名任务'}`
    : (record.task_name || '未命名任务')
}

function formatTaskPlanStart(record: WorkspaceTask) {
  return formatDateTime(record.task_window.start)
}

function formatTaskPlanEnd(record: WorkspaceTask) {
  return formatDateTime(record.task_window.end)
}

function formatTaskActualStart(record: WorkspaceTask) {
  return formatDateTime(record.actual_window.start)
}

function formatTaskActualEnd(record: WorkspaceTask) {
  return formatDateTime(record.actual_window.end)
}

function formatHours(value: number | null | undefined) {
  return value === null ? '-' : `${value} 小时`
}

const columns = computed(() => [
  { title: '任务名称', dataIndex: 'task_name', key: 'task_name', width: 200, ellipsis: true },
  { title: '任务类型', key: 'task_type', width: 110 },
  { title: '所属项目', key: 'project', width: 200 },
  { title: '仪器', key: 'instrument', width: 130 },
  { title: '负责人', dataIndex: 'assignee_name', key: 'assignee_name', width: 90 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 80 },
  { title: '计划开始', key: 'plan_start', width: 120 },
  { title: '计划结束', key: 'plan_end', width: 120 },
  ...(activeTab.value === 'completed' ? [
    { title: '实际开始', key: 'actual_start', width: 120 },
    { title: '实际完成', key: 'actual_end', width: 120 },
    { title: '预计工时', key: 'estimated_hours', width: 100 },
    { title: '实际工时', key: 'actual_hours', width: 100 },
  ] : []),
  { title: '操作', key: 'actions', width: 160 },
])

const approvedColumns = [
  { title: '方案', dataIndex: 'name', key: 'name', width: 190, ellipsis: true },
  { title: '所属项目', key: 'project', width: 260 },
  { title: '负责人', dataIndex: 'assignee_name', key: 'assignee_name', width: 90 },
  { title: '实际签批', key: 'approved_at', width: 130 },
  { title: '记录人', dataIndex: 'approved_by_name', key: 'approved_by_name', width: 90 },
  { title: '排程结果', key: 'schedule_result', width: 210 },
  { title: '备注', key: 'approval_note', width: 180 },
]

async function fetchData(isSilent = false) {
  if (isFetching) return
  isFetching = true
  try {
    const [taskResult, approvalResult, approvedResult] = await Promise.all([
      getMyTasks(),
      getApprovalGates({ status: 'pending', page_size: 500, workspace_only: true }),
      getApprovalGates({ status: 'approved', page_size: 500, workspace_only: true }),
    ])
    tasks.value = taskResult
    approvalGates.value = approvalResult.items
    approvedGates.value = approvedResult.items
    loadTaskTypes()
  } catch {
    if (!isSilent) message.error('加载工作台失败')
  } finally {
    isFetching = false
    loading.value = false
  }
}

function refreshWorkspaceWhenActive() {
  if (document.visibilityState === 'visible') void fetchData(true)
}

async function handleStart(record: WorkspaceTask) {
  const slotId = actionableSlotId(record)
  if (!slotId) return
  actingId.value = slotId
  try {
    await startTask(slotId)
    message.success(workspaceActionStatus(record) === 'paused' ? '任务已恢复' : '任务已开始')
    fetchData()
  } catch (error: unknown) { message.error(errorDetail(error, '开始任务失败')) }
  finally { actingId.value = null }
}

function handlePause(record: WorkspaceTask) {
  pauseTaskRecord.value = record
  pauseModalOpen.value = true
}

const releaseConfirmContent = computed(() => {
  const record = releaseConfirmTask.value
  if (!record) return ''
  const earlyMinutes = getEarlyCompletionMinutes(record)
  const timing = `当前完成时间比计划完成时间 ${formatTaskPlanEnd(record)} 提前约 ${earlyMinutes} 分钟。`
  const priority = record.resume_priority
  if (!priority) return `${timing}释放仪器后，同项目同仪器的后续任务可按约束前移。`
  const project = [priority.project_code, priority.project_name].filter(Boolean).join(' · ')
  return `${timing}释放仪器后，项目【${project || '未命名项目'}】任务【${priority.task_name}】将优先恢复并开始。`
})

async function handleComplete(record: WorkspaceTask) {
  const earlyMinutes = getEarlyCompletionMinutes(record)
  if (earlyMinutes > EARLY_RELEASE_THRESHOLD_MINUTES) {
    releaseConfirmTask.value = record
    releaseConfirmOpen.value = true
    return
  }
  await submitComplete(record, true)
}

function closeReleaseConfirm() {
  if (releaseSubmitting.value) return
  releaseConfirmOpen.value = false
  releaseConfirmTask.value = null
}

async function confirmTaskCompletion(releaseInstrument: boolean) {
  const record = releaseConfirmTask.value
  if (!record || releaseSubmitting.value) return
  releaseSubmitting.value = true
  const isCompleted = await submitComplete(record, releaseInstrument)
  releaseSubmitting.value = false
  if (isCompleted) closeReleaseConfirm()
}

function getEarlyCompletionMinutes(record: WorkspaceTask) {
  const plannedEnd = record.task_window.end
  if (!plannedEnd) return 0
  return dayjs(plannedEnd).diff(dayjs(), 'minute')
}

async function submitComplete(record: WorkspaceTask, releaseInstrument: boolean) {
  const slotId = actionableSlotId(record)
  if (!slotId) return false
  actingId.value = slotId
  try {
    const result = await completeTask(slotId, { release_instrument: releaseInstrument })
    message.success(completionMessage(result, releaseInstrument))
    await fetchData()
    return true
  } catch {
    message.error('操作失败')
    return false
  } finally {
    actingId.value = null
  }
}

function completionMessage(result: { message?: string; moved_tasks?: number }, releaseInstrument: boolean) {
  if (result.message) return result.message
  if (!releaseInstrument) return '任务已完成，后续排程保持不变'
  if (result.moved_tasks && result.moved_tasks > 0) return `任务已完成，已前移 ${result.moved_tasks} 个后续任务`
  return '任务已完成'
}

function errorDetail(error: unknown, fallback: string) {
  const candidate = error as { response?: { data?: { detail?: string } } }
  return candidate.response?.data?.detail || fallback
}

function formatApprovedDateTime(value?: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'
}

function scheduleLabel(status?: string | null) {
  if (!status) return '-'
  return {
    forecast: '预测排程已更新',
    applied: '正式排程已更新',
    confirmation_required: '待确认排程影响',
    deadline_risk: '结题日期不可满足',
  }[status] || status
}

onMounted(() => {
  void fetchData()
  window.addEventListener('focus', refreshWorkspaceWhenActive)
  document.addEventListener('visibilitychange', refreshWorkspaceWhenActive)
})

onBeforeUnmount(() => {
  window.removeEventListener('focus', refreshWorkspaceWhenActive)
  document.removeEventListener('visibilitychange', refreshWorkspaceWhenActive)
})
</script>

<style scoped>
.task-action-button {
  min-width: 72px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.task-action-button-start {
  background: var(--color-accent) !important;
  border-color: var(--color-accent) !important;
}

.task-action-button-complete {
  color: #ffffff !important;
  background: var(--color-success) !important;
  border-color: var(--color-success) !important;
}

.task-action-button-complete:hover,
.task-action-button-complete:focus {
  color: #ffffff !important;
  background: #15803d !important;
  border-color: #15803d !important;
}

.release-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
}

.workspace-project-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 12px;
}

.workspace-project-search .ant-input-affix-wrapper { width: 240px; }

.approved-project-code {
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 12px;
}

.approved-project-name {
  margin-left: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.approved-cell-ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 720px) {
  .workspace-project-search { width: 100%; }
  .workspace-project-search .ant-input-affix-wrapper { flex: 1; width: auto; }
}
</style>
