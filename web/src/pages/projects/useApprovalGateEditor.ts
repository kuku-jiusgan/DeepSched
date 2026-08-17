import { ref, type ComputedRef, type Ref } from 'vue'
import { message } from 'ant-design-vue'
import type { Project, Task } from '@/services/api'
import { updateTask } from '@/services/api'

interface ApprovalGateEditorOptions {
  allTasks: Ref<Task[]>
  hasLocalDrafts: ComputedRef<boolean>
  project: Ref<Project | null>
  getAssigneeName: (id: number | null) => string | null
  fetchProject: () => Promise<void>
  errorDetail: (error: unknown, fallback: string) => string
}

export function useApprovalGateEditor(options: ApprovalGateEditorOptions) {
  const approvalGateEditOpen = ref(false)
  const approvalGateEditSubmitting = ref(false)
  const editingApprovalGate = ref<Task | null>(null)

  function openEditApprovalGate(task: Task) {
    if (options.hasLocalDrafts.value && !task.is_local_draft) {
      message.warning('请先保存当前新增草稿，再编辑数据库中的方案签批')
      return
    }
    editingApprovalGate.value = {
      ...task,
      assignee_id: task.assignee_id || options.project.value?.manager_id || null,
    }
    approvalGateEditOpen.value = true
  }

  function closeEditApprovalGate() {
    approvalGateEditOpen.value = false
    editingApprovalGate.value = null
  }

  async function handleApprovalGateEditSubmit(payload: { name: string; assignee_id: number }) {
    const gate = editingApprovalGate.value
    if (!gate) return
    approvalGateEditSubmitting.value = true
    try {
      if (gate.is_local_draft) {
        updateLocalGate(gate.id, payload)
        message.success('方案签批草稿已更新')
      } else {
        await updateTask(gate.id, payload)
        message.success('方案签批已更新')
        await options.fetchProject()
      }
      closeEditApprovalGate()
    } catch (error: unknown) {
      message.error(options.errorDetail(error, '更新方案签批失败'))
    } finally {
      approvalGateEditSubmitting.value = false
    }
  }

  function updateLocalGate(gateId: number, payload: { name: string; assignee_id: number }) {
    const index = options.allTasks.value.findIndex(task => task.id === gateId)
    if (index < 0) return
    options.allTasks.value[index] = {
      ...options.allTasks.value[index],
      name: payload.name,
      assignee_id: payload.assignee_id,
      assignee_name: options.getAssigneeName(payload.assignee_id),
    }
  }

  return {
    approvalGateEditOpen,
    approvalGateEditSubmitting,
    editingApprovalGate,
    openEditApprovalGate,
    closeEditApprovalGate,
    handleApprovalGateEditSubmit,
  }
}
