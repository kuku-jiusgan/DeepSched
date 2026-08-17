<template>
  <div class="audit-page">
    <header class="page-header">
      <div><h2>操作日志</h2><p>查询系统写操作的操作人、对象、执行结果与时间。</p></div>
    </header>
    <section class="filter-bar">
      <a-input v-model:value="filters.keyword" allow-clear placeholder="搜索操作人或操作类型" @press-enter="loadLogs" />
      <a-select v-model:value="filters.category" allow-clear placeholder="全部分类" :options="categoryOptions" />
      <a-select v-model:value="filters.action" allow-clear placeholder="全部操作" :options="actionOptions" />
      <a-button type="primary" @click="loadLogs">查询</a-button>
      <a-button :loading="loading" @click="loadLogs">刷新</a-button>
      <a-button class="export-button" :loading="exporting" @click="exportExcel">
        <template #icon><DownloadOutlined /></template>
        导出 Excel
      </a-button>
    </section>
    <a-table v-model:expandedRowKeys="expandedRowKeys" :data-source="logs" :loading="loading" row-key="id" :scroll="{ x: 980 }" :pagination="{ pageSize: 20, showSizeChanger: true, showTotal: (total: number) => `共 ${total} 条` }">
      <a-table-column title="时间" key="created_at" width="170"><template #default="{ record }">{{ formatTime(record.created_at) }}</template></a-table-column>
      <a-table-column title="操作人" key="user_name" width="120"><template #default="{ record }">{{ operatorLabel(record.user_name) }}</template></a-table-column>
      <a-table-column title="分类" key="category" width="130"><template #default="{ record }"><a-tag>{{ record.category_label }}</a-tag></template></a-table-column>
      <a-table-column title="操作摘要" key="summary"><template #default="{ record }"><span class="summary-text">{{ record.summary }}</span></template></a-table-column>
      <a-table-column title="结果" key="result" width="90"><template #default="{ record }"><a-tag :color="record.result === 'success' ? 'green' : 'red'">{{ record.result === 'success' ? '成功' : '失败' }}</a-tag></template></a-table-column>
      <a-table-column title="详情" key="expand" width="80"><template #default="{ record }"><a-button type="link" size="small" @click="toggleDetail(record.id)">{{ expandedRowKeys.includes(record.id) ? '收起' : '查看' }}</a-button></template></a-table-column>
      <template #expandedRowRender="{ record }">
        <div class="audit-detail">
          <div class="detail-heading"><strong>{{ record.action_label }}</strong><span>{{ record.target_display }}</span></div>
          <dl v-if="record.changes.length" class="change-list">
            <template v-for="change in record.changes" :key="change.field">
              <dt>{{ change.field }}</dt><dd>{{ formatValue(change.before) }} <span class="change-arrow">→</span> {{ formatValue(change.after) }}</dd>
            </template>
          </dl>
          <dl v-if="hasValues(record.business_detail)" class="context-list">
            <template v-for="(value, key) in record.business_detail" :key="key">
              <dt>{{ detailLabel(String(key)) }}</dt><dd>{{ formatValue(value) }}</dd>
            </template>
          </dl>
          <a-collapse v-if="hasValues(record.technical_detail)" ghost class="technical-collapse">
            <a-collapse-panel key="technical" header="技术信息">
              <span v-for="(value, key) in record.technical_detail" :key="key" class="technical-item">{{ key }}：{{ formatValue(value) }}</span>
            </a-collapse-panel>
          </a-collapse>
        </div>
      </template>
      <template #emptyText><a-empty description="当前筛选条件下暂无操作日志" /></template>
    </a-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { message } from 'ant-design-vue'
import { DownloadOutlined } from '@ant-design/icons-vue'
import { exportAuditLogs, getAuditLogCategories, getAuditLogs, type AuditLogRecord, type AuditLogCategoryOption } from '@/services/api'

