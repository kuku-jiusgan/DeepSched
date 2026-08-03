<template>
  <div class="schedule-engine">
    <div class="page-header">
      <h2>自动排程引擎</h2>
      <p>查看全部排程，并在确有需要时重新优化所有可移动任务。</p>
    </div>

    <section class="reschedule-panel" aria-labelledby="global-reschedule-title">
      <div>
        <h3 id="global-reschedule-title">全局重排</h3>
        <p>保留冻结、运行中和已完成的日程，撤销其余可移动排程后重新求解。</p>
      </div>
      <a-button v-operation="'reschedule'" type="primary" danger :loading="rescheduleLoading" @click="confirmGlobalReschedule">
        <ReloadOutlined /> 全局重排
      </a-button>
    </section>

    <section class="schedule-section" aria-labelledby="all-schedules-title">
      <div class="section-header">
        <div>
          <h3 id="all-schedules-title">全部排程</h3>
          <p>{{ scheduleSummary }}</p>
        </div>
      </div>

      <a-table
        :dataSource="allSlots"
        rowKey="id"
        size="small"
        :columns="slotColumns"
        :loading="tableLoading"
        :pagination="paginationConfig"
        :scroll="{ x: 1180 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tier'">
            <a-tag :color="record.tier === 'frozen' ? 'blue' : record.tier === 'confirmed' ? 'green' : 'default'">{{ tierLabels[record.tier] || record.tier }}</a-tag>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="record.status === 'completed' ? 'green' : record.status === 'running' ? 'blue' : 'default'">{{ statusLabels[record.status] || record.status }}</a-tag>
          </template>
          <template v-else-if="column.key === 'start'">{{ dayjs(record.plan_start).format('YYYY-MM-DD HH:mm') }}</template>
          <template v-else-if="column.key === 'end'">{{ dayjs(record.plan_end).format('YYYY-MM-DD HH:mm') }}</template>
        </template>
      </a-table>
    </section>

    <a-modal
      v-model:open="isFailureModalOpen"
      title="全局重排失败"
      :width="760"
      :footer="null"
      wrap-class-name="reschedule-failure-modal"
    >
      <template v-if="failureDetail">
        <div class="failure-heading">
          <div class="failure-heading-icon" aria-hidden="true">!</div>
          <div>
            <strong>项目时间窗不足</strong>
            <p>当前约束下，后续任务无法在项目截止时间前完成。</p>
          </div>
        </div>

        <dl class="failure-context">
          <div>
            <dt>冲突项目</dt>
            <dd>{{ failureDetail.projectName }}</dd>
          </div>
          <div>
            <dt>冲突任务</dt>
            <dd>{{ failureDetail.taskName }}</dd>
          </div>
        </dl>

        <section class="time-comparison" aria-label="时间冲突对比">
          <div class="project-window">
            <span>项目时间窗</span>
            <strong>{{ failureDetail.projectStart }}</strong>
            <i aria-hidden="true">→</i>
            <strong>{{ failureDetail.projectEnd }}</strong>
          </div>
          <div class="deadline-comparison">
            <div>
              <span>任务最早可开始</span>
              <strong>{{ failureDetail.earliestStart }}</strong>
              <small>{{ failureDetail.constraintReason }}</small>
            </div>
            <div class="deadline-value">
              <span>项目截止</span>
              <strong>{{ failureDetail.projectEnd }}</strong>
              <small>必须在此时间前完成</small>
            </div>
          </div>
        </section>

        <div class="capacity-comparison">
          <div>
            <span>任务所需</span>
            <strong>{{ formatHours(failureDetail.requiredHours) }}</strong>
          </div>
          <div>
            <span>剩余有效工时</span>
            <strong>{{ formatHours(failureDetail.availableHours) }}</strong>
          </div>
          <div class="capacity-shortage">
            <span>工时缺口</span>
            <strong>{{ formatHours(failureDetail.shortageHours) }}</strong>
          </div>
        </div>

        <div class="failure-advice">
          <strong>建议处理</strong>
          <p>{{ failureDetail.advice }}</p>
        </div>
      </template>
      <p v-else class="failure-fallback">{{ failureMessage }}</p>
      <div class="failure-actions">
        <a-button type="primary" @click="isFailureModalOpen = false">我知道了</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { isAxiosError } from "axios"
