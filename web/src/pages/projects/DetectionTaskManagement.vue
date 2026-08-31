<template>
  <div>
    <div class="page-header">
      <div>
        <h2>检测任务管理</h2>
        <p>录入无需前置任务的独立检测任务，保存后自动参与排程。</p>
      </div>
    </div>

    <div class="action-bar">
      <a-button v-if="canManage" v-operation="'create'" type="primary" :loading="isReferenceLoading" @click="openCreate">
        <PlusOutlined /> 新建检测任务
      </a-button>
      <a-input v-model:value="keyword" allow-clear placeholder="搜索编号或任务名称" style="width: 240px">
        <template #prefix><SearchOutlined /></template>
      </a-input>
    </div>
    <a-tabs v-model:activeKey="taskStatusTab" size="small">
      <a-tab-pane key="active" tab="进行中" />
      <a-tab-pane key="pending" tab="未开始" />
      <a-tab-pane key="completed" tab="已完成" />
    </a-tabs>

    <a-table
      :key="taskStatusTab"
      :loading="isLoading"
      :data-source="filteredTasks"
      :columns="columns"
      row-key="id"
      size="small"
      :pagination="{ pageSize: 10, showSizeChanger: false }"
    >
      <template #emptyText>
        <a-empty description="暂无检测任务">
          <a-button v-if="canManage" v-operation="'create'" type="primary" @click="openCreate">录入第一个检测任务</a-button>
        </a-empty>
      </template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'code'"><span class="task-code">{{ record.code }}</span></template>
        <template v-else-if="column.key === 'duration'">{{ record.task.est_duration_hours }} h</template>
        <template v-else-if="column.key === 'actualHours'">{{ formatHours(record.actual_hours) }} h</template>
        <template v-else-if="column.key === 'manager'">{{ record.task.assignee_name || record.manager_name || '-' }}</template>
        <template v-else-if="column.key === 'window'">{{ formatDate(record.start_date) }} 至 {{ formatDate(record.end_date) }}</template>
        <template v-else-if="column.key === 'priority'"><a-tag :color="priorityColor(record.priority)">{{ record.priority }}级</a-tag></template>
        <template v-else-if="column.key === 'status'"><a-tag :color="taskStatusColor(record.task.status)">{{ taskStatusLabel(record.task.status) }}</a-tag></template>
        <template v-else-if="column.key === 'instruments'">{{ instrumentCodes(record.task.instrument_ids) }}</template>
        <template v-else-if="column.key === 'actions'">
          <a-space v-if="canManage" :size="0">
            <a-button v-operation="'edit'" type="link" size="small" :disabled="!canEditDetectionTask(record)" @click="openEdit(record)"><EditOutlined /> 编辑</a-button>
            <a-popconfirm v-if="canDelete(record.task.status)" title="确定删除该检测任务及其排程？" @confirm="removeTask(record.id)">
              <a-button v-operation="'delete'" type="link" size="small" danger>删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-modal v-model:open="isFormOpen" :title="editingTask ? '编辑检测任务' : '新建检测任务'" width="680" :ok-text="formOkText" :confirm-loading="isSaving" @ok="submitForm">
      <a-alert type="info" show-icon :message="formHelpMessage" style="margin-bottom: 16px" />
      <a-form layout="vertical">
        <a-row :gutter="16">
          <a-col :span="12"><a-form-item label="任务编号" required><a-input v-model:value="form.code" :disabled="isControlledEdit" placeholder="如：JC-2026-001" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="任务名称" required><a-input v-model:value="form.name" placeholder="如：样品含量检测" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="客户名称"><a-input v-model:value="form.clientName" :disabled="isControlledEdit" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="优先级"><a-select v-model:value="form.priority" :disabled="isControlledEdit" :options="priorityOptions" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="任务类型" required><a-select v-model:value="form.taskType" :disabled="isControlledEdit" :options="taskTypeOptions" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="执行人" required><a-select v-model:value="form.assigneeId" :disabled="isControlledEdit" :options="userOptions" placeholder="默认当前登录用户，可修改" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="预计耗时（小时）" required><a-input-number v-model:value="form.duration" :disabled="isControlledEdit" :min="0.5" :step="0.5" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="切换时间（小时）"><a-input-number v-model:value="form.switchover" :disabled="isControlledEdit" :min="0" :step="0.5" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="计划开始日期" required><a-date-picker v-model:value="form.startDate" :disabled="isControlledEdit" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="12"><a-form-item label="计划完成日期" required><a-date-picker v-model:value="form.endDate" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="24"><a-form-item label="指定仪器" :required="selectedTypeRequiresInstrument"><a-select v-model:value="form.instrumentIds" mode="multiple" :disabled="isControlledEdit" :options="instrumentOptions" placeholder="选择可用于该任务的仪器" /></a-form-item></a-col>
        </a-row>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { isAxiosError } from 'axios'
