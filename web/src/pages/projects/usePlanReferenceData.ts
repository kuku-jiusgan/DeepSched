import { computed, ref } from 'vue'
import { getInstruments, getTaskTypes, getUserDirectory, type TaskTypeConfig } from '@/services/api'

/**
 * 计划分解页的参考数据：任务类型、人员目录与仪器目录。
 * 只负责拉取下拉选项并提供 id → 展示名的查询，不参与任务编辑流程。
 */
export function usePlanReferenceData() {
  const taskTypeOptions = ref<{ label: string; value: string; resource_type: string }[]>([])
  const taskTypeMap = ref<Record<string, TaskTypeConfig>>({})
  const userOptions = ref<{ label: string; value: number }[]>([])
  const instrumentOptions = ref<{ label: string; value: number }[]>([])

  const instrumentCodeMap = computed(() => {
    const map: Record<number, string> = {}
    instrumentOptions.value.forEach(instrument => { map[instrument.value] = instrument.label })
    return map
  })

  function getTaskTypeName(code: string) {
    if (code === 'approval_gate') return '方案签批'
    return taskTypeMap.value[code]?.name || code
  }

  function getAssigneeName(id: number | null | undefined) {
    if (!id) return null
    return userOptions.value.find(u => u.value === id)?.label || null
  }

  function getInstrumentCode(id: number) {
    return instrumentCodeMap.value[id] || `ID ${id}`
  }

  async function loadInstruments() {
    try {
      const insts = await getInstruments({ include_unavailable: true })
      instrumentOptions.value = insts.map(instrument => ({
        label: [instrument.code, instrument.name, instrument.model].filter(Boolean).join(' · '),
        value: instrument.id,
      }))
    } catch (e) { console.error("loadInstruments failed:", e) }
  }

  async function loadUsers() {
    try { const users = await getUserDirectory(); userOptions.value = users.filter(u => u.is_active).map(u => ({ label: u.display_name, value: u.id })) }
    catch { console.error('loadUsers failed') }
  }

  async function loadTaskTypes() {
    try {
      const types = await getTaskTypes(); const active = types.filter(t => t.is_active && t.code !== 'approval_gate')
      taskTypeOptions.value = active.map(t => ({ label: t.name, value: t.code, resource_type: t.resource_type }))
      taskTypeMap.value = {}; active.forEach(t => { taskTypeMap.value[t.code] = t })
    } catch { console.error('loadTaskTypes failed') }
  }

  return {
    taskTypeOptions,
    taskTypeMap,
    userOptions,
    instrumentOptions,
    getTaskTypeName,
    getAssigneeName,
    getInstrumentCode,
    loadInstruments,
    loadUsers,
    loadTaskTypes,
  }
}
