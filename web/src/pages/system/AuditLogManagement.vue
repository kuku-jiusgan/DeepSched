<template>
  <div class="audit-page">
    <header class="page-header">
      <div><h2>操作日志</h2><p>查询系统写操作的操作人、对象、执行结果与时间。</p></div>
    </header>
    <section class="filter-bar">
      <a-input v-model:value="filters.keyword" allow-clear placeholder="搜索操作人或操作类型" @press-enter="loadLogs" />
      <a-select v-model:value="filters.action" allow-clear placeholder="全部操作" :options="actionOptions" />
      <a-button type="primary" @click="loadLogs">查询</a-button>
      <a-button :loading="loading" @click="loadLogs">刷新</a-button>
    </section>
    <a-table :data-source="logs" :loading="loading" row-key="id" :scroll="{ x: 900 }" :pagination="{ pageSize: 20, showSizeChanger: true, showTotal: (total: number) => `共 ${total} 条` }">
      <a-table-column title="时间" key="created_at" width="170"><template #default="{ record }">{{ formatTime(record.created_at) }}</template></a-table-column>
      <a-table-column title="操作人" key="user_name" width="120"><template #default="{ record }">{{ operatorLabel(record.user_name) }}</template></a-table-column>
      <a-table-column title="操作" key="action" width="170"><template #default="{ record }"><a-tag :color="actionColor(record.action)">{{ actionLabel(record) }}</a-tag></template></a-table-column>
      <a-table-column title="对象" key="target" width="150"><template #default="{ record }">{{ targetText(record) }}</template></a-table-column>
      <a-table-column title="详情" key="detail"><template #default="{ record }"><span class="detail-text">{{ detailText(record.detail) }}</span></template></a-table-column>
    </a-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { message } from 'ant-design-vue'
import { getAuditLogs, type AuditLogRecord } from '@/services/api'

const logs = ref<AuditLogRecord[]>([])
const loading = ref(false)
const filters = reactive({ keyword: '', action: undefined as string | undefined })
const actionOptions = [
  { value: 'user_logged_in', label: '用户登录' },
  { value: 'project_created', label: '新增项目' },
  { value: 'project_updated', label: '修改项目' },
  { value: 'user_created', label: '新增用户' },
  { value: 'user_updated', label: '修改用户' },
  { value: 'user_deleted', label: '删除用户' },
  { value: 'user_password_reset', label: '重置用户密码' },
  { value: 'project_deleted', label: '删除项目' },
  { value: 'schedule_generated', label: '生成排程' },
  { value: 'schedule_rescheduled', label: '重新排程' },
  { value: 'HTTP POST', label: '提交操作' },
  { value: 'HTTP PUT', label: '修改操作' },
  { value: 'HTTP DELETE', label: '删除操作' },
]
const labels: Record<string, string> = {
  schedule_insert_confirmed: '确认插单',
  user_logged_in: '用户登录',
  project_created: '新增项目', project_updated: '修改项目',
  user_created: '新增用户', user_updated: '修改用户', user_deleted: '删除用户', user_password_reset: '重置用户密码',
  project_deleted: '删除项目', schedule_generated: '生成排程', schedule_rescheduled: '重新排程',
  task_delay_reported: '提交任务延期', standard_project_plan_imported: '导入标准计划',
  project_plan_drafts_committed: '保存项目计划', approval_gate_submitted: '提交方案',
  approval_gate_approved: '确认方案', approval_schedule_impact_confirmed: '确认排程影响',
}
const targetLabels: Record<string, string> = { project: '项目', schedule: '排程', task: '任务', time_slot: '任务排程时间段', approval_gate: '方案签批', user: '系统用户', api_request: '系统操作' }
const pathLabels: Array<[string, string]> = [
  ['/api/v1/users/keep-alive', '保持登录状态'], ['/api/v1/users/logout', '退出登录'],
  ['/api/v1/users/login', '账号密码登录'], ['/api/v1/wecom-auth/login', '企业微信登录'],
  ['/api/v1/users/me/password', '修改本人密码'], ['/api/v1/projects/tasks/', '项目任务操作'],
  ['/plan-drafts/commit', '保存项目计划草稿'], ['/tasks/reorder', '调整任务顺序'],
  ['/api/v1/schedules/apply-project-plan', '保存并排程'], ['/api/v1/schedules/reschedule', '重新排程'],
  ['/api/v1/schedules/generate', '生成排程'], ['/api/v1/schedules/timeslots/', '任务执行操作'],
  ['/api/v1/projects/', '项目管理操作'], ['/api/v1/detection-tasks', '检测任务管理操作'],
  ['/api/v1/approval-gates', '方案签批操作'], ['/api/v1/instruments', '仪器管理操作'],
  ['/api/v1/task-types', '标准任务类型操作'], ['/api/v1/role-permissions', '角色权限设置'],
  ['/api/v1/alert-rules', '预警规则设置'], ['/api/v1/calendar', '工作日历设置'],
  ['/api/v1/schedule-rules', '排程规则设置'], ['/api/v1/notifications', '通知操作'],
  ['/api/v1/users', '用户管理操作'],
]
const detailLabels: Record<string, string> = {
  project_code: '项目编号', project_name: '项目名称', task_count: '关联任务数', project_ids: '项目范围', task_id: '任务编号', task_display: '任务',
  mode: '排程模式', result: '执行结果', path: '接口', status: '状态', success: '执行结果',
  duration_ms: '耗时', created: '新增任务数', client_ids: '任务标识', expected_approval_at: '预计签批时间',
  schedule_run_id: '排程批次', delay_hours: '延期时长（小时）', reason: '延期原因', shifted_slots: '受影响排程数',
  insert_summary: '插单说明',
  task_ids: '插单任务', anchor_task_id: '插入位置任务', moved_tasks: '移动任务数',
  username: '登录账号', display_name: '姓名', roles: '角色', email: '邮箱', phone: '手机号',
  wecom_id: '企业微信号', is_active: '账号状态', login_method: '登录方式',
}