import dayjs, { type Dayjs } from 'dayjs'
import { message, Modal } from 'ant-design-vue'
import { canOperatePage, permissionState } from '@/services/permissions'
import { isTaskCompleted, taskStatusColor, taskStatusLabel, taskStatusMatchesGroup, type TaskStatusGroup } from '@/utils/statusMeta'
import {
  confirmDetectionTaskInsert, createDetectionTask, deleteDetectionTask, getDetectionTasks, getInstruments, updateDetectionTask,
  getTaskTypes, getUserDirectory, type DetectionTask, type TaskTypeConfig,
} from '@/services/api'
import type { ProjectScheduleImpact } from '@/types'

const tasks = ref<DetectionTask[]>([])
const instruments = ref<{ id: number; code: string; name: string }[]>([])
const taskTypes = ref<TaskTypeConfig[]>([])
const isLoading = ref(false)
const isReferenceLoading = ref(false)
const isSaving = ref(false)
const isFormOpen = ref(false)
const editingTask = ref<DetectionTask | null>(null)
const keyword = ref('')
const taskStatusTab = ref<'active' | 'pending' | 'completed'>('active')
const userOptions = ref<{ label: string; value: number }[]>([])
const form = reactive({ code: '', name: '', clientName: '', priority: 3, taskType: '', assigneeId: null as number | null, duration: 8, switchover: 0, startDate: null as Dayjs | null, endDate: null as Dayjs | null, instrumentIds: [] as number[] })

const canManage = computed(() => {
  permissionState.permissions
  return canOperatePage('/projects/detection-tasks')
})
const taskTypeOptions = computed(() => taskTypes.value.filter(item => item.resource_type !== 'none').map(item => ({ label: item.name, value: item.code })))
const selectedTaskType = computed(() => taskTypes.value.find(item => item.code === form.taskType))
const selectedTypeRequiresHuman = computed(() => ['human', 'both'].includes(selectedTaskType.value?.resource_type || ''))
const selectedTypeRequiresInstrument = computed(() => ['instrument', 'both'].includes(selectedTaskType.value?.resource_type || ''))
const instrumentOptions = computed(() => instruments.value.map(item => ({ label: `${item.code} · ${item.name}`, value: item.id })))
const isControlledEdit = computed(() => Boolean(editingTask.value && editingTask.value.task.can_edit_resource_fields === false))
const formOkText = computed(() => editingTask.value ? (isControlledEdit.value ? '保存' : '保存并重新排程') : '保存并参与排程')
const formHelpMessage = computed(() => {
  if (!editingTask.value) return '检测任务是独立顶级任务，不设置前置任务；保存后系统会立即尝试排程。'
  if (isControlledEdit.value) return '该检测任务已进入冻结期或执行中，仅允许修改任务名称和计划完成日期。'
  return '保存后系统会删除原排程并重新计算检测任务排程。'
})
const filteredTasks = computed(() => tasks.value.filter(item => {
  if (!statusMatchesTab(item.task.status, taskStatusTab.value)) return false
  const search = keyword.value.trim().toLowerCase()
  if (search && !`${item.code} ${item.name}`.toLowerCase().includes(search)) return false
  return true
}))
const columns = [
  { title: '任务编号', key: 'code', width: 140 }, { title: '检测任务名称', dataIndex: 'name', key: 'name' },
  { title: '执行人', key: 'manager', width: 100 }, { title: '预计耗时', key: 'duration', width: 90 },
  { title: '实际工时', key: 'actualHours', width: 80, responsive: ['xl'] },
  { title: '计划时间窗', key: 'window', width: 190 }, { title: '仪器', key: 'instruments', ellipsis: true, responsive: ['lg'] },
  { title: '优先级', key: 'priority', width: 70, responsive: ['xl'] }, { title: '状态', key: 'status', width: 80 },
  { title: '操作', key: 'actions', width: 120 },
]
const priorityOptions = [{ label: '一级（最高）', value: 1 }, { label: '二级', value: 2 }, { label: '三级', value: 3 }]

function statusMatchesTab(status: string, tab: TaskStatusGroup) { return taskStatusMatchesGroup(status, tab) }
function canEditDetectionTask(record: DetectionTask) { return record.task.can_edit_basic_fields !== false }