const logs = ref<AuditLogRecord[]>([])
const loading = ref(false)
const exporting = ref(false)
const expandedRowKeys = ref<number[]>([])
const categoryOptions = ref<AuditLogCategoryOption[]>([])
const filters = reactive({ keyword: '', category: undefined as string | undefined, action: undefined as string | undefined })
const actionOptions = [
  { value: 'user_logged_in', label: '用户登录' },
  { value: 'project_created', label: '新增项目' },
  { value: 'project_updated', label: '修改项目' },
  { value: 'user_created', label: '新增用户' },
  { value: 'user_updated', label: '修改用户' },
  { value: 'user_deleted', label: '删除用户' },
  { value: 'user_password_reset', label: '重置用户密码' },
  { value: 'project_deleted', label: '删除项目' },
  { value: 'task_created', label: '新增任务' },
  { value: 'task_updated', label: '修改任务' },
  { value: 'task_deleted', label: '删除任务' },
  { value: 'task_reordered', label: '调整任务顺序' },
  { value: 'schedule_generated', label: '生成排程' },
  { value: 'schedule_rescheduled', label: '重新排程' },
  { value: 'HTTP POST', label: '提交操作' },
  { value: 'HTTP PUT', label: '修改操作' },
  { value: 'HTTP DELETE', label: '删除操作' },
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
  try {
    logs.value = await getAuditLogs({ keyword: filters.keyword || undefined, category: filters.category, action: filters.action })
    expandedRowKeys.value = []
  }
  catch { message.error('加载操作日志失败') }
  finally { loading.value = false }
}
async function exportExcel() {
  exporting.value = true
  try {
    const blob = await exportAuditLogs({ keyword: filters.keyword || undefined, category: filters.category, action: filters.action })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `操作日志-${dayjs().format('YYYY-MM-DD')}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
    message.success('操作日志已导出')
  } catch {
    message.error('操作日志导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}
function toggleDetail(id: number) {
  expandedRowKeys.value = expandedRowKeys.value.includes(id)
    ? expandedRowKeys.value.filter(item => item !== id)
    : [...expandedRowKeys.value, id]
}
function hasValues(value: Record<string, unknown>) { return Object.keys(value).length > 0 }
function detailLabel(key: string) { return detailLabels[key] || key }
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未设置'
  if (value === true) return '是'
  if (value === false) return '否'
  if (Array.isArray(value)) return value.length ? value.join('、') : '无'
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${detailLabel(key)}：${formatValue(item)}`).join('；')
  return String(value)
}
function formatTime(value: string) { return dayjs(value).format('YYYY-MM-DD HH:mm:ss') }
function operatorLabel(value: string) { return value === 'system' ? '系统自动任务' : value === 'anonymous' ? '未登录用户' : value }
onMounted(async () => {
  try { categoryOptions.value = await getAuditLogCategories() }
  catch { categoryOptions.value = [] }
  await loadLogs()
})
</script>

<style scoped>
.audit-page { min-width: 0; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.page-header h2 { margin: 0; font-size: 20px; color: var(--color-text-primary); }
.page-header p { margin: 5px 0 0; color: var(--color-text-secondary); font-size: 13px; }
.filter-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.filter-bar .ant-input { width: 260px; }.filter-bar .ant-select { width: 150px; }
.export-button { margin-left: auto; }
.summary-text { color: var(--color-text-primary); line-height: 1.6; }
.audit-detail { padding: 8px 32px 12px 176px; color: var(--color-text-secondary); }
.detail-heading { display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; }.detail-heading strong { color: var(--color-text-primary); }.detail-heading span { color: var(--color-text-secondary); }
.change-list, .context-list { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px 16px; margin: 0; }.context-list { margin-top: 10px; }.change-list dt, .context-list dt { color: var(--color-text-secondary); }.change-list dd, .context-list dd { margin: 0; color: var(--color-text-primary); }.change-arrow { padding: 0 6px; color: #1677ff; }
.technical-collapse { margin-top: 6px; }.technical-item { display: inline-block; margin-right: 20px; color: var(--color-text-secondary); }
@media (max-width: 720px) { .filter-bar { flex-wrap: wrap; }.filter-bar .ant-input, .filter-bar .ant-select { width: 100%; }.export-button { margin-left: 0; }.audit-detail { padding: 8px 4px 12px; }.change-list, .context-list { grid-template-columns: 1fr; gap: 2px; }.change-list dd, .context-list dd { margin-bottom: 8px; } }
</style>
