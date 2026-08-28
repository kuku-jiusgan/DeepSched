<template>
  <div class="today-card-group approval-group">
    <div class="today-card-group-title">
      <span class="today-card-dot" />
      <span>方案签批确认</span>
    </div>
    <div v-if="displayGates.length" class="today-card-stack">
      <article v-for="gate in displayGates" :key="gate.id" class="today-card">
        <div class="today-card-meta">
          <a-tag :color="gateMeta(gate.gate_status).color">{{ gateMeta(gate.gate_status).label }}</a-tag>
          <a-tag v-if="gate.risk_status !== 'normal'" :color="riskMeta(gate.risk_status).color">{{ riskMeta(gate.risk_status).label }}</a-tag>
        </div>
        <div class="today-card-lines">
          <div><span>项目：</span>{{ gate.project_code }} · {{ gate.project_name }}</div>
          <div><span>方案：</span>{{ gateDisplayName(gate) }}</div>
          <div><span>负责人：</span>{{ gate.assignee_name || '未指定' }}</div>
          <div><span>提交时间：</span>{{ formatDateTime(gate.submitted_at) }}</div>
          <div><span>预计签批：</span>{{ formatDateTime(gate.expected_approval_at) }}</div>
          <div><span>最迟签批：</span>{{ formatDateTime(gate.latest_approval_at) }}</div>
          <div><span>前置任务：</span>{{ taskNames(gate.predecessor_tasks) }}</div>
          <div><span>解锁任务：</span>{{ taskNames(gate.unlock_tasks) }}</div>
        </div>
        <div v-if="!predecessorsCompleted(gate)" class="approval-prerequisite-warning">
          前置任务【{{ incompletePredecessorNames(gate) }}】尚未完成。可以先填写预计签批时间，确认签批仍需等待前置任务完成。
        </div>
        <div class="today-card-choice">请选择：</div>
        <div class="today-card-actions">
          <a-button v-if="gate.gate_status !== 'approved'" v-operation="'submit'" size="small" class="workspace-action-button workspace-action-button-secondary" @click="openExpectedApproval(gate)">预计签批时间</a-button>
          <a-tooltip v-if="gate.gate_status !== 'approved'" :title="prerequisiteBlockReason(gate)">
            <span v-operation="'approve'" class="disabled-action-wrapper">
              <a-button size="small" class="workspace-action-button workspace-action-button-success" :disabled="!predecessorsCompleted(gate)" @click="confirmApprove(gate)">确认签批</a-button>
            </span>
          </a-tooltip>
          <a-button v-if="gate.schedule_status === 'confirmation_required'" v-operation="'confirm_impact'" size="small" class="workspace-action-button workspace-action-button-warning" @click="confirmImpact(gate)">确认排程影响</a-button>
        </div>
      </article>
    </div>
    <div v-else class="today-card-empty">暂无方案签批确认事项</div>

    <a-modal
      v-model:open="expectedModalOpen"
      title="填写预计签批时间"
      ok-text="保存并生成预测排程"
      cancel-text="取消"
      :confirm-loading="expectedSubmitting"
      :ok-button-props="{ disabled: !expectedApprovalAt || !expectedGate }"
      :cancel-button-props="{ disabled: expectedSubmitting }"
      :mask-closable="!expectedSubmitting"
      :closable="!expectedSubmitting"
      :keyboard="!expectedSubmitting"
      @ok="saveExpectedApproval"
    >
      <a-form layout="vertical">
        <a-form-item label="预计签批完成时间" required>
          <a-date-picker
            v-model:value="expectedApprovalAt"
            :show-time="{ format: 'HH:mm', minuteStep: 30 }"
            format="YYYY-MM-DD HH:mm"
            :disabled-date="disabledExpectedDate"
            :disabled-time="disabledExpectedTime"
            style="width: 100%"
            @change="normalizeExpectedApprovalAt"
          />
        </a-form-item>
      </a-form>
      <p class="expected-approval-help">预计时间可以在方案撰写完成前填写，但必须晚于当前时间。后续任务仍会同时遵守方案签批和前置任务约束。</p>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { approveApprovalGate, confirmApprovalScheduleImpact, submitApprovalGate } from '@/services/api'