async function openCreate() {
  await ensureFormReferenceData()
  editingTask.value = null
  Object.assign(form, { code: '', name: '', clientName: '', priority: 3, taskType: defaultDetectionTaskType(), assigneeId: currentUserId(), duration: 8, switchover: 0, startDate: dayjs(), endDate: dayjs().add(7, 'day'), instrumentIds: [] })
  isFormOpen.value = true
}
async function openEdit(record: DetectionTask) {
  await ensureFormReferenceData()
  editingTask.value = record
  Object.assign(form, { code: record.code, name: record.name, clientName: record.client_name || '', priority: record.priority, taskType: record.task.task_type, assigneeId: record.task.assignee_id, duration: record.task.est_duration_hours || 8, switchover: record.task.switchover_hours || 0, startDate: dayjs(record.start_date), endDate: dayjs(record.end_date), instrumentIds: [...record.task.instrument_ids] })
  isFormOpen.value = true
}
async function submitForm() {
  if (!form.code.trim() || !form.name.trim() || !form.startDate || !form.endDate || !form.taskType) return void message.warning('请完整填写必填项')
  if (!form.assigneeId) return void message.warning('检测任务必须选择执行人')
  if (selectedTypeRequiresInstrument.value && !form.instrumentIds.length) return void message.warning('该任务类型需要指定仪器')
  isSaving.value = true
  try {
    const assigneeId = form.assigneeId
    const payload = { code: form.code, name: form.name, client_name: form.clientName || undefined, priority: form.priority, manager_id: assigneeId, start_date: form.startDate.startOf('day').format('YYYY-MM-DDTHH:mm:ss'), end_date: form.endDate.endOf('day').format('YYYY-MM-DDTHH:mm:ss'), task_type: form.taskType, est_duration_hours: form.duration, switchover_hours: form.switchover, requires_instrument: selectedTypeRequiresInstrument.value, requires_human: selectedTypeRequiresHuman.value, allow_split: false, allow_transfer: false, instrument_ids: form.instrumentIds, assignee_id: assigneeId }
    const result = editingTask.value ? await updateDetectionTask(editingTask.value.id, payload) : await createDetectionTask(payload)
    isFormOpen.value = false
    await loadTasks()
    if (result.schedule_status === 'insert_confirmation_required' && result.preview_token) {
      showInsertConfirmation(result)
    } else if (result.schedule_status === 'error') {
      message.warning(result.schedule_message || '任务已保存，但暂未排入日程')
    } else {
      message.success(result.schedule_message || (editingTask.value ? '检测任务已更新并重新排程' : '检测任务已保存并完成排程'))
    }
  } catch (error: unknown) { message.error(errorDetail(error, editingTask.value ? '检测任务更新失败' : '检测任务创建失败')) } finally { isSaving.value = false }
}
/** 插单影响的结构化渲染。

    后端同时返回了整段提示语和结构化的 project_impacts，早先直接把整段话塞进
    弹窗，几个项目用分号连成一行，关键数字（顺延几小时、调整后什么时候开始、
    会不会超结题日）全埋在文字里，看的人得逐字读。这里改成一个项目一张卡片，
    数字单独成行，超期的用红色标出来。 */
function impactTitle(impact: ProjectScheduleImpact) {
  // 测试项目常把项目号和项目名设成同一个词，原样拼接会显示成「测试项目A · 测试项目A」。
  return impact.project_name && impact.project_name !== impact.project_code
    ? `${impact.project_code} · ${impact.project_name}`
    : impact.project_code
}

function renderInsertImpact(task: DetectionTask) {
  const impacts = task.project_impacts || []
  const intro = h(
    'div',
    { class: 'insert-impact-intro' },
    '本次插单需要移动同优先级或低优先级的未开始项目任务，请确认以下影响：',
  )
  if (!impacts.length) {
    return h('div', [intro, h('div', { class: 'insert-impact-empty' }, task.schedule_message || '暂无可展示的影响明细')])
  }
  return h('div', { class: 'insert-impact' }, [
    intro,
    ...impacts.map(impact => h('div', { class: 'insert-impact-card' }, [
      h('div', { class: 'insert-impact-title' }, impactTitle(impact)),
      h('dl', { class: 'insert-impact-rows' }, [
        h('div', [h('dt', '预计顺延'), h('dd', `${Math.max(0, impact.delay_hours)} 小时`)]),
        h('div', [
          h('dt', '调整后开始'),
          h('dd', impact.new_start ? dayjs(impact.new_start).format('YYYY-MM-DD HH:mm') : '暂无'),
        ]),
        h('div', [
          h('dt', '结题日期'),
          h(
            'dd',
            { class: impact.exceeds_end_date ? 'insert-impact-danger' : 'insert-impact-safe' },
            impact.exceeds_end_date
              ? `超出 ${impact.overdue_hours} 小时`
              : '未超出',
          ),
        ]),
        ...(impact.pending_approval_hours > 0
          ? [h('div', [
              h('dt', '签批后待排'),
              h('dd', `${impact.pending_approval_hours} 小时（已计入推演）`),
            ])]
          : []),
      ]),
    ])),
  ])
}

