<template>
  <div>
    <div class="page-header">
      <h2>插单与影响预览</h2>
      <p>将已排程任务插入到指定任务之后；插单可调整冻结任务，已开始和已完成任务不会移动。</p>
    </div>

    <a-card class="insert-card" :bordered="false">
      <a-form layout="vertical">
        <a-form-item label="插单项目" required>
          <a-select
            v-model:value="form.projectId"
            :options="projectOptions"
            placeholder="选择需要插单的项目"
            show-search
            option-filter-prop="label"
            @change="handleProjectChange"
          />
        </a-form-item>
        <a-form-item label="插单任务" required>
          <a-checkbox-group v-model:value="form.taskIds" class="task-options">
            <a-checkbox v-for="task in selectableTasks" :key="task.id" :value="task.id">
              <span class="task-name">{{ task.name }}</span>
              <span class="task-meta">{{ task.est_duration_hours || 0 }}h · {{ statusLabel(task.status) }}</span>
            </a-checkbox>
          </a-checkbox-group>
          <a-empty v-if="form.projectId && !selectableTasks.length" description="该项目暂无可插单的叶子任务" />
        </a-form-item>
        <a-form-item label="目标项目" required>
          <a-select
            v-model:value="form.targetProjectId"
            :options="targetProjectOptions"
            placeholder="选择插入位置所在的项目"
            show-search
            option-filter-prop="label"
            @change="handleTargetProjectChange"
          />
        </a-form-item>
        <a-form-item label="插入到该任务之后" required>
          <a-select
            v-model:value="form.anchorTaskId"
            :options="anchorTaskOptions"
            placeholder="选择已排程、运行中或已完成的叶子任务"
            show-search
            option-filter-prop="label"
          />
        </a-form-item>
        <a-space>
          <a-button v-operation="'preview'" type="primary" :loading="previewing" @click="loadPreview">计算影响</a-button>
          <a-button @click="resetPreview">清空预览</a-button>
        </a-space>
      </a-form>
    </a-card>

    <a-card v-if="preview" class="preview-card" title="排程影响预览" :bordered="false">
      <a-alert
        type="info"
        show-icon
        :message="`预计创建 ${preview.timeslots_created} 个时间槽，累计顺延 ${preview.total_delay_hours} 小时`"
      />
      <a-table
        :data-source="preview.impacts"
        :columns="impactColumns"
        row-key="task_id"
        size="small"
        :pagination="false"
        style="margin-top: 16px"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'type'">
            <a-tag :color="impactRoleColor(record.impact_role, record.is_insert_task)">
              {{ impactRoleLabel(record.impact_role, record.is_insert_task) }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'original'">
            {{ formatRange(record.original_start, record.original_end) }}
          </template>
          <template v-else-if="column.key === 'new'">
            {{ formatRange(record.new_start, record.new_end) }}
          </template>
          <template v-else-if="column.key === 'delay'">
            <span :class="impactTimeClass(record.delay_hours)">{{ impactTimeText(record.delay_hours) }}</span>
          </template>
        </template>
      </a-table>
      <div class="preview-actions">
        <a-button @click="router.push('/schedule/engine')">返回排程管理</a-button>
        <a-button v-operation="'confirm'" type="primary" :loading="confirming" @click="handleConfirm">确认插单</a-button>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { isAxiosError } from 'axios'
import dayjs from 'dayjs'
import {
  calculateInsertCost,
  confirmInsert,
  getProjects,
  type InsertOrderRequest,
} from '@/services/api'
import type { InsertCost, Project, Task } from '@/types'

interface InsertForm {
  projectId: number | null
  taskIds: number[]
  targetProjectId: number | null
  anchorTaskId: number | null
}

const router = useRouter()
const projects = ref<Project[]>([])
const preview = ref<InsertCost | null>(null)
const previewing = ref(false)
const confirming = ref(false)
const form = reactive<InsertForm>({
  projectId: null,
  taskIds: [],
  targetProjectId: null,
  anchorTaskId: null,
})

function priorityLabel(priority: number) {
  return ({ 1: '一级（最高）', 2: '二级', 3: '三级' } as Record<number, string>)[priority] || `${priority}级`
}

const projectOptions = computed(() => projects.value.map(project => ({
  label: `${project.code} · ${project.name}（${priorityLabel(project.priority)}）`,
  value: project.id,
})))

const selectedProject = computed(() => projects.value.find(project => project.id === form.projectId) || null)
const targetProject = computed(() => projects.value.find(project => project.id === form.targetProjectId) || null)
const selectableTasks = computed(() => uniqueTasksById(
  flattenLeafTasks(selectedProject.value?.tasks || []).filter(task =>
    !task.is_external_gate && ['scheduled', 'blocked'].includes(task.status),
  ),
))
const targetProjectOptions = computed(() => projectOptions.value)
const anchorTaskOptions = computed(() => uniqueTasksById(
  flattenLeafTasks(targetProject.value?.tasks || []).filter(task =>
    !task.is_external_gate && ['scheduled', 'blocked', 'running', 'done', 'completed'].includes(task.status)
      && !form.taskIds.includes(task.id),
  ),
).map(task => ({
  label: `${task.name} · ${statusLabel(task.status)}`,
  value: task.id,
})))

const impactColumns = [
  { title: '类型', key: 'type', width: 100 },
  { title: '项目', dataIndex: 'project_name', key: 'project' },
  { title: '任务', dataIndex: 'task_name', key: 'task' },
  { title: '原排程', key: 'original', width: 240 },
  { title: '新排程', key: 'new', width: 240 },
  { title: '时间变化', key: 'delay', width: 110 },
]

onMounted(async () => {
  try {
    projects.value = await getProjects()
  } catch {
    message.error('项目加载失败')
  }
})

function flattenLeafTasks(tasks: Task[]): Task[] {
  return tasks.flatMap(task => task.children?.length ? flattenLeafTasks(task.children) : [task])
}

function uniqueTasksById(tasks: Task[]): Task[] {
  return [...new Map(tasks.map(task => [task.id, task])).values()]
}

function handleProjectChange() {
  form.taskIds = []
  preview.value = null
}

function handleTargetProjectChange() {
  form.anchorTaskId = null
  preview.value = null
}

function requestData(): InsertOrderRequest | null {
  if (!form.projectId) {
    message.warning('请选择插单项目')
    return null
  }
  if (!form.taskIds.length) {
    message.warning('请选择插单任务')
    return null
  }
  if (!form.anchorTaskId) {
    message.warning('请选择插入位置任务')
    return null
  }
  return {
    project_id: form.projectId,
    task_ids: form.taskIds,
    mode: 'custom_after_task',
    anchor_task_id: form.anchorTaskId,
  }
}

async function loadPreview() {
  const data = requestData()
  if (!data) return
  previewing.value = true
  try {
    preview.value = await calculateInsertCost(data)
  } catch (error: unknown) {
    message.error(errorDetail(error, '插单影响计算失败'))
  } finally {
    previewing.value = false
  }
}

function resetPreview() {
  preview.value = null
}

function handleConfirm() {
  const data = requestData()
  if (!data || !preview.value) return
  Modal.confirm({
    title: '确认执行插单？',
    content: '确认后将保存新的排程时间，不会修改项目原有前置关系。',
    okText: '确认插单',
    cancelText: '取消',
    onOk: () => executeConfirm(data),
  })
}

async function executeConfirm(data: InsertOrderRequest) {
  confirming.value = true
  try {
    const result = await confirmInsert(data)
    message.success(`插单完成，创建 ${result.timeslots_created} 个时间槽`)
    await router.push('/kanban/instrument-gantt')
  } catch (error: unknown) {
    message.error(errorDetail(error, '插单执行失败'))
  } finally {
    confirming.value = false
  }
}

function formatRange(start: string | null, end: string | null) {
  if (!start || !end) return '未排程'
  return `${dayjs(start).format('MM-DD HH:mm')} – ${dayjs(end).format('MM-DD HH:mm')}`
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '待排程',
    ready: '可排程',
    scheduled: '已排程',
    blocked: '已阻塞',
    waiting_external: '等待方案签批',
    waiting_approval: '等待签批结果',
    running: '进行中',
    interrupted: '已中断',
    done: '已完成',
    completed: '已完成',
  }
  return labels[status] || status
}