async function loadLogs() {
  loading.value = true
  try { logs.value = await getAuditLogs({ keyword: filters.keyword || undefined, action: filters.action }) }
  catch { message.error('加载操作日志失败') }
  finally { loading.value = false }
}
function formatTime(value: string) { return dayjs(value).format('YYYY-MM-DD HH:mm:ss') }
function operatorLabel(value: string) { return value === 'system' ? '系统自动任务' : value === 'anonymous' ? '未登录用户' : value }
function actionLabel(record: AuditLogRecord) {
  if (labels[record.action]) return labels[record.action]
  const path = String(record.detail.path || '')
  const matched = matchedPathLabel(path)
  if (matched) return matched
  return labels[record.action] || httpMethodLabel(record.action)
}
function httpMethodLabel(action: string) {
  return ({ 'HTTP POST': '新增/提交', 'HTTP PUT': '修改', 'HTTP PATCH': '修改', 'HTTP DELETE': '删除' } as Record<string, string>)[action] || action
}
function actionColor(value: string) { return value.includes('DELETE') || value === 'project_deleted' ? 'red' : value.includes('schedule') ? 'blue' : 'default' }
function targetText(record: AuditLogRecord) {
  if (record.detail.target_display) return String(record.detail.target_display)
  const path = String(record.detail.path || '')
  if (path) return pathTargetText(path)
  const target = targetLabels[record.target_type] || record.target_type
  return record.target_id ? `${target} #${record.target_id}` : target
}
function detailText(detail: Record<string, unknown>) {
  if (Array.isArray(detail.task_details)) return formatPlanTasks(detail)
  if (Array.isArray(detail.changes)) return formatUserChanges(detail.changes)
  if (detail.project_fields && typeof detail.project_fields === 'object') return formatProjectDetail(detail.project_fields as Record<string, unknown>)
  if (detail.username && detail.display_name) return formatUserDetail(detail)
  if (detail.project_code || detail.project_name) return `项目：${detail.project_code || ''} ${detail.project_name || ''}`.trim()
  if (detail.path) return `${pathLabel(String(detail.path))} · ${detail.success ? '成功' : '失败'}`
  return Object.entries(detail)
    .filter(([key]) => key !== 'client_ip' && key !== 'target_display' && !(key === 'task_id' && detail.task_display))
    .map(([key, value]) => `${detailLabels[key] || key}: ${formatDetailValue(value)}`)
    .join(' · ') || '-'
}
function formatPlanTasks(detail: Record<string, unknown>) {
  const tasks = detail.task_details as Array<Record<string, unknown>>
  const lines = tasks.map((task, index) => {
    const parts = [
      `${index + 1}. ${task.name}`,
      `类型：${task.task_type}`,
      `工时：${task.estimated_hours === null || task.estimated_hours === undefined ? '无需排程' : `${task.estimated_hours} 小时`}`,
      `负责人：${task.assignee || '未指定负责人'}`,
      `仪器：${formatTaskList(task.instruments, '无需仪器')}`,
      `前置任务：${formatTaskList(task.predecessors, '无')}`,
    ]
    if (task.parent) parts.push(`所属任务：${task.parent}`)
    return parts.join(' ｜ ')
  })
  return [`新增 ${detail.created || tasks.length} 个任务：`, ...lines].join('\n')
}
function formatTaskList(value: unknown, emptyText: string) {
  return Array.isArray(value) && value.length ? value.join('、') : emptyText
}
function formatProjectDetail(fields: Record<string, unknown>) {
  const projectLabels: Record<string, string> = {
    code: '项目编号', name: '项目名称', client_name: '客户名称', estimated_hours: '预计总工时',
    priority: '优先级', manager_name: '项目负责人', start_date: '项目开始时间', end_date: '项目结束时间',
  }
  return Object.entries(fields)
    .map(([key, value]) => `${projectLabels[key] || key}：${formatUserValue(value)}`)
    .join(' · ')
}
function formatUserChanges(changes: unknown[]) {
  if (!changes.length) return '用户信息未发生变化'
  return changes.map(change => {
    if (!change || typeof change !== 'object') return String(change)
    const item = change as Record<string, unknown>
    return `${item.field}：${formatUserValue(item.before)} → ${formatUserValue(item.after)}`
  }).join(' · ')
}
function formatUserDetail(detail: Record<string, unknown>) {
  return ['username', 'display_name', 'login_method', 'roles', 'email', 'phone', 'wecom_id', 'is_active']
    .filter(key => detail[key] !== null && detail[key] !== undefined && detail[key] !== '')
    .map(key => `${detailLabels[key]}：${formatUserValue(detail[key])}`)
    .join(' · ')
}
function formatUserValue(value: unknown) {
  if (value === true) return '启用'
  if (value === false) return '停用'
  if (value === null || value === undefined || value === '') return '未填写'
  return formatDetailValue(value)
}
function pathLabel(path: string) { return matchedPathLabel(path) || '系统接口操作' }
function matchedPathLabel(path: string) {
  if (path === '/api/v1/projects') return '新增项目'
  if (path.includes('/plan-drafts/commit')) return '保存项目计划草稿'
  if (path.endsWith('/tasks/reorder')) return '调整任务顺序'
  return pathLabels.find(([prefix]) => path.startsWith(prefix))?.[1]
}
function pathTargetText(path: string) {
  if (path.includes('/users/keep-alive') || path.includes('/users/login') || path.includes('/users/logout') || path.includes('/wecom-auth/login')) return '登录会话'
  if (path.includes('/plan-drafts/') || path.includes('/tasks/reorder')) return '项目计划'
  if (path.includes('/timeslots/')) return '任务排程时间段'
  if (path.includes('/users')) return '系统用户'
  if (path.includes('/notifications')) return '站内通知'
  if (path.includes('/alert-rules')) return '预警规则'
  if (path.includes('/role-permissions')) return '角色权限'
  return pathLabel(path).replace('操作', '')
}
function formatDetailValue(value: unknown) { return Array.isArray(value) ? value.join('、') : String(value) }
onMounted(loadLogs)
</script>

<style scoped>
.audit-page { min-width: 0; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.page-header h2 { margin: 0; font-size: 20px; color: var(--color-text-primary); }
.page-header p { margin: 5px 0 0; color: var(--color-text-secondary); font-size: 13px; }
.filter-bar { display: flex; gap: 12px; margin-bottom: 16px; }
.filter-bar .ant-input { width: 260px; }.filter-bar .ant-select { width: 170px; }.detail-text { color: var(--color-text-secondary); white-space: pre-line; line-height: 1.75; }
@media (max-width: 720px) { .filter-bar { flex-wrap: wrap; }.filter-bar .ant-input, .filter-bar .ant-select { width: 100%; } }
</style>