import { message, Modal } from "ant-design-vue"
import { ReloadOutlined } from "@ant-design/icons-vue"
import { getTimeslots, reschedule } from "@/services/api"
import dayjs from "dayjs"
import type { TimeSlot } from "@/types"

const allSlots = ref<TimeSlot[]>([])
const tableLoading = ref(false)
const rescheduleLoading = ref(false)
const isFailureModalOpen = ref(false)
const failureMessage = ref('')
const failureDetail = ref<RescheduleFailureDetail | null>(null)

interface RescheduleFailureDetail {
  projectName: string
  taskName: string
  projectStart: string
  projectEnd: string
  earliestStart: string
  constraintReason: string
  requiredHours: number
  availableHours: number
  shortageHours: number
  advice: string
}

const tierLabels: Record<string, string> = { frozen: '冻结', confirmed: '确认', forecast: '预测' }
const statusLabels: Record<string, string> = {
  completed: '已完成', running: '运行中', scheduled: '已排程',
  paused: '已暂停', interrupted: '已中断', blocked: '已阻塞', pending: '待处理',
}

const slotColumns = [
  { title: "仪器编号", dataIndex: "instrument_code", key: "instCode", width: 150 },
  { title: "仪器", dataIndex: "instrument_name", key: "inst", width: 180 },
  { title: "项目编号", dataIndex: "project_code", key: "projectCode", width: 140 },
  { title: "项目", dataIndex: "project_name", key: "proj", width: 220 },
  { title: "任务", dataIndex: "task_name", key: "task" },
  { title: "负责人", dataIndex: "assignee_name", key: "assignee", width: 110 },
  { title: "开始", dataIndex: "plan_start", key: "start", width: 150 },
  { title: "结束", dataIndex: "plan_end", key: "end", width: 150 },
  { title: "层级", dataIndex: "tier", key: "tier", width: 80 },
  { title: "状态", dataIndex: "status", key: "status", width: 90 },
]

const paginationConfig = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100'],
  showTotal: (total: number) => `共 ${total} 条排程`,
}

const scheduleSummary = computed(() => {
  const counts = allSlots.value.reduce<Record<string, number>>((result, slot) => {
    result[slot.tier] = (result[slot.tier] || 0) + 1
    return result
  }, {})
  return `共 ${allSlots.value.length} 条 · 冻结 ${counts.frozen || 0} · 确认 ${counts.confirmed || 0} · 预测 ${counts.forecast || 0}`
})

onMounted(loadSchedules)

async function loadSchedules() {
  tableLoading.value = true
  try {
    allSlots.value = await getTimeslots()
  } catch (error: unknown) {
    message.error(errorDetail(error, '排程信息加载失败'))
  } finally {
    tableLoading.value = false
  }
}

function confirmGlobalReschedule() {
  Modal.confirm({
    title: '确认执行全局重排？',
    content: '确认期和预测期内的可移动日程将被撤销并重新生成；冻结、运行中和已完成的日程保持不变。',
    okText: '确认重排',
    okType: 'danger',
    cancelText: '取消',
    onOk: executeGlobalReschedule,
  })
}

async function executeGlobalReschedule() {
  rescheduleLoading.value = true
  try {
    const result = await reschedule({ trigger_type: 'manual', strategy: 'global' })
    if (result.status !== 'ok') {
      showRescheduleFailure(result.message || '当前任务无法完成全局重排。')
      return
    }
    message.success(result.message || '全局重排完成')
    await loadSchedules()
  } catch (error: unknown) {
    showRescheduleFailure(errorDetail(error, '服务器内部错误，请稍后重试。'))
  } finally {
    rescheduleLoading.value = false
  }
}

function showRescheduleFailure(detail: string) {
  failureMessage.value = detail
  failureDetail.value = parseRescheduleFailure(detail)
  isFailureModalOpen.value = true
}