function impactRoleLabel(role: string | null | undefined, isInsertTask: boolean) {
  if (role === 'anchor_downstream') return '锚点后续任务'
  if (role === 'source_downstream') return '插单后续任务'
  if (role === 'shifted') return '顺延任务'
  return isInsertTask ? '插单任务' : '顺延任务'
}

function impactRoleColor(role: string | null | undefined, isInsertTask: boolean) {
  if (role === 'anchor_downstream') return 'orange'
  if (role === 'source_downstream') return 'gold'
  return isInsertTask ? 'blue' : 'orange'
}

function impactTimeText(hours: number) {
  if (hours > 0) return `顺延 ${hours}h`
  if (hours < 0) return `提前 ${Math.abs(hours)}h`
  return '无变化'
}

function impactTimeClass(hours: number) {
  return { 'delay-positive': hours > 0, 'advance-positive': hours < 0 }
}

function errorDetail(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error)) return error.response?.data?.detail || fallback
  return fallback
}
</script>

<style scoped>
.page-header p { margin: 6px 0 0; color: var(--color-text-secondary); }
.insert-card, .preview-card { max-width: 1100px; margin-top: 16px; }
.task-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; width: 100%; }
.task-options :deep(.ant-checkbox-wrapper) { margin-inline-start: 0; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); }
.task-name { font-weight: 500; }
.task-meta { margin-left: 8px; color: var(--color-text-tertiary); font-size: 12px; }
.preview-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.delay-positive { color: var(--color-danger); font-weight: 600; }
.advance-positive { color: var(--color-success); font-weight: 600; }
@media (max-width: 760px) { .task-options { grid-template-columns: 1fr; } }
</style>