import type { ApprovalGate, ApprovalGateStatus, ApprovalGateTaskRef, ApprovalRiskStatus } from '@/types'
import { isTaskCompleted } from '@/utils/statusMeta'

interface Props { approvalGates: ApprovalGate[] }

const props = defineProps<Props>()
const emit = defineEmits<{ refreshed: [] }>()
const displayGates = computed(() => props.approvalGates)
const expectedModalOpen = ref(false)
const expectedSubmitting = ref(false)
const expectedGate = ref<ApprovalGate | null>(null)
const expectedApprovalAt = ref<Dayjs | null>(null)
function gateDisplayName(gate: ApprovalGate) {
  if (!gate.top_level_task_name || gate.name.startsWith(`${gate.top_level_task_name} ·`)) {
    return gate.name
  }
  return `${gate.top_level_task_name} · ${gate.name}`
}

function openExpectedApproval(gate: ApprovalGate) {
  expectedGate.value = gate
  const latestApprovalAt = gate.latest_approval_at ? dayjs(gate.latest_approval_at) : null
  const defaultExpectedAt = gate.expected_approval_at
    ? dayjs(gate.expected_approval_at)
    : latestApprovalAt?.isAfter(dayjs())
      ? latestApprovalAt
      : dayjs().add(1, 'day').minute(0).second(0)
  const minimum = minimumExpectedAt(gate)
  expectedApprovalAt.value = defaultExpectedAt.isBefore(minimum) ? minimum : ceilToHalfHour(defaultExpectedAt)
  expectedModalOpen.value = true
}
function ceilToHalfHour(value: Dayjs) {
  const normalized = value.second(0).millisecond(0)
  if (normalized.minute() === 0 || normalized.minute() === 30) return normalized
  if (normalized.minute() < 30) return normalized.minute(30)
  return normalized.add(1, 'hour').minute(0)
}

function normalizeExpectedApprovalAt(value: Dayjs | null) {
  expectedApprovalAt.value = value ? ceilToHalfHour(value) : null
}

function disabledExpectedDate(value: Dayjs) {
  const gate = expectedGate.value
  return gate ? value.endOf('day').isBefore(minimumExpectedAt(gate)) : value.endOf('day').isBefore(dayjs())
}

function disabledExpectedTime(value: Dayjs | null) {
  const gate = expectedGate.value
  if (!gate || !value) return {}
  const minimum = minimumExpectedAt(gate)
  if (!value.isSame(minimum, 'day')) return {}
  return {
    disabledHours: () => Array.from({ length: minimum.hour() }, (_, hour) => hour),
    disabledMinutes: (hour: number) => hour === minimum.hour()
      ? Array.from({ length: minimum.minute() }, (_, minute) => minute)
      : [],
  }
}

async function saveExpectedApproval() {
  const gate = expectedGate.value
  const expectedAt = expectedApprovalAt.value ? ceilToHalfHour(expectedApprovalAt.value) : null
  if (!gate || !expectedAt) return
  const minimum = minimumExpectedAt(gate)
  if (expectedAt.isBefore(minimum)) {
    message.warning(`预计签批时间不能早于 ${minimum.format('YYYY-MM-DD HH:mm')}`)
    return
  }
  expectedSubmitting.value = true
  try {
    const result = await submitApprovalGate(gate.id, {
      expected_approval_at: expectedAt.format('YYYY-MM-DDTHH:mm:ss'),
      approval_note: gate.approval_note,
    })
    expectedModalOpen.value = false
    if (result.schedule_status === 'pending_approval') {
      message.success(result.schedule_message || '预计签批时间已保存，签批通过后生成后续排程')
    } else {
      message.warning(result.schedule_message || '预计签批时间已保存，请确认排程影响')
    }
    emit('refreshed')
  } catch (error: unknown) {
    message.error(errorDetail(error, '保存预计签批时间失败'))
  } finally {
    expectedSubmitting.value = false
  }
}

function confirmApprove(gate: ApprovalGate) {
  Modal.confirm({
    title: '确认方案签批已完成？',
    content: `确认后将以当前时间完成“${gate.name}”，并自动重新安排后续验证任务。`,
    okText: '确认完成并排程',
    async onOk() {
      try {
        const result = await approveApprovalGate(gate.id, { approval_note: gate.approval_note })
        if (result.schedule_status === 'confirmation_required') message.warning(result.schedule_message || '签批已记录，请继续确认跨项目排程影响')
        else message.success(result.schedule_message || '已记录客户审核同意')
        emit('refreshed')
      } catch (error: unknown) { message.error(errorDetail(error, '记录签批同意失败')); throw error }
    },
  })
}

