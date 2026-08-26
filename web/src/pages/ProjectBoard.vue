<template>
  <div>
    <div class="page-header"><h2>项目看板</h2></div>
    <div class="action-bar">
      <a-button v-if="canManageProjectInfo" v-operation="'create'" type="primary" @click="openCreate"><PlusOutlined /> 新建项目</a-button>
      <a-input placeholder="项目编号" allowClear style="width: 150px" v-model:value="filterCode"><template #prefix><SearchOutlined /></template></a-input>
      <a-input placeholder="项目名称" allowClear style="width: 180px" v-model:value="filterName"><template #prefix><SearchOutlined /></template></a-input>
      <a-input placeholder="客户名称" allowClear style="width: 150px" v-model:value="filterClient"><template #prefix><SearchOutlined /></template></a-input>
      <a-range-picker :placeholder="['开始日期','结束日期']" style="width: 240px" v-model:value="filterDateRange" allowClear />
    </div>
    <a-tabs v-model:activeKey="projectStatusTab" size="small">
      <a-tab-pane key="active" tab="进行中" />
      <a-tab-pane key="pending" tab="未开始" />
      <a-tab-pane key="completed" tab="已完成" />
    </a-tabs>
    <a-spin v-if="loading" size="large" style="display: block; margin: 50px auto" />
    <a-table
      v-else
      :key="projectStatusTab"
      :dataSource="filtered"
      :columns="columns"
      rowKey="id"
      size="small"
      :pagination="{ pageSize: 10, showSizeChanger: false }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'priority'">
          <a-tag :color="priorityColor(record.priority)">{{ priorityLabel(record.priority) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="projectStatusColor(record.status)">{{ statusLabels[record.status] || record.status }}</a-tag>
        </template>
        <template v-else-if="column.key === 'client'">{{ record.client_name || '-' }}</template>
        <template v-else-if="column.key === 'name'">{{ record.name || '-' }}</template>
        <template v-else-if="column.key === 'manager'">{{ record.manager_name || '-' }}</template>
        <template v-else-if="column.key === 'estimated_hours'">{{ record.estimated_hours ?? '-' }}</template>
        <template v-else-if="column.key === 'actual_hours'">{{ formatHours(record.actual_hours) }}</template>
        <template v-else-if="column.key === 'start'">{{ record.start_date ? dayjs(record.start_date).format('YYYY-MM-DD') : '-' }}</template>
        <template v-else-if="column.key === 'end'">{{ record.end_date ? dayjs(record.end_date).format('YYYY-MM-DD') : '-' }}</template>
        <template v-else-if="column.key === 'code'">
          <a-button type="link" size="small" :class="{ 'project-code-overdue': record.delivery_status === 'overdue' }" style="padding:0;font-family:monospace;font-weight:600;font-size:12px" @click="handleViewDetail(record.id)">{{ record.code }}</a-button>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space :size="0">
            <a-button type="link" size="small" @click="openPlanDetail(record.id)">详情</a-button>
            <a-button v-if="canManageProjectInfo" v-operation="'edit'" type="link" size="small" @click="openEditFromTable(record)"><EditOutlined /> 编辑</a-button>
            <a-popconfirm v-if="canDeleteProject && record.status !== 'completed'" title="确定删除该项目及其所有任务？" @confirm="handleDeleteProject(record.id)">
              <a-button type="link" size="small" danger>删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
    <a-modal title="新建项目" v-model:open="createOpen" @ok="handleCreate" width="640" okText="创建项目">
      <a-form layout="vertical">
        <a-form-item label="项目名称" required><a-input v-model:value="cf.name" placeholder="如：某注射剂基因毒杂质研究" /></a-form-item>
        <a-form-item label="项目编号" required><a-input v-model:value="cf.code" placeholder="如：GT-2026-001" /></a-form-item>
        <a-form-item label="客户名称"><a-input v-model:value="cf.client_name" placeholder="如：某制药公司" /></a-form-item>
        <a-form-item label="预计工时(小时)">
          <a-input-number v-model:value="cf.estimated_hours" :min="0" :step="0.5" style="width: 180px" placeholder="整个项目预计工时" />
        </a-form-item>
        <a-form-item label="项目负责人">
          <a-select v-model:value="cf.manager_id" placeholder="选择项目负责人" allowClear :options="userOptions" />
        </a-form-item>
        <a-form-item label="项目优先级">
          <a-select v-model:value="cf.priority" :options="priorityOptions" />
        </a-form-item>
        <a-space :size="16" style="width: 100%">
          <a-form-item label="项目开始日期" style="width: 200px"><a-date-picker v-model:value="cf.start_date" style="width: 100%" /></a-form-item>
          <a-form-item label="项目结题日期" style="width: 200px"><a-date-picker v-model:value="cf.end_date" style="width: 100%" /></a-form-item>
        </a-space>
      </a-form>
    </a-modal>
    <a-modal title="编辑项目" v-model:open="editOpen" @ok="handleUpdateProject" width="640" okText="保存">
      <a-form layout="vertical">
        <a-form-item label="项目名称" required><a-input v-model:value="ef.name" /></a-form-item>
        <a-form-item label="项目编号" required><a-input v-model:value="ef.code" /></a-form-item>
        <a-form-item label="客户名称"><a-input v-model:value="ef.client_name" /></a-form-item>
        <a-form-item label="预计工时(小时)">
          <a-input-number v-model:value="ef.estimated_hours" :min="0" :step="0.5" style="width: 180px" placeholder="整个项目预计工时" />
        </a-form-item>
        <a-form-item label="项目负责人">
          <a-select v-model:value="ef.manager_id" placeholder="选择项目负责人" allowClear :options="userOptions" />
        </a-form-item>
        <a-form-item label="项目优先级">
          <a-select v-model:value="ef.priority" :options="priorityOptions" />
        </a-form-item>
        <a-space :size="16" style="width: 100%">
          <a-form-item label="项目开始日期" style="width: 200px"><a-date-picker v-model:value="ef.start_date" style="width: 100%" /></a-form-item>
          <a-form-item label="项目结题日期" style="width: 200px"><a-date-picker v-model:value="ef.end_date" style="width: 100%" /></a-form-item>
        </a-space>
      </a-form>
    </a-modal>
    <a-drawer :title="selectedProject?.name || '项目详情'" v-model:open="detailOpen" width="720">
      <template #extra><a-button v-if="canManageProjectInfo" v-operation="'edit'" @click="openEditProject"><EditOutlined /> 编辑项目</a-button></template>
      <a-tabs v-if="selectedProject" defaultActiveKey="tasks">
        <a-tab-pane key="tasks" tab="任务列表">
          <a-descriptions size="small" bordered :column="2" style="margin-bottom: 12px">
            <a-descriptions-item label="项目编号">{{ selectedProject.code }}</a-descriptions-item>
            <a-descriptions-item label="状态">
              <a-tag :color="projectStatusColor(selectedProject.status)">{{ statusLabels[selectedProject.status] || selectedProject.status }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="客户">{{ selectedProject.client_name || '-' }}</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ selectedProject.manager_name || '-' }}</a-descriptions-item>
            <a-descriptions-item label="预计工时(h)">{{ selectedProject.estimated_hours ?? '-' }}</a-descriptions-item>
            <a-descriptions-item label="优先级">{{ priorityLabel(selectedProject.priority) }}</a-descriptions-item>
            <a-descriptions-item label="项目开始日期">{{ selectedProject.start_date ? dayjs(selectedProject.start_date).format('YYYY-MM-DD') : '-' }}</a-descriptions-item>
            <a-descriptions-item label="项目结题日期">{{ selectedProject.end_date ? dayjs(selectedProject.end_date).format('YYYY-MM-DD') : '-' }}</a-descriptions-item>
          </a-descriptions>
          <a-table :dataSource="selectedProject.tasks || []" rowKey="id" size="small" :pagination="false">
            <a-table-column title="任务名称" dataIndex="name" key="name" />
            <a-table-column title="类型" key="type" width="90"><template #default="{ record }"><a-tag :color="getTaskTypeColor(record.task_type)" style="font-size:11px">{{ getTaskTypeName(record.task_type) }}</a-tag></template></a-table-column>
            <a-table-column title="预计耗时(h)" dataIndex="est_duration_hours" key="dur" width="100" />
            <a-table-column title="状态" key="status" width="80"><template #default="{ record }"><a-tag :color="taskStatusColor(record.status)" style="font-size:11px">{{ taskStatusLabel(record.status) }}</a-tag></template></a-table-column>
            <a-table-column title="负责人" key="assignee" width="80"><template #default="{ record }"><span style="font-size:12px;color:#475569">{{ record.assignee_name || "-" }}</span></template></a-table-column>
            <a-table-column title="操作" key="actions" width="60">
              <template #default="{ record }"><a-button v-operation="'task_edit'" type="link" size="small" @click="openEditTask(record)"><EditOutlined /></a-button></template>
            </a-table-column>
          </a-table>
          <a-button v-if="!isAddingTask" v-operation="'task_edit'" type="dashed" size="small" block @click="startAddTask" style="margin-top:8px"><PlusOutlined /> 添加任务</a-button>
          <div v-else style="margin-top:8px;padding:8px;background:#f8fafc;border-radius:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <a-input v-model:value="newTaskName" size="small" placeholder="任务名称" style="width:150px" />
            <a-select v-model:value="newTaskType" size="small" style="width:100px" :options="taskTypeOptions" placeholder="类型" />
            <a-input-number v-model:value="newTaskDuration" :min="0.5" :step="0.5" size="small" style="width:70px" addon-after='h' />
            <a-select v-model:value="newAssigneeId" size="small" style="width:110px" placeholder="负责人" allowClear :options="userOptions" />
            <a-button type="primary" size="small" @click="handleAddInline">确认</a-button>
            <a-button size="small" @click="cancelAddTask">取消</a-button>
          </div>
        </a-tab-pane>
        <a-tab-pane key="dag" tab="依赖关系">
          <div v-if="dagData">
            <div style="font-size: 13px; font-weight: 600; color: #334155; margin-bottom: 8px">任务节点 ({{ dagData.nodes.length }})</div>
            <a-tag v-for="n in dagData.nodes" :key="n.id" :color="n.requires_instrument ? '#2563eb' : '#94a3b8'" style="margin: 4px">{{ n.name }} ({{ n.type }})</a-tag>
            <div v-if="dagData.edges.length" style="margin-top: 16px">
              <strong style="color: #334155; font-size: 13px">依赖关系：</strong>
              <div v-for="(e,i) in dagData.edges" :key="i" style="font-size: 12px; color: #64748b; margin-top: 4px">任务{{ e.from }} -> 任务{{ e.to }}</div>
            </div>
          </div>
          <a-spin v-else />
        </a-tab-pane>
      </a-tabs>
    </a-drawer>
    <a-modal :title="editingTask ? '编辑任务' : '添加任务'" v-model:open="taskOpen" @ok="handleTaskSubmit" width="600" :okText="editingTask ? '保存' : '添加任务'">
      <a-form layout="vertical">
        <a-form-item label="任务名称" required><a-input v-model:value="tf.name" placeholder="如：LC-MS方法开发" /></a-form-item>
        <a-space :size="16" style="width: 100%">
          <a-form-item label="负责人"><a-select v-model:value="tf.assignee_id" style="width:100%" placeholder="选择负责人" allowClear :options="userOptions" /></a-form-item>
        <a-form-item label="任务类型"><a-select v-model:value="tf.task_type" style="width: 160px" :options="taskTypeOptions" placeholder="选择任务类型" /></a-form-item>
          <a-form-item label="预计耗时(小时)" required><a-input-number v-model:value="tf.est_duration_hours" :min="0.5" :step="0.5" style="width: 120px" /></a-form-item>
          <a-form-item label="切换时间(h)"><a-input-number v-model:value="tf.switchover_hours" :min="0" :step="0.5" style="width: 100px" /></a-form-item>
        </a-space>
        <a-form-item label="前置依赖任务">
          <a-select mode="multiple" v-model:value="tf.predecessor_ids" placeholder="选择依赖的前置任务（可选）"
            :options="(selectedProject?.tasks || []).map(t => ({ label: t.name + ' (' + (taskTypeMap[t.task_type]?.name || t.task_type) + ')', value: t.id }))" />
        </a-form-item>
        <a-divider>指定仪器</a-divider>
        <a-form-item label="选择仪器">
          <a-select v-model:value="tf.instrument_ids" mode="multiple" placeholder="选择该任务所需的仪器（可多选）" :options="instrumentOptions" style="width: 100%" />
        </a-form-item>
      </a-form>
    </a-modal>
    <ProjectHealthDrawer :open="healthOpen" :loading="healthLoading" :error="healthError" :health="projectHealth" @close="healthOpen = false" />
  </div>
</template>
<script setup lang="ts">
import { ref, computed, reactive, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { isAxiosError } from 'axios'
import { PlusOutlined, EditOutlined, SearchOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import { getProjects, createProject, updateProject, deleteProject, getProject, getProjectDAG, addTask, updateTask, deleteTask, getUserDirectory, getTaskTypes, getInstruments, generateSchedule, getProjectHealth, type Project, type Task, type DAGData, type TaskTypeConfig, type ProjectHealth } from '@/services/api'
import ProjectHealthDrawer from './ProjectHealthDrawer.vue'
  import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { canOperateAction, canOperatePage, permissionState } from '@/services/permissions'
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'
const projects = ref<Project[]>([])
const router = useRouter()
const loading = ref(true)
const createOpen = ref(false)
const editOpen = ref(false)
const detailOpen = ref(false)
const healthOpen = ref(false)
const healthLoading = ref(false)
const healthError = ref('')
const projectHealth = ref<ProjectHealth | null>(null)
const taskOpen = ref(false)
const selectedProject = ref<Project | null>(null)
const editingTask = ref<Task | null>(null)
const isAddingTask = ref(false)
const newTaskName = ref("")
const newTaskType = ref("")
const newTaskDuration = ref(8)
const newAssigneeId = ref<number | null>(null)
const userOptions = ref<{ label: string; value: number }[]>([])
const instrumentOptions = ref<{ label: string; value: number }[]>([])
const taskTypeOptions = ref<{ label: string; value: string; resource_type: string }[]>([])
const taskTypeMap = ref<Record<string, TaskTypeConfig>>({})
const dagData = ref<DAGData | null>(null)
const filterCode = ref('')
const filterName = ref('')
const filterClient = ref('')
const projectStatusTab = ref<'active' | 'pending' | 'completed'>('active')
watch(projectStatusTab, () => { fetchProjects() })
const projectCodeCollator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const filterDateRange = ref<any>(null)
const canManageProjectInfo = computed(() => {
  permissionState.permissions
  return canOperatePage('/projects/ledger')
})
const canDeleteProject = computed(() => {
  permissionState.permissions
  return canOperateAction('/projects/ledger', 'delete')
})
const cf = reactive({ name: '', code: '', client_name: '', estimated_hours: null as number | null, manager_id: null as number | null, priority: 3, start_date: null as any, end_date: null as any })
const ef = reactive({ name: '', code: '', client_name: '', estimated_hours: null as number | null, manager_id: null as number | null, priority: 3, start_date: null as any, end_date: null as any })
const tf = reactive({ name: '', task_type: '', est_duration_hours: 8, switchover_hours: 0.5, predecessor_ids: [] as number[], instrument_ids: [] as number[], assignee_id: null as number | null })
const statusLabels: Record<string, string> = { active: '进行中', completed: '已完成', pending: '未开始', suspended: '已暂停', cancelled: '已取消', draft: '草稿' }
const priorityOptions = [
  { label: '一级', value: 1 },
  { label: '二级', value: 2 },
  { label: '三级', value: 3 },
]
function priorityLabel(priority: number) {
  return priorityOptions.find(option => option.value === priority)?.label || `${priority}级`
}
function priorityColor(priority: number) {
  if (priority === 1) return '#dc2626'
  if (priority === 2) return '#ea580c'
  return '#2563eb'
}
function projectStatusColor(status: string) {
  const colors: Record<string, string> = {
    pending: '#94a3b8',
    active: '#16a34a',
    completed: '#7c3aed',
    suspended: '#f59e0b',
    cancelled: '#64748b',
    draft: '#94a3b8',
  }
  return colors[status] || '#94a3b8'
}
const capValOpts: Record<string, { label: string; value: string }[]> = {
  '离子源': [{ label: 'ESI', value: 'ESI' }, { label: 'APCI', value: 'APCI' }],
  '质量分析器': [{ label: 'QqQ', value: 'QqQ' }, { label: 'Q-TOF', value: 'Q-TOF' }],
  '方法类型': [{ label: '基因毒杂质', value: '基因毒杂质' }, { label: '有关物质', value: '有关物质' }, { label: '含量测定', value: '含量测定' }],
  '灵敏度等级': [{ label: '痕量', value: '痕量' }, { label: '常量', value: '常量' }],
}
const filtered = computed(() => projects.value.filter(p => {
  if (!statusMatchesTab(p.status, projectStatusTab.value)) return false
  if (filterCode.value && !p.code.toLowerCase().includes(filterCode.value.toLowerCase())) return false
  if (filterName.value && !p.name.toLowerCase().includes(filterName.value.toLowerCase())) return false
  if (filterClient.value && !(p.client_name || '').toLowerCase().includes(filterClient.value.toLowerCase())) return false
  if (filterDateRange.value?.[0] && filterDateRange.value?.[1] && p.start_date) {
    const d = dayjs(p.start_date)
    if (d.isBefore(filterDateRange.value[0]) || d.isAfter(filterDateRange.value[1])) return false
  }
  return true
}).sort((left, right) =>
  projectCodeCollator.compare(right.code, left.code) || right.id - left.id,
))
function statusMatchesTab(status: string, tab: 'active' | 'pending' | 'completed') {
  if (tab === 'pending') return ['pending', 'draft'].includes(status)
  return status === tab
}
function formatHours(value: number | null | undefined) {
  return value == null ? '-' : Number(value.toFixed(2))
}
const columns = [
  { title: '项目编号', dataIndex: 'code', key: 'code', width: 130 },
  { title: '项目名称', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
  { title: '客户', dataIndex: 'client_name', key: 'client', width: 140 },
  { title: '负责人', dataIndex: 'manager', key: 'manager', width: 90 },
  { title: '预计工时(h)', dataIndex: 'estimated_hours', key: 'estimated_hours', width: 90 },
  { title: '实际工时(h)', dataIndex: 'actual_hours', key: 'actual_hours', width: 90 },
  { title: '计划开始', dataIndex: 'start_date', key: 'start', width: 110 },
  { title: '计划完成', dataIndex: 'end_date', key: 'end', width: 110 },
  { title: '优先级', dataIndex: 'priority', key: 'priority', width: 80 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  { title: '操作', key: 'actions', width: 200 },
]
async function fetchProjects() { loading.value = true; try { projects.value = await getProjects(projectStatusTab.value) } catch { message.error('加载项目失败') } finally { loading.value = false } }
async function ensureProjectFormOptions() { await Promise.all([loadUsers(), loadTaskTypes()]) }
async function ensureTaskFormOptions() { await Promise.all([loadUsers(), loadTaskTypes(), loadInstruments()]) }
async function openCreate() {
  if (!ensureCanManageProjectInfo()) return
  await ensureProjectFormOptions()
  Object.assign(cf, { name: '', code: '', client_name: '', estimated_hours: null, manager_id: null, priority: 3, start_date: null, end_date: null }); createOpen.value = true
}
async function handleCreate() {
  if (!ensureCanManageProjectInfo()) return
  try {
    await createProject({
      ...cf,
      start_date: formatProjectStart(cf.start_date),
      end_date: formatProjectEnd(cf.end_date),
    } as any)
    message.success('项目创建成功'); createOpen.value = false; fetchProjects()
  } catch (error: unknown) { message.error(errorDetail(error, '创建失败')) }
}
async function handleUpdateProject() {
  if (!selectedProject.value) return
  if (!ensureCanManageProjectInfo()) return
  try {
    await updateProject(selectedProject.value.id, {
      ...ef,
      start_date: formatProjectStart(ef.start_date),
      end_date: formatProjectEnd(ef.end_date),
    } as any)
    message.success('项目更新成功'); editOpen.value = false; fetchProjects()
  } catch (error: unknown) { message.error(errorDetail(error, '更新失败')) }
}
function errorDetail(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error)) return error.response?.data?.detail || fallback
  return fallback
}
function formatProjectStart(value: Dayjs | null) {
  return value ? dayjs(value).startOf('day').format('YYYY-MM-DDTHH:mm:ss') : undefined
}
function formatProjectEnd(value: Dayjs | null) {
  return value ? dayjs(value).endOf('day').format('YYYY-MM-DDTHH:mm:ss') : undefined
}
function ensureCanManageProjectInfo() {
  if (canManageProjectInfo.value) return true
  message.warning('当前角色无权新建或编辑项目信息')
  return false
}
function getStoredUserRole() {
  const raw = localStorage.getItem('user')
  if (!raw) return ''
  try {
    const parsed = JSON.parse(raw) as { role?: unknown }
    return typeof parsed.role === 'string' ? parsed.role : ''
  } catch {
    return ''
  }
}
async function handleViewDetail(id: number) {
  healthOpen.value = true
  healthLoading.value = true
  healthError.value = ''
  try { projectHealth.value = await getProjectHealth(id) }
  catch { healthError.value = '项目健康数据加载失败' }
  finally { healthLoading.value = false }
}
function openPlanDetail(id: number) {
  router.push({ path: '/projects/plan-breakdown', query: { id } })
}
function openEditFromTable(record: Project) { selectedProject.value = record; openEditProject() }
async function openEditProject() {
  if (!selectedProject.value) return
  if (!ensureCanManageProjectInfo()) return
  await ensureProjectFormOptions()
  const p = selectedProject.value
  Object.assign(ef, { name: p.name, code: p.code, client_name: p.client_name || '', estimated_hours: p.estimated_hours ?? null, manager_id: p.manager_id || null, priority: p.priority, start_date: p.start_date ? dayjs(p.start_date) : null, end_date: p.end_date ? dayjs(p.end_date) : null })
  editOpen.value = true
}
const taskList = computed(() => {
  const tasks = [...(selectedProject.value?.tasks || [])]
  if (isAddingTask.value) {
    tasks.push({ _isNew: true, id: -1 } as any)
  }
  return tasks
})
function getTaskTypeName(code: string) { return taskTypeMap.value[code]?.name || code }
function getTaskTypeColor(code: string) { const r = taskTypeMap.value[code]?.resource_type; return r === "instrument" ? "#2563eb" : r === "human" ? "#16a34a" : "#7c3aed" }
function startAddTask() { isAddingTask.value = true; newTaskName.value = ''; newTaskType.value = 'instrument'; newTaskDuration.value = 8; newAssigneeId.value = null }
function cancelAddTask() { isAddingTask.value = false }
async function handleAddInline() {
  if (!newTaskName.value || !selectedProject.value) { message.error('请输入任务名称'); return }
  try {
    await addTask(selectedProject.value.id, {
      name: newTaskName.value,
      task_type: newTaskType.value,
      requires_instrument: (taskTypeMap.value[newTaskType.value]?.resource_type || 'both') !== 'human',
      est_duration_hours: newTaskDuration.value,
      switchover_hours: 0.5,
      predecessor_ids: [],
      assignee_id: newAssigneeId.value || null,
      instrument_ids: [],
    } as any)
    message.success('任务添加成功')
    isAddingTask.value = false
    const [p, d] = await Promise.all([getProject(selectedProject.value.id), getProjectDAG(selectedProject.value.id)])
    selectedProject.value = p; dagData.value = d
  } catch (error: unknown) { message.error(errorDetail(error, '添加失败')) }
}
async function handleDeleteProject(projId: number) {
  try {
    await deleteProject(projId)
    message.success('项目已删除')
    await fetchProjects()
    if (selectedProject.value?.id === projId) selectedProject.value = null
  } catch (e: any) {
    console.error('Delete project error:', e)
    const msg = e?.response?.data?.detail || e?.message || '删除失败'
    message.error(msg)
  }
}
async function handleDeleteTask(taskId: number) {
  if (!selectedProject.value) return
  try {
    await deleteTask(taskId)
    message.success('任务已删除')
    const [p, d] = await Promise.all([getProject(selectedProject.value.id), getProjectDAG(selectedProject.value.id)])
    selectedProject.value = p; dagData.value = d
  } catch { message.error('删除失败') }
}
async function openAddTask() { await ensureTaskFormOptions(); editingTask.value = null; Object.assign(tf, { name: '', task_type: taskTypeOptions.value[0]?.value || '', est_duration_hours: 8, switchover_hours: 0.5, predecessor_ids: [],
      assignee_id: newAssigneeId.value || null, instrument_ids: [] }); taskOpen.value = true }
async function openEditTask(t: Task) {
  await ensureTaskFormOptions()
  editingTask.value = t
  Object.assign(tf, { name: t.name, task_type: t.task_type, est_duration_hours: t.est_duration_hours || 8, switchover_hours: t.switchover_hours, predecessor_ids: t.predecessor_ids || [], instrument_ids: t.instrument_ids || [], assignee_id: t.assignee_id || null })
  taskOpen.value = true
}
async function handleTaskSubmit() {
  if (!selectedProject.value) return
  const payload = {
    name: tf.name, task_type: tf.task_type,
    requires_instrument: (taskTypeMap.value[tf.task_type]?.resource_type || 'both') !== 'human',
    est_duration_hours: tf.est_duration_hours,
    switchover_hours: tf.switchover_hours,
    predecessor_ids: tf.predecessor_ids,
      assignee_id: tf.assignee_id || null,
    instrument_ids: tf.instrument_ids,
  }
  try {
    if (editingTask.value) {
      await updateTask(editingTask.value.id, payload as any)
      message.success('任务更新成功')
    } else {
      await addTask(selectedProject.value.id, payload as any)
      message.success('任务添加成功')
    }
    taskOpen.value = false; editingTask.value = null
    const [p, d] = await Promise.all([getProject(selectedProject.value.id), getProjectDAG(selectedProject.value.id)])
    selectedProject.value = p; dagData.value = d
  } catch (error: unknown) { message.error(errorDetail(error, '操作失败')) }
}
async function loadInstruments() {
  try {
    const insts = await getInstruments()
    instrumentOptions.value = insts.map(i => ({ label: i.name + ' (' + i.code + ')', value: i.id }))
  } catch (e) { console.error("loadInstruments failed:", e) }
}
async function loadUsers() {
  try {
    const users = await getUserDirectory()
    userOptions.value = users.filter(u => u.is_active).map(u => ({ label: u.display_name, value: u.id }))
  } catch (e) { console.error("loadUsers failed:", e) }
}
async function loadTaskTypes() {
  try {
    const types = await getTaskTypes()
    const active = types.filter(t => t.is_active)
    taskTypeOptions.value = active.map(t => ({ label: t.name, value: t.code, resource_type: t.resource_type }))
    taskTypeMap.value = {}
    active.forEach(t => { taskTypeMap.value[t.code] = t })
    return active
  } catch (e) { console.error('loadTaskTypes failed:', e); return [] }
}
onMounted(() => { fetchProjects() })
</script>