function showInsertConfirmation(task: DetectionTask) {
  Modal.confirm({
    title: '确认检测任务插入排程',
    width: 560,
    icon: null,
    content: renderInsertImpact(task),
    okText: '确认并重排',
    cancelText: '暂不调整',
    async onOk() {
      if (!task.preview_token) return
      const result = await confirmDetectionTaskInsert(task.id, task.preview_token)
      await loadTasks()
      message.success(result.schedule_message || '检测任务已插入并完成重排')
    },
  })
}
function defaultDetectionTaskType() {
  const dailyDetection = taskTypes.value.find(item => item.name.trim() === '日常检测' || ['daily_detection', 'routine_detection', 'daily_test'].includes(item.code.toLowerCase()))
  return dailyDetection?.code || taskTypeOptions.value[0]?.value || ''
}
function currentUserId(): number | null {
  try {
    const user = JSON.parse(localStorage.getItem('user') || '{}') as { id?: unknown }
    return typeof user.id === 'number' ? user.id : null
  } catch { return null }
}
async function removeTask(id: number) { try { await deleteDetectionTask(id); message.success('检测任务已删除'); await loadTasks() } catch (error: unknown) { message.error(errorDetail(error, '删除失败')) } }
async function loadTasks() { isLoading.value = true; try { tasks.value = await getDetectionTasks() } catch { message.error('检测任务加载失败') } finally { isLoading.value = false } }
async function loadInstruments() {
  try { instruments.value = await getInstruments() }
  catch { message.error('仪器列表加载失败') }
}
async function loadUsers() {
  try {
    const users = await getUserDirectory()
    userOptions.value = users.filter(user => user.is_active).map(user => ({ label: user.display_name, value: user.id }))
  } catch { message.error('执行人列表加载失败') }
}
async function loadTaskTypes() {
  try { taskTypes.value = (await getTaskTypes()).filter(item => item.is_active) }
  catch { message.error('任务类型加载失败') }
}
async function ensureFormReferenceData() {
  const loaders: Promise<void>[] = []
  if (!instruments.value.length) loaders.push(loadInstruments())
  if (!userOptions.value.length) loaders.push(loadUsers())
  if (!taskTypes.value.length) loaders.push(loadTaskTypes())
  if (!loaders.length) return
  isReferenceLoading.value = true
  try { await Promise.all(loaders) }
  finally { isReferenceLoading.value = false }
}
function instrumentCodes(ids: number[]) { return ids.map(id => instruments.value.find(item => item.id === id)?.code).filter(Boolean).join('、') || '-' }
function formatDate(value: string) { return dayjs(value).format('YYYY-MM-DD') }
function formatHours(value: number) { return Number(value.toFixed(2)) }
function priorityColor(priority: number) { return priority === 1 ? 'red' : priority === 2 ? 'orange' : 'blue' }
function canDelete(status: string) {
  if (!isTaskCompleted(status)) return true
  try {
    const user = JSON.parse(localStorage.getItem('user') || '{}') as { role?: unknown; roles?: unknown }
    const roles = Array.isArray(user.roles) ? user.roles : []
    return user.role === '系统管理员' || roles.includes('系统管理员')
  } catch { return false }
}
function errorDetail(error: unknown, fallback: string) { return isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail || fallback : fallback }

onMounted(() => { loadTasks() })
</script>

<style scoped>
.page-header p { margin: 4px 0 0; color: #64748b; }
.task-code { color: #2563eb; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600; }
</style>

<!-- Modal.confirm 的内容会被传送到组件之外，scoped 样式匹配不上，必须用全局样式。 -->
<style>
.insert-impact-intro {
  margin-bottom: 12px;
  color: #334155;
  line-height: 1.6;
}

.insert-impact-empty {
  color: #64748b;
  line-height: 1.6;
}

.insert-impact {
  max-height: 420px;
  overflow-y: auto;
}

.insert-impact-card {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.insert-impact-card:last-child {
  margin-bottom: 0;
}

.insert-impact-title {
  margin-bottom: 8px;
  color: #0f172a;
  font-weight: 600;
}

.insert-impact-rows {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px 20px;
  margin: 0;
}

.insert-impact-rows > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.insert-impact-rows dt {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 12px;
}

.insert-impact-rows dd {
  margin: 0;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}

.insert-impact-danger {
  color: #dc2626;
  font-weight: 600;
}

.insert-impact-safe {
  color: #16a34a;
}
</style>