function confirmImpact(gate: ApprovalGate) {
  if (!gate.preview_token) return
  Modal.confirm({
    title: '确认跨项目排程影响？',
    content: gate.schedule_message || '该方案需要移动同优先级或低优先级的未开始项目任务，确认后将应用新的排程。',
    okText: '确认应用',
    async onOk() {
      try {
        const result = await confirmApprovalScheduleImpact(gate.id, gate.preview_token || '')
        message.success(result.schedule_message || '排程影响已确认')
        emit('refreshed')
      } catch (error: unknown) { message.error(errorDetail(error, '确认排程影响失败')); throw error }
    },
  })
}

function taskNames(tasks: ApprovalGateTaskRef[]) { return tasks.map(task => task.name).join('、') || '-' }
function incompletePredecessors(gate: ApprovalGate) {
  return gate.predecessor_tasks.filter(task => !isTaskCompleted(task.status))
}
function incompletePredecessorNames(gate: ApprovalGate) {
  return incompletePredecessors(gate).map(task => task.name).join('、')
}
function predecessorsCompleted(gate: ApprovalGate) {
  return gate.predecessor_tasks.length > 0 && incompletePredecessors(gate).length === 0
}
function prerequisiteBlockReason(gate: ApprovalGate) {
  return predecessorsCompleted(gate) ? '' : `前置任务【${incompletePredecessorNames(gate) || '未配置'}】尚未完成`
}
function minimumExpectedAt(_gate: ApprovalGate) {
  return ceilToHalfHour(dayjs().add(1, 'millisecond'))
}
function formatDateTime(value?: string | null) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-' }
function gateMeta(status: ApprovalGateStatus) {
  return { not_submitted: { label: '待提交', color: 'default' }, waiting_approval: { label: '等待客户', color: 'blue' }, approved: { label: '已签批', color: 'green' } }[status]
}
function riskMeta(status: ApprovalRiskStatus) {
  return { normal: { label: '正常', color: 'default' }, upcoming: { label: '即将超期', color: 'gold' }, overdue: { label: '已超预计日期', color: 'orange' }, deadline_risk: { label: '结题风险', color: 'red' } }[status]
}
function errorDetail(error: unknown, fallback: string) {
  const candidate = error as { response?: { data?: { detail?: string } } }
  return candidate.response?.data?.detail || fallback
}
</script>

<style scoped>
.approval-group { order: 2; min-width: 0; padding: var(--space-sm); background: var(--color-bg); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.today-card-group-title { display: flex; align-items: center; gap: var(--space-xs); margin-bottom: var(--space-sm); color: var(--color-text-primary); font-weight: 600; }
.today-card-dot { width: 8px; height: 8px; border-radius: 999px; background: var(--color-accent); }
.today-card-stack { display: flex; flex-direction: column; gap: var(--space-sm); }
.today-card { padding: var(--space-md); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.today-card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); margin-bottom: var(--space-sm); }
.today-card-lines { display: flex; flex-direction: column; gap: 3px; color: var(--color-text-primary); font-size: 0.84rem; line-height: 1.55; }
.today-card-lines span { color: var(--color-text-secondary); }
.today-card-choice { margin: var(--space-sm) 0 var(--space-xs); color: var(--color-text-secondary); font-size: 0.78rem; }
.today-card-actions { display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.approval-prerequisite-warning { margin-top: var(--space-sm); padding: 6px 8px; color: #92400e; background: #fffbeb; border: 1px solid #fde68a; border-radius: var(--radius-sm); font-size: 0.78rem; line-height: 1.45; }
.disabled-action-wrapper { display: inline-block; }
.today-card-empty { min-height: 120px; display: flex; align-items: center; justify-content: center; color: var(--color-text-tertiary); background: var(--color-surface); border: 1px dashed var(--color-border); border-radius: var(--radius-sm); font-size: 0.82rem; }
.expected-approval-help { margin: 0; color: var(--color-text-secondary); font-size: 0.84rem; line-height: 1.55; }
</style>
