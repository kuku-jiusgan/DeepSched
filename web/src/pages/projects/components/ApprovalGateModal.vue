<template>
  <a-modal
    :open="open"
    title="添加方案签批"
    okText="添加限制"
    :confirmLoading="submitting"
    @ok="submit"
    @cancel="$emit('cancel')"
  >
    <a-alert type="info" showIcon message="签批等待不占用人员或仪器；未提交预计日期前，后续验证任务不会进入排程。" style="margin-bottom: 16px" />
    <a-form layout="vertical">
      <a-form-item label="限制名称" required>
        <a-input v-model:value="form.name" :maxlength="100" />
      </a-form-item>
      <a-form-item label="负责人" required>
        <a-select
          v-model:value="form.assigneeId"
          :options="userOptions"
          placeholder="选择方案签批负责人"
          showSearch
          optionFilterProp="label"
        />
      </a-form-item>
      <a-form-item label="前置方案任务" required>
        <a-select v-model:value="form.predecessorTaskId" :options="taskOptions" placeholder="选择方案编写任务" showSearch optionFilterProp="label" />
      </a-form-item>
      <a-form-item label="签批后解锁的任务" required>
        <a-select v-model:value="form.unlockTaskIds" mode="multiple" :options="unlockOptions" placeholder="选择方法验证等后续任务" showSearch optionFilterProp="label" />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import type { Task } from '@/types'
import type { ApprovalGateCreatePayload } from '@/services/api'

interface Props {
  open: boolean
  tasks: Task[]
  userOptions: { label: string; value: number }[]
  defaultAssigneeId?: number | null
  submitting?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  submit: [payload: ApprovalGateCreatePayload]
  cancel: []
}>()
const form = reactive({ name: '方案签批', assigneeId: undefined as number | undefined, predecessorTaskId: undefined as number | undefined, unlockTaskIds: [] as number[] })
const leafTasks = computed(() => props.tasks.filter(task => !task.is_external_gate && !props.tasks.some(candidate => candidate.parent_id === task.id)))
const taskOptions = computed(() => leafTasks.value.map(task => ({ label: task.name, value: task.id })))
const unlockOptions = computed(() => leafTasks.value.filter(task => task.id !== form.predecessorTaskId).map(task => ({ label: task.name, value: task.id })))

watch(() => props.open, value => {
  if (!value) return
  form.name = '方案签批'
  form.assigneeId = props.defaultAssigneeId || undefined
  form.predecessorTaskId = undefined
  form.unlockTaskIds = []
})

function submit() {
  if (!form.name.trim()) { message.error('请输入限制名称'); return }
  if (!form.assigneeId) { message.error('请选择负责人'); return }
  if (!form.predecessorTaskId) { message.error('请选择前置方案任务'); return }
  if (!form.unlockTaskIds.length) { message.error('请选择签批后解锁的任务'); return }
  emit('submit', {
    name: form.name.trim(),
    assignee_id: form.assigneeId,
    predecessor_task_id: form.predecessorTaskId,
    unlock_task_ids: form.unlockTaskIds,
  })
}
</script>
