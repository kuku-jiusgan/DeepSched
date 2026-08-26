<template>
  <div>
    <div class="page-header">
      <a-button type="text" @click="goBack"><LeftOutlined /> 返回</a-button>
      <h2 style="margin: 0 0 0 8px">{{ project?.name || '项目计划拆解' }}</h2>
      <a-tag v-if="project" :color="project.status === 'active' ? '#16a34a' : '#94a3b8'" style="margin-left: 12px">{{ statusLabels[project.status] || project.status }}</a-tag>
    </div>
    <a-spin v-if="loading" size="large" style="display: block; margin: 50px auto" />
    <template v-else-if="project">
      <div class="project-info">
        <a-descriptions :column="4" size="small" bordered>
          <a-descriptions-item label="项目编号">{{ project.code }}</a-descriptions-item>
          <a-descriptions-item label="客户">{{ project.client_name || '-' }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ project.manager_name || '-' }}</a-descriptions-item>
          <a-descriptions-item label="项目优先级"><a-tag :color="priorityColor(project.priority)">{{ priorityLabel(project.priority) }}</a-tag></a-descriptions-item>
          <a-descriptions-item label="项目预计工时">{{ project.estimated_hours != null ? `${project.estimated_hours} 小时` : '-' }}</a-descriptions-item>
          <a-descriptions-item label="开始日期">{{ project.start_date ? dayjs(project.start_date).format('YYYY-MM-DD') : '-' }}</a-descriptions-item>
          <a-descriptions-item label="结题日期">{{ project.end_date ? dayjs(project.end_date).format('YYYY-MM-DD') : '-' }}</a-descriptions-item>
        </a-descriptions>
      </div>
      <a-alert
        v-if="hasLocalDrafts"
        type="info"
        showIcon
        message="当前有未保存的计划草稿"
        description="新增任务和模板计划目前只保存在本页面。点击“仅保存计划”可持久化内容且不触发排程。"
        style="margin-bottom: 16px"
      />
      <a-alert
        v-else-if="hasPendingPlanChanges"
        type="warning"
        showIcon
        message="计划已修改，待重新排程"
        description="计划内容已经保存，当前甘特图仍保留原排程；只有点击“保存并排程”后才会应用新计划。"
        style="margin-bottom: 16px"
      />
      <div class="action-bar">
        <a-button v-operation="'create_task'" type="primary" @click="openAddTask(null)"><PlusOutlined /> 添加顶级任务</a-button>
        <a-button v-operation="'import_template'" @click="openTemplateImport"><ImportOutlined /> 模板计划导入</a-button>
        <a-button v-operation="'approval_gate'" @click="openApprovalGate"><FileTextOutlined /> 添加方案签批</a-button>
        <span style="margin-left: 8px; font-size: 12px; color: #94a3b8">点击左侧 &gt; 展开/收起子任务</span>
        <span style="margin-left: auto; font-size: 12px; color: #94a3b8">{{ flatTaskCount }} 个任务（{{ leafTaskCount }} 个叶子任务）</span>
      </div>
      <a-table v-model:expandedRowKeys="expandedTaskIds" :dataSource="treeTasks" rowKey="id" size="small" :pagination="{ pageSize: 50, showSizeChanger: true }"
        :indentSize="24" :customRow="taskRowProps">
        <a-table-column title="任务名称" dataIndex="name" key="name">
          <template #default="{ record }">
            <HolderOutlined v-if="canOperate" class="task-drag-handle" title="拖动调整同级任务顺序" />
            <span :style="{ fontWeight: record.children?.length ? 600 : 400 }">{{ record.name }}</span>
              <a-tag v-if="record.is_local_draft" color="blue" style="margin-left: 8px">未保存</a-tag>
              <a-tag v-else-if="record.schedule_dirty" color="orange" style="margin-left: 8px">待重新排程</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="类型" key="task_type" width="110" :responsive="['lg']">
          <template #default="{ record }">
            <a-tag v-if="record.is_external_gate" color="default" style="font-size: 11px">方案签批</a-tag>
            <a-tag v-else-if="!record.children?.length" :color="getTaskTypeColor(record.task_type)" style="font-size: 11px">{{ getTaskTypeName(record.task_type) }}</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="状态/签批时间" key="plan_status" width="175">
          <template #default="{ record }">
            <template v-if="record.is_external_gate">
              <a-tag :color="gateStatusMeta(record.gate_status).color">{{ gateStatusMeta(record.gate_status).label }}</a-tag>
              <div style="margin-top: 3px; color: #64748b; font-size: 11px">{{ gateDateText(record) }}</div>
            </template>
            <span v-else>{{ taskStatusLabel(record.status) }}</span>
          </template>
        </a-table-column>
        <a-table-column title="对应仪器" key="instruments" width="160" :responsive="['xl']">
          <template #default="{ record }">
            <div v-if="getTaskInstrumentIds(record).length" class="instrument-tag-list">
              <a-tooltip
                v-for="instId in getTaskInstrumentIds(record).slice(0, 2)"
                :key="instId"
                :title="getInstrumentCode(instId)"
              >
                <a-tag color="blue" class="instrument-tag">{{ getInstrumentCode(instId) }}</a-tag>
              </a-tooltip>
              <a-tooltip v-if="getTaskInstrumentIds(record).length > 2" :title="getInstrumentSummary(record)">
                <a-tag class="instrument-tag">+{{ getTaskInstrumentIds(record).length - 2 }}</a-tag>
              </a-tooltip>
            </div>
            <span v-else-if="!record.children?.length && record.requires_instrument" style="color: #dc2626">未指定</span>
            <span v-else style="color: #ccc">-</span>
          </template>
        </a-table-column>
        <a-table-column title="负责人" key="assignee" width="100">
          <template #default="{ record }">{{ !record.children?.length ? (record.assignee_name || getAssigneeName(record.assignee_id) || '-') : '' }}</template>
        </a-table-column>
        <a-table-column title="计划耗时(h)" key="dur" width="105" align="center">
          <template #default="{ record }">{{ !record.children?.length ? (record.est_duration_hours || '-') : sumChildrenHours(record).toFixed(1) }}</template>
        </a-table-column>
        <a-table-column title="实际耗时(h)" key="actual_hours" width="105" align="center">
          <template #default="{ record }">{{ taskActualHoursText(record) }}</template>
        </a-table-column>
        <a-table-column title="前置任务" key="predecessors" width="140" :responsive="['xl']">
          <template #default="{ record }">
            <span v-if="record.predecessor_ids?.length && !record.children?.length">
              <a-tag v-for="pid in record.predecessor_ids" :key="pid" color="blue" style="font-size: 10px; margin: 1px">{{ getTaskNameById(pid) }}</a-tag>
            </span>
            <span v-else style="color: #ccc">-</span>
          </template>
        </a-table-column>
        <a-table-column v-if="canOperate" title="操作" key="actions" width="180">
          <template #default="{ record }">
            <a-space v-if="record.is_external_gate && record.is_local_draft" :size="0">
              <a-button v-operation="'edit_task'" type="link" size="small" @click="openEditApprovalGate(record)">编辑</a-button>
              <span v-operation="'delete_task'">
                <a-popconfirm title="确定删除这个未保存的方案签批？" @confirm="handleDeleteTask(record.id)">
                  <a-button type="link" size="small" danger>删除</a-button>
                </a-popconfirm>
              </span>
            </a-space>
            <a-space v-else-if="record.is_external_gate" :size="0">
              <a-button v-operation="'edit_task'" type="link" size="small" @click="openEditApprovalGate(record)">编辑</a-button>
              <span v-operation="'delete_task'">
                <a-popconfirm :disabled="!canDeleteTask(record)" title="确定删除方案签批？删除后下游任务将恢复为待排程状态。" @confirm="handleDeleteTask(record.id)">
                  <a-tooltip :title="deleteDisabledReason(record)">
                    <a-button type="link" size="small" danger :disabled="!canDeleteTask(record)">删除</a-button>
                  </a-tooltip>
                </a-popconfirm>
              </span>
            </a-space>
            <a-space v-else :size="0">
              <a-button v-operation="'create_task'" type="link" size="small" @click="openAddTask(record.id)" title="添加子任务"><PlusOutlined /></a-button>
              <a-button v-operation="'edit_task'" type="link" size="small" @click="openEditTask(record)"><EditOutlined /></a-button>
              <span v-operation="'delete_task'">
                <a-popconfirm :disabled="!canDeleteTask(record)" title="确定删除该任务及其所有子任务？" @confirm="handleDeleteTask(record.id)">
                  <a-tooltip :title="deleteDisabledReason(record)">
                    <a-button type="link" size="small" danger :disabled="!canDeleteTask(record)">删除</a-button>
                  </a-tooltip>
                </a-popconfirm>
              </span>
            </a-space>
          </template>
        </a-table-column>
      </a-table>
      <div style="margin-top: 16px; text-align: right">
        <a-button v-if="hasLocalDrafts" v-operation="'save_draft'" size="large" :loading="savingPlan" @click="handleSavePlan" style="margin-right: 8px">
          <SaveOutlined /> 仅保存计划
        </a-button>
        <a-button v-operation="'schedule'" type="primary" size="large" @click="handleStartSchedule" :loading="scheduling">
          <PlayCircleOutlined /> 保存并排程
        </a-button>
      </div>
    </template>
    <a-modal :title="editingTask ? '编辑任务' : '添加任务'" v-model:open="taskOpen" @ok="handleTaskSubmit" width="500" :okText="editingTask ? '保存' : '添加'">
      <a-form layout="vertical" :labelCol="{ style: { paddingBottom: 0 } }">
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="父任务"><a-select v-model:value="tf.parent_id" :options="parentTaskOptions" placeholder="顶级任务" allowClear :disabled="!!parentTaskId || !canEditScheduleFields" size="small" /></a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="任务名称" required><a-input v-model:value="tf.name" placeholder="输入名称" size="small" /></a-form-item>
          </a-col>
        </a-row>
        <a-row v-if="!isEditingParent" :gutter="12">
          <a-col :span="6">
            <a-form-item label="任务类型" required><a-select v-model:value="tf.task_type" :options="taskTypeOptions" placeholder="选择" :disabled="!canEditScheduleFields" size="small" /></a-form-item>
          </a-col>
          <a-col :span="6">
            <a-form-item label="负责人" required><a-select v-model:value="tf.assignee_id" :options="userOptions" placeholder="选择" allowClear size="small" /></a-form-item>
          </a-col>
          <a-col :span="6">
            <a-form-item label="耗时(h)" required><a-input-number v-model:value="tf.est_duration_hours" :min="0.5" :step="0.5" :max="999" :disabled="!canEditScheduleFields" size="small" style="width: 100%" /></a-form-item>
          </a-col>
          <a-col :span="6">
            <a-form-item label="切换(h)"><a-input-number v-model:value="tf.switchover_hours" :min="0" :step="0.5" :max="99" :disabled="!canEditScheduleFields" size="small" style="width: 100%" /></a-form-item>
          </a-col>
        </a-row>
        <a-row v-if="!isEditingParent" :gutter="12">
          <a-col :span="12">
            <a-form-item label="前置任务"><a-select v-model:value="tf.predecessor_ids" mode="multiple" :options="leafTaskOptions" placeholder="可多选" allowClear :disabled="!canEditScheduleFields" size="small" /></a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="指定仪器" :required="isInstrumentRequired">
              <a-select v-model:value="tf.instrument_id" :options="instrumentOptions" :placeholder="isInstrumentRequired ? '必填：请选择仪器' : '选择仪器'" allowClear :disabled="!canEditScheduleFields" size="small" style="width: 100%" />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form>
    </a-modal>
    <PlanInsertPreviewModal
      :open="insertPreviewOpen"
      :confirming="confirmingInsert"
      :result="insertPreview"
      @confirm="handleConfirmInsert"
      @cancel="handleCancelInsert"
    />
    <ApprovalGateModal
      :open="approvalGateOpen"
      :tasks="allTasks"
      :userOptions="userOptions"
      :defaultAssigneeId="project?.manager_id || null"
      :submitting="approvalGateSubmitting"
      @submit="handleCreateApprovalGate"
      @cancel="approvalGateOpen = false"
    />
    <ApprovalGateEditModal
      :open="approvalGateEditOpen"
      :gate="editingApprovalGate"
      :userOptions="userOptions"
      :defaultAssigneeId="project?.manager_id || null"
      :submitting="approvalGateEditSubmitting"
      @submit="handleApprovalGateEditSubmit"
      @cancel="closeEditApprovalGate"
    />
  </div>
