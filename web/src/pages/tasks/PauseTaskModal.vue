<template>
  <a-modal
    :open="open"
    title="暂停当前任务"
    :footer="null"
    :closable="!isSubmitting"
    :mask-closable="false"
    @cancel="close"
  >
    <a-descriptions v-if="task" :column="1" size="small" class="pause-task-summary">
      <a-descriptions-item label="当前任务">
        {{ task.project_code }} · {{ task.task_name }}
      </a-descriptions-item>
      <a-descriptions-item label="当前仪器">{{ instrumentText }}</a-descriptions-item>
    </a-descriptions>
    <a-form layout="vertical">
      <a-form-item label="暂停原因" required>
        <a-textarea
          v-model:value="reason"
          :rows="3"
          :maxlength="500"
          show-count
          placeholder="填写等待样品、等待反应或临时插单等原因"
        />
      </a-form-item>
      <a-form-item label="接替任务" required>
        <a-select
          v-model:value="targetSlotId"
          allow-clear
          :loading="isLoadingCandidates"
          placeholder="请选择接替任务"
          option-label-prop="label"
        >
          <a-select-option
            v-for="candidate in candidates"
            :key="candidate.slot_id"
            :value="candidate.slot_id"
            :label="`${candidate.project_code} · ${candidate.task_name}`"
          >
            <div class="pause-candidate-title">
              {{ candidate.project_code }} · {{ candidate.task_name }}
              <a-tag v-if="candidate.is_paused" color="orange">恢复</a-tag>
            </div>
            <div class="pause-candidate-meta">
              {{ candidate.project_name }} · {{ formatWindow(candidate) }}
            </div>
          </a-select-option>
        </a-select>
        <a-empty
          v-if="!isLoadingCandidates && !candidates.length"
          :image="simpleImage"
          description="当前没有满足条件的接替任务"
          class="pause-candidate-empty"
        />
      </a-form-item>
    </a-form>
    <div class="pause-task-actions">
      <a-button :disabled="isSubmitting" @click="close">取消</a-button>
      <a-button type="primary" :disabled="!targetSlotId" :loading="isSubmitting" @click="submit">
        暂停并切换
      </a-button>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import type { ProjectPlanApplyResult } from '@/types'
import { scheduleFailureContent } from '@/pages/projects/planScheduleFailure'
import dayjs from 'dayjs'
import { Empty, message, Modal } from 'ant-design-vue'
import { getTaskSwitchCandidates, pauseTask } from '@/services/api'
import type { TaskSwitchCandidate } from '@/services/api'
import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { actionableSlotId } from '@/domains/tasks/workspaceTask'

interface Props {
  open: boolean
  task: WorkspaceTask | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  completed: []
}>()

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE
const reason = ref('')
const targetSlotId = ref<number | undefined>()
const candidates = ref<TaskSwitchCandidate[]>([])
const isLoadingCandidates = ref(false)
const isSubmitting = ref(false)

const instrumentText = computed(() => {
  const slot = props.task?.actionable_slot
  return [slot?.instrument_code, slot?.instrument_name].filter(Boolean).join(' · ') || '-'
})

watch(() => props.open, isOpen => {
  if (!isOpen) return
  reason.value = ''
  targetSlotId.value = undefined
  void loadCandidates()
})

async function loadCandidates() {
  const slotId = props.task ? actionableSlotId(props.task) : null
  candidates.value = []
  if (!slotId) return
  isLoadingCandidates.value = true
  try {
    candidates.value = await getTaskSwitchCandidates(slotId)
  } catch (error: unknown) {
    const candidate = error as { response?: { data?: { detail?: string } } }
    message.error(candidate.response?.data?.detail || '接替任务加载失败')
  } finally {
    isLoadingCandidates.value = false
  }
}

async function submit() {
  const slotId = props.task ? actionableSlotId(props.task) : null
  const cleanReason = reason.value.trim()
  if (!slotId || isSubmitting.value) return
  if (!cleanReason) return void message.warning('请填写暂停原因')
  if (!targetSlotId.value) return void message.warning('请选择接替任务')
  isSubmitting.value = true
  try {
    const result = await pauseTask(slotId, cleanReason, targetSlotId.value)
    message.success(result.message || '任务已暂停')
    emit('update:open', false)
    emit('completed')
  } catch (error: unknown) {
    const candidate = error as { response?: { data?: { detail?: string } } }
    const detail = candidate.response?.data?.detail || '暂停任务失败'
    const structured = typeof detail === 'object' && detail !== null
      ? detail as ProjectPlanApplyResult
      : null
    Modal.error({
      title: '暂停并切换失败',
      content: structured?.schedule_failure
        ? scheduleFailureContent(structured)
        : h('div', { style: { whiteSpace: 'pre-line' } }, String(detail)),
      okText: '确认',
    })
  } finally {
    isSubmitting.value = false
  }
}

function close() {
  if (!isSubmitting.value) emit('update:open', false)
}

function formatWindow(candidate: TaskSwitchCandidate) {
  return `${dayjs(candidate.plan_start).format('MM-DD HH:mm')}–${dayjs(candidate.plan_end).format('MM-DD HH:mm')}`
}
</script>

<style scoped>
.pause-task-summary {
  margin-bottom: 16px;
}

.pause-candidate-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pause-candidate-meta {
  color: #64748b;
  font-size: 12px;
}

.pause-candidate-empty {
  margin: 12px 0 0;
}

.pause-task-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

</style>
