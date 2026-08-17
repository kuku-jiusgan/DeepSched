<template>
  <a-modal
    :open="open"
    title="编辑方案签批"
    okText="保存"
    :confirmLoading="submitting"
    @ok="submit"
    @cancel="$emit('cancel')"
  >
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
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import type { Task } from '@/types'

interface Props {
  open: boolean
  gate: Task | null
  userOptions: { label: string; value: number }[]
  defaultAssigneeId?: number | null
  submitting?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  submit: [payload: { name: string; assignee_id: number }]
  cancel: []
}>()
const form = reactive({ name: '', assigneeId: undefined as number | undefined })

watch(() => [props.open, props.gate] as const, ([open, gate]) => {
  if (!open || !gate) return
  form.name = gate.name
  form.assigneeId = gate.assignee_id || props.defaultAssigneeId || undefined
})

function submit() {
  if (!form.name.trim()) { message.error('请输入限制名称'); return }
  if (!form.assigneeId) { message.error('请选择负责人'); return }
  emit('submit', { name: form.name.trim(), assignee_id: form.assigneeId })
}
</script>