</template>
<script setup lang="ts">
import { ref, computed, reactive, onMounted, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { isAxiosError } from 'axios'
import { PlusOutlined, EditOutlined, LeftOutlined, PlayCircleOutlined, FileTextOutlined, ImportOutlined, HolderOutlined, SaveOutlined } from '@ant-design/icons-vue'
import { commitProjectPlanDrafts, saveAndScheduleProjectPlan, createApprovalGate, reorderProjectTasks, getProject, getProjectDAG, updateTask, deleteTask, getUserDirectory, getTaskTypes, getInstruments, applyProjectPlan, confirmProjectPlanInsert, type ApprovalGateCreatePayload, type Project, type Task, type DAGData, type TaskTypeConfig } from '@/services/api'
import type { ProjectPlanApplyResult } from '@/types'
import PlanInsertPreviewModal from './components/PlanInsertPreviewModal.vue'
import ApprovalGateModal from './components/ApprovalGateModal.vue'
import ApprovalGateEditModal from './components/ApprovalGateEditModal.vue'
import ScheduleFailureModal from './components/ScheduleFailureModal.vue'
import dayjs from 'dayjs'
import { canOperatePage, permissionState } from '@/services/permissions'
import { taskStatusLabel } from '@/utils/statusMeta'
import {
  buildTaskTree, countLeafTasks, gateDateText, gateStatusMeta, getTaskTypeColor,
  localDraftDependsOnTask, parentTaskIds, priorityColor, priorityLabel, sumTaskHours, taskActualHoursText, taskTreeIds,
  taskInstrumentIds, taskTreeHasCompletedTask,
} from './planBreakdownUtils'
import { buildStandardPlanDrafts } from './standardPlanDrafts'
import { buildLocalTask, type LocalTaskPayload } from './planLocalTaskFactory'
import { persistCommittedDraftOrders, siblingOrderGroups, toDraftPayload } from './planDraftPersistence'
import { useApprovalGateEditor } from './useApprovalGateEditor'
import './planBreakdown.css'
const router = useRouter()
const route = useRoute()
const canOperate = computed(() => {
  permissionState.permissions
  return canOperatePage('/projects/plan-breakdown')
})
const isSystemAdministrator = computed(() => {
  try {
    const user = JSON.parse(localStorage.getItem('user') || '{}') as { role?: string; roles?: string[] }
    return user.role === '系统管理员' || user.roles?.includes('系统管理员') === true
  } catch {
    return false
  }
})
const projectId = Number(route.query.id)
const project = ref<Project | null>(null)
const dagData = ref<DAGData | null>(null)
const allTasks = ref<Task[]>([])
const expandedTaskIds = ref<number[]>([])
const loading = ref(true)
const taskOpen = ref(false)
const editingTask = ref<Task | null>(null)
const insertPreview = ref<ProjectPlanApplyResult | null>(null)
const insertPreviewOpen = ref(false)
const confirmingInsert = ref(false)
const approvalGateOpen = ref(false)
const approvalGateSubmitting = ref(false)
const draggingTaskId = ref<number | null>(null)
const parentTaskId = ref<number | null>(null)
let nextDraftId = -1
const taskTypeOptions = ref<{ label: string; value: string; resource_type: string }[]>([])
const taskTypeMap = ref<Record<string, TaskTypeConfig>>({})
const REQUIRED_INSTRUMENT_TASK_TYPES = new Set(['FFKF_001', 'FFYZ_001'])
const userOptions = ref<{ label: string; value: number }[]>([])
const instrumentOptions = ref<{ label: string; value: number }[]>([])
const instrumentCodeMap = computed(() => {
  const map: Record<number, string> = {}
  instrumentOptions.value.forEach(instrument => { map[instrument.value] = instrument.label })
  return map
})
const tf = reactive({ name: '', task_type: '', est_duration_hours: 8, switchover_hours: 0.5, predecessor_ids: [] as number[], instrument_id: null as number | null, assignee_id: null as number | null, parent_id: null as number | null })
const statusLabels: Record<string, string> = { active: '进行中', completed: '已完成', pending: '待启动', suspended: '已暂停', cancelled: '已取消', draft: '草稿' }
function goBack() { router.push('/projects/ledger') }
function getTaskTypeName(code: string) {
  if (code === 'approval_gate') return '方案签批'
  return taskTypeMap.value[code]?.name || code
}
function canDeleteTask(task: Task) { return isSystemAdministrator.value || !taskTreeHasCompletedTask(task) }
function deleteDisabledReason(task: Task) { return canDeleteTask(task) ? '' : '已完成任务不允许删除' }
function getTaskNameById(id: number) {
  const t = allTasks.value.find(t => t.id === id)
  return t ? (t.name.length > 8 ? t.name.slice(0, 8) + '...' : t.name) : '#' + id
}
function getAssigneeName(id: number | null | undefined) {
  if (!id) return null
  return userOptions.value.find(u => u.value === id)?.label || null
}
function getInstrumentCode(id: number) {
  return instrumentCodeMap.value[id] || `ID ${id}`
}
function getTaskInstrumentIds(task: Task): number[] {
  return taskInstrumentIds(task)
}
function getInstrumentSummary(task: Task) {
  return getTaskInstrumentIds(task).map(getInstrumentCode).join('、')
}
const treeTasks = computed(() => buildTaskTree(allTasks.value))
const flatTaskCount = computed(() => allTasks.value.length)
const leafTaskCount = computed(() => countLeafTasks(treeTasks.value))
function sumChildrenHours(task: Task): number {
  return sumTaskHours(task)
}
function isParentTask(id: number): boolean { return allTasks.value.some(t => t.parent_id === id) }
function getParentTaskIds(tasks: Task[]): number[] {
  return parentTaskIds(tasks)
}
function expandTask(taskId: number | null) {
  if (taskId != null && !expandedTaskIds.value.includes(taskId)) {
    expandedTaskIds.value = [...expandedTaskIds.value, taskId]
  }
}
const isEditingParent = computed(() => editingTask.value ? isParentTask(editingTask.value.id) : false)
const canEditScheduleFields = computed(() => editingTask.value?.can_edit_schedule_fields !== false)
const isInstrumentRequired = computed(() => REQUIRED_INSTRUMENT_TASK_TYPES.has(tf.task_type))
const hasLocalDrafts = computed(() => allTasks.value.some(task => task.is_local_draft))
const { approvalGateEditOpen, approvalGateEditSubmitting, editingApprovalGate, openEditApprovalGate, closeEditApprovalGate, handleApprovalGateEditSubmit } = useApprovalGateEditor({
  allTasks, project, getAssigneeName, errorDetail,
})
const hasPendingPlanChanges = computed(() => allTasks.value.some(task =>
  !task.children?.length
  && (task.schedule_dirty || ['pending', 'ready'].includes(task.status)),
))
const parentTaskOptions = computed(() => allTasks.value.filter(t => !editingTask.value || t.id !== editingTask.value.id).map(t => ({ label: t.name, value: t.id })))
const leafTaskOptions = computed(() => allTasks.value.filter(t => !t.children || t.children.length === 0).map(t => ({ label: t.name, value: t.id })))
function siblingTasks(parentId: number | null) {
  return allTasks.value.filter(task => task.parent_id === parentId)
    .sort((left, right) => (left.plan_order ?? 0) - (right.plan_order ?? 0) || left.id - right.id)
}
function taskRowProps(record: Task) {
  return {
    draggable: canOperate.value,
    onDragstart: () => { draggingTaskId.value = record.id },
    onDragover: (event: DragEvent) => event.preventDefault(),
    onDrop: () => handleTaskDrop(record),
    onDragend: () => { draggingTaskId.value = null },
  }
}
async function handleTaskDrop(target: Task) {
  const source = allTasks.value.find(task => task.id === draggingTaskId.value)
  draggingTaskId.value = null
  if (!source || source.id === target.id) return
  if (source.parent_id !== target.parent_id) { message.warning('只能调整同一层级内的任务顺序'); return }
  const siblings = siblingTasks(source.parent_id)
  const sourceIndex = siblings.findIndex(task => task.id === source.id)
  const targetIndex = siblings.findIndex(task => task.id === target.id)
  siblings.splice(targetIndex, 0, siblings.splice(sourceIndex, 1)[0])
  siblings.forEach((task, index) => { task.plan_order = index })
  allTasks.value = [...allTasks.value]
  if (siblings.some(task => task.is_local_draft)) return
  try { await reorderProjectTasks(projectId, source.parent_id, siblings.map(task => task.id)) }
  catch (error: unknown) { message.error(errorDetail(error, '保存任务顺序失败')); await fetchProject() }
}
async function fetchProject() {
  loading.value = true
  try {
    const [p, d] = await Promise.all([getProject(projectId), getProjectDAG(projectId)])
    project.value = p; dagData.value = d; allTasks.value = p.tasks || []
    expandedTaskIds.value = getParentTaskIds(allTasks.value)
  } catch { message.error('加载项目失败') }
  finally { loading.value = false }
}
function openAddTask(parentId: number | null) {
  editingTask.value = null; parentTaskId.value = parentId
  Object.assign(tf, { name: '', task_type: taskTypeOptions.value[0]?.value || '', est_duration_hours: 8, switchover_hours: 0.5, predecessor_ids: [], instrument_id: null, assignee_id: null, parent_id: parentId })
  taskOpen.value = true
}
function openEditTask(t: Task) {
  editingTask.value = t; parentTaskId.value = null
  Object.assign(tf, { name: t.name, task_type: t.task_type, est_duration_hours: t.est_duration_hours || 8, switchover_hours: t.switchover_hours, predecessor_ids: t.predecessor_ids || [], instrument_id: t.instrument_ids?.[0] || null, assignee_id: t.assignee_id || null, parent_id: t.parent_id || null })
  taskOpen.value = true
}
async function handleTaskSubmit() {
  if (!project.value) return
  if (!tf.name) { message.error('请输入任务名称'); return }
  const isParent = isEditingParent.value
  if (!isParent) {
    if (!tf.task_type) { message.error('请选择任务类型'); return }
    if (!tf.assignee_id) { message.error('请选择负责人'); return }
    if (!tf.est_duration_hours || tf.est_duration_hours <= 0) { message.error('请输入预计耗时'); return }
    if (isInstrumentRequired.value && !tf.instrument_id) { message.error('方法开发和方法验证必须指定仪器'); return }
  }
  const payload = {
    name: tf.name, task_type: isParent ? 'group' : tf.task_type,
    requires_instrument: isParent ? false : (taskTypeMap.value[tf.task_type]?.resource_type || 'both') !== 'human',
    est_duration_hours: isParent ? null : tf.est_duration_hours, switchover_hours: isParent ? 0 : tf.switchover_hours,
    predecessor_ids: isParent ? [] : tf.predecessor_ids, assignee_id: isParent ? null : (tf.assignee_id || null),
    parent_id: tf.parent_id, instrument_ids: isParent || !tf.instrument_id ? [] : [tf.instrument_id],
  }
  try {
    if (editingTask.value?.is_local_draft) {
      const index = allTasks.value.findIndex(task => task.id === editingTask.value?.id)
      const planOrder = editingTask.value.parent_id === payload.parent_id
        ? editingTask.value.plan_order
        : undefined
      if (index >= 0) allTasks.value[index] = buildLocalTask(payload, {
        projectId, id: editingTask.value.id, planOrder: planOrder ?? siblingTasks(payload.parent_id).length,
        assigneeName: getAssigneeName(payload.assignee_id),
      })
      expandTask(payload.parent_id)
      message.success('草稿任务已更新')
    } else if (editingTask.value) {
      const updatedTask = await updateTask(editingTask.value.id, payload)
      const index = allTasks.value.findIndex(task => task.id === updatedTask.id)
      if (index >= 0) allTasks.value[index] = updatedTask
      message.success('任务更新成功')
    } else {
      allTasks.value.push(buildLocalTask(payload, {
        projectId, id: nextDraftId--, planOrder: siblingTasks(payload.parent_id).length,
        assigneeName: getAssigneeName(payload.assignee_id),
      }))
      expandTask(payload.parent_id)
      message.success('任务已加入本地草稿，保存前不会写入数据库')
    }
    taskOpen.value = false; editingTask.value = null
  } catch (error: unknown) { message.error(errorDetail(error, '操作失败')) }
}
async function handleDeleteTask(taskId: number) {
  const task = allTasks.value.find(item => item.id === taskId)
  if (task && !canDeleteTask(task)) { message.warning('已完成任务不允许删除'); return }
  if (task?.is_local_draft) {
    const removedIds = taskTreeIds(allTasks.value, taskId)
    allTasks.value = allTasks.value
      .filter(candidate => !removedIds.has(candidate.id))
      .map(candidate => ({
        ...candidate,
        predecessor_ids: candidate.predecessor_ids.filter(id => !removedIds.has(id)),
      }))
    message.success('未保存草稿已删除')
    return
  }
  if (localDraftDependsOnTask(allTasks.value, taskId)) {
    message.warning('该任务被未保存草稿作为父任务或前置任务引用，请先调整草稿关联')
    return
  }
  try {
    const removedIds = taskTreeIds(allTasks.value, taskId)
    await deleteTask(taskId)
    allTasks.value = allTasks.value
      .filter(candidate => !removedIds.has(candidate.id))
      .map(candidate => ({
        ...candidate,
        predecessor_ids: candidate.predecessor_ids.filter(id => !removedIds.has(id)),
      }))
    message.success(task?.is_external_gate ? '方案签批已删除' : '任务已删除')
  }
  catch (error: unknown) { message.error(errorDetail(error, '删除失败')) }
}
async function handleCreateApprovalGate(payload: ApprovalGateCreatePayload) {
  approvalGateSubmitting.value = true
  try {
    await createApprovalGate(projectId, payload)
    approvalGateOpen.value = false
    message.success('方案签批已添加，后续任务已转为等待签批')
    await fetchProject()
  } catch (error: unknown) { message.error(errorDetail(error, '添加方案签批失败')) }
  finally { approvalGateSubmitting.value = false }
}
function openApprovalGate() {
  if (hasLocalDrafts.value) {
    message.warning('请先保存当前新增草稿，再添加独立方案签批')
    return
  }
  approvalGateOpen.value = true
}
function openTemplateImport() {
  if (!project.value) return
  if (!project.value?.estimated_hours) { message.error('请先填写项目预计工时'); return }
  if (!project.value.manager_id) { message.error('请先设置项目负责人'); return }
  const groupNumber = allTasks.value.filter(task => task.parent_id == null && task.task_type === 'group').length + 1
  const draft = buildStandardPlanDrafts({
    projectId, estimatedHours: project.value.estimated_hours, managerId: project.value.manager_id,
    groupNumber, nextDraftId, getAssigneeName,
  })
  nextDraftId = draft.nextDraftId
  allTasks.value.push(...draft.tasks)
  expandTask(draft.group.id)
  message.success(`已追加“${draft.group.name}”及其 5 个子任务，点击保存前不会写入数据库`)
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
const scheduling = ref(false)
const savingPlan = ref(false)
async function saveLocalDrafts() {
  const drafts = allTasks.value.filter(task => task.is_local_draft)
  if (!drafts.length) return null
  const pendingOrders = siblingOrderGroups(allTasks.value, siblingTasks)
  const result = await commitProjectPlanDrafts(projectId, drafts.map(task => toDraftPayload(task, isParentTask)))
  await persistCommittedDraftOrders(projectId, pendingOrders, result.id_map, reorderProjectTasks)
  return result
}
async function handleSavePlan() {
  savingPlan.value = true
  try {
    const result = await saveLocalDrafts()
    if (!result) { message.info('当前没有需要保存的计划草稿'); return }
    message.success(`${result.message}，尚未执行排程`)
    await fetchProject()
  } catch (error: unknown) {
    message.error(errorDetail(error, '保存计划失败'))
  } finally {
    savingPlan.value = false
  }
}
async function handleStartSchedule() {
  const missingInstrumentTasks = allTasks.value.filter(task => (
    !isParentTask(task.id)
    && !task.is_external_gate
    && REQUIRED_INSTRUMENT_TASK_TYPES.has(task.task_type)
    && !task.instrument_ids.length
  ))
  if (missingInstrumentTasks.length) {
    message.error(`请先为任务【${missingInstrumentTasks.map(task => task.name).join('、')}】指定仪器`)
    return
  }
  scheduling.value = true
  try {
    const drafts = allTasks.value.filter(task => task.is_local_draft)
    const result = await saveAndScheduleProjectPlan(projectId, drafts.map(task => toDraftPayload(task, isParentTask)))
    if (result.status === 'applied') {
      message.success(result.message || '排程完成')
      await fetchProject()
    } else if (result.status === 'no_changes') {
      message.info(result.message || '当前没有需要重新排程的任务')
      await fetchProject()
    } else if (result.status === 'insert_confirmation_required') {
      insertPreview.value = result
      insertPreviewOpen.value = true
    } else {
      Modal.error({
        title: '排程失败',
        width: 900,
        wrapClassName: 'schedule-failure-modal',
        content: h(ScheduleFailureModal, { projectId, result }),
      })
    }
  } catch (error: unknown) {
    const responseData = isAxiosError<ProjectPlanApplyResult & { detail?: string }>(error)
      ? error.response?.data
      : undefined
    if (responseData?.schedule_failure) {
      Modal.error({
        title: '排程失败',
        width: 900,
        wrapClassName: 'schedule-failure-modal',
        content: h(ScheduleFailureModal, { projectId, result: responseData }),
      })
    } else {
      Modal.error({ title: '排程请求失败', content: errorDetail(error, '服务器内部错误，请稍后重试。') })
    }
  } finally { scheduling.value = false }
}
async function handleConfirmInsert() {
  const previewToken = insertPreview.value?.preview_token
  if (!previewToken) return
  confirmingInsert.value = true
  try {
    const result = await confirmProjectPlanInsert(projectId, previewToken)
    message.success(result.message || '插单排程完成')
    insertPreviewOpen.value = false
    insertPreview.value = null
    await fetchProject()
  } catch (error: unknown) {
    message.error(errorDetail(error, '插单确认失败，请重新计算影响'))
  } finally { confirmingInsert.value = false }
}
function handleCancelInsert() {
  insertPreviewOpen.value = false
  insertPreview.value = null
}
function errorDetail(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error)) return error.response?.data?.detail || fallback
  return fallback
}
onMounted(async () => {
  if (!projectId) { message.error('缺少项目ID'); router.push('/projects/ledger'); return }
  await loadTaskTypes(); await Promise.all([fetchProject(), loadUsers(), loadInstruments()])
})
</script>
