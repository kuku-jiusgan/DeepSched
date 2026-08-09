<template>
  <div>
    <div class="page-header">
      <a-button type="text" @click="goBack"><LeftOutlined /> 返回</a-button>
      <h2 style="margin: 0 0 0 8px">{{ projectName }} - 任务工时明细</h2>
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 50px auto" />

    <template v-else>
      <div class="action-bar">
        <span style="margin-left: auto; font-size: 12px; color: #94a3b8">共 {{ tasks.length }} 个任务，合计 {{ totalHours.toFixed(1) }}h</span>
      </div>

      <a-table :dataSource="tasks" rowKey="taskId" size="small" :pagination="{ pageSize: 50, showSizeChanger: true }">
        <a-table-column title="任务名称" dataIndex="taskName" key="name" />
        <a-table-column title="类型" key="type" width="120">
          <template #default="{ record }">
            <a-tag :color="getTypeColor(record.taskType)" style="font-size: 11px">{{ getTypeName(record.taskType) }}</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="负责人" dataIndex="assigneeName" key="assignee" width="100" />
        <a-table-column title="工时(h)" key="hours" width="100" align="center">
          <template #default="{ record }">{{ record.hours.toFixed(1) }}</template>
        </a-table-column>
        <a-table-column title="资源类型" key="res" width="100" align="center">
          <template #default="{ record }">
            <a-tag v-if="record.isHuman" color="purple" style="font-size: 11px">人工</a-tag>
            <a-tag v-if="record.isInstrument" color="blue" style="font-size: 11px">仪器</a-tag>
          </template>
        </a-table-column>
      </a-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { LeftOutlined } from '@ant-design/icons-vue'
import { getProject, getTaskTypes, getUserDirectory } from '@/services/api'

const router = useRouter()
const route = useRoute()
const projectId = Number(route.query.id)
const projectName = ref(route.query.name as string || '')
const loading = ref(true)
const taskTypeMap = ref<Record<string, string>>({})
const userMap = ref<Map<number, string>>(new Map())

interface TaskDetail {
  taskId: number; taskName: string; taskType: string; assigneeName: string; hours: number; isHuman: boolean; isInstrument: boolean
}
const tasks = ref<TaskDetail[]>([])

const totalHours = computed(() => tasks.value.reduce((s, t) => s + t.hours, 0))

function getTypeName(code: string) { return taskTypeMap.value[code] || code }
function getTypeColor(code: string) {
      const m: Record<string, string> = { FFKF_001: '#8b5cf6', QCFA_001: '#f59e0b', FFYZ_001: '#10b981', SJCL_001: '#3b82f6', ZXBG_001: '#ef4444' }
  return m[code] || '#94a3b8'
}

function goBack() { router.push('/operations/reports') }

async function loadUsers() {
  try {
    const users = await getUserDirectory()
    const m = new Map<number, string>()
    users.forEach(u => { m.set(u.id, u.display_name) })
    userMap.value = m
  } catch { message.warning('人员目录加载失败，将使用任务中的负责人信息') }
}

async function loadTaskTypes() {
  try {
    const types = await getTaskTypes()
    types.forEach(t => { taskTypeMap.value[t.code] = t.name })
  } catch { console.error('loadTaskTypes failed') }
}

async function fetchData() {
  loading.value = true
  try {
    const p = await getProject(projectId)
    if (!projectName.value) projectName.value = p.name
    const details: TaskDetail[] = []
    for (const t of p.tasks || []) {
      const hours = t.est_duration_hours || 0
      if (hours <= 0) continue
      let name = t.assignee_name || '-'
      if (name === '-' && t.assignee_id) {
        const u = userMap.value.get(t.assignee_id)
        if (u) name = u
      }
      details.push({
        taskId: t.id, taskName: t.name, taskType: t.task_type,
        assigneeName: name,
        hours,
        isHuman: t.requires_instrument === false,
        isInstrument: t.requires_instrument === true,
      })
    }
    tasks.value = details
  } catch { message.error('加载数据失败') }
  finally { loading.value = false }
}

onMounted(async () => {
  if (!projectId) { message.error('缺少项目ID'); router.push('/operations/reports'); return }
  await Promise.all([loadTaskTypes(), loadUsers()])
  await fetchData()
})
</script>