function parseRescheduleFailure(messageText: string): RescheduleFailureDetail | null {
  const pattern = /^时间配置冲突：项目【(.+?)】的任务【(.+?)】无法在项目时间窗内完成。项目时间：(.+?) 至 (.+?)；最早可开始时间：(.+?)（(.+?)）；任务需要约 ([\d.]+) 小时，剩余有效工时约 ([\d.]+) 小时。(.+)$/
  const match = messageText.match(pattern)
  if (!match) return null
  const requiredHours = Number(match[7])
  const availableHours = Number(match[8])
  return {
    projectName: match[1],
    taskName: match[2],
    projectStart: match[3],
    projectEnd: match[4],
    earliestStart: match[5],
    constraintReason: match[6],
    requiredHours,
    availableHours,
    shortageHours: Math.max(0, requiredHours - availableHours),
    advice: match[9],
  }
}

function formatHours(hours: number) {
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`
}

function errorDetail(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error)) {
    return error.response?.data?.detail || fallback
  }
  return fallback
}
</script>

<style scoped>
.schedule-engine { max-width: 1600px; }
.reschedule-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px;
  margin-bottom: 20px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.reschedule-panel h3, .section-header h3 { margin: 0; font-size: 15px; }
.reschedule-panel p, .section-header p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.schedule-section {
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border-light);
}
.schedule-section :deep(.ant-table-wrapper) { padding: 0 16px 16px; }
@media (max-width: 720px) {
  .reschedule-panel, .section-header { align-items: flex-start; flex-direction: column; }
  .reschedule-panel .ant-btn { width: 100%; }
}
</style>

<style>
.reschedule-failure-modal .ant-modal-content { padding: 0; overflow: hidden; }
.reschedule-failure-modal .ant-modal-header {
  margin: 0;
  padding: 20px 24px 16px;
  border-bottom: 1px solid #e2e8f0;
}
.reschedule-failure-modal .ant-modal-body { padding: 20px 24px 24px; }
.failure-heading { display: flex; align-items: flex-start; gap: 12px; }
.failure-heading-icon {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  color: #fff;
  background: #dc2626;
  font-size: 16px;
  font-weight: 700;
}
.failure-heading strong { color: #991b1b; font-size: 15px; }
.failure-heading p { margin: 3px 0 0; color: #64748b; font-size: 13px; }
.failure-context {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(180px, 1fr);
  gap: 12px 24px;
  margin: 20px 0 16px;
}
.failure-context div { min-width: 0; }
.failure-context dt,
.time-comparison span,
.capacity-comparison span {
  color: #64748b;
  font-size: 12px;
}
.failure-context dd { margin: 3px 0 0; color: #1e293b; font-size: 14px; font-weight: 600; }
.time-comparison {
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
}
.project-window {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}
.project-window span { margin-right: auto; }
.project-window strong { color: #334155; font-size: 13px; font-variant-numeric: tabular-nums; }
.project-window i { color: #94a3b8; font-style: normal; }
.deadline-comparison { display: grid; grid-template-columns: 1fr 1fr; }
.deadline-comparison > div { padding: 16px; }
.deadline-comparison > div + div { border-left: 1px solid #e2e8f0; }
.deadline-comparison strong {
  display: block;
  margin-top: 5px;
  color: #0f172a;
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}
.deadline-comparison small { display: block; margin-top: 4px; color: #64748b; font-size: 12px; }
.deadline-value { background: #fff7ed; }
.deadline-value strong { color: #c2410c; }
.capacity-comparison {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.capacity-comparison > div { padding: 13px 16px; }
.capacity-comparison > div + div { border-left: 1px solid #e2e8f0; }
.capacity-comparison strong { display: block; margin-top: 3px; color: #1e293b; font-size: 16px; }
.capacity-shortage { background: #fef2f2; }
.capacity-shortage strong { color: #b91c1c; }
.failure-advice { margin-top: 16px; padding: 12px 14px; border-radius: 8px; background: #f1f5f9; }
.failure-advice strong { color: #334155; font-size: 13px; }
.failure-advice p,
.failure-fallback { margin: 4px 0 0; color: #475569; font-size: 13px; line-height: 1.7; }
.failure-actions { display: flex; justify-content: flex-end; margin-top: 20px; }
@media (max-width: 640px) {
  .reschedule-failure-modal { padding: 12px; }
  .failure-context,
  .deadline-comparison,
  .capacity-comparison { grid-template-columns: 1fr; }
  .deadline-comparison > div + div,
  .capacity-comparison > div + div { border-top: 1px solid #e2e8f0; border-left: 0; }
  .project-window { align-items: flex-start; flex-wrap: wrap; }
  .project-window span { width: 100%; }
}
</style>
