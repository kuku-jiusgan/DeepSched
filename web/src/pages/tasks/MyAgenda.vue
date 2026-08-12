<template>
  <div class="agenda-page">
    <div class="page-header agenda-heading">
      <div>
        <h2>我的安排</h2>
        <span>{{ result?.assignee.display_name || '当前用户' }} · {{ rangeLabel }}</span>
      </div>
    </div>

    <div class="agenda-toolbar">
      <a-select
        v-if="canSelectAssignee"
        v-model:value="selectedAssigneeId"
        show-search
        option-filter-prop="label"
        :options="userOptions"
        :loading="usersLoading"
        class="agenda-user-select"
        @change="loadAgenda"
      >
        <template #suffixIcon><UserOutlined /></template>
      </a-select>
      <a-segmented v-model:value="activePreset" :options="presetOptions" @change="applyPreset" />
      <a-range-picker
        v-model:value="dateRange"
        format="YYYY-MM-DD"
        :allow-clear="false"
        :placeholder="['开始日期', '结束日期']"
        @change="applyCustomRange"
      />
      <a-button :loading="loading" @click="loadAgenda"><ReloadOutlined /> 刷新</a-button>
    </div>

    <a-skeleton v-if="loading" active :paragraph="{ rows: 8 }" />

    <div v-else class="agenda-days">
      <section v-for="day in agendaDays" :key="day.key" class="agenda-day">
        <header class="agenda-day-header" :class="{ 'is-today': day.date.isSame(dayjs(), 'day') }">
          <strong>{{ day.date.format('MM月DD日') }}</strong>
          <span>周{{ weekdayLabel(day.date.day()) }}</span>
          <a-tag v-if="day.date.isSame(dayjs(), 'day')" color="blue">今天</a-tag>
        </header>

        <div v-if="day.items.length" class="agenda-item-list">
          <article
            v-for="item in day.items"
            :key="`${day.key}-${item.slot_id}`"
            class="agenda-item"
            :class="{ 'has-conflict': item.hasConflict, 'is-overdue': item.isOverdue }"
          >
            <div class="agenda-time">
              <strong>{{ item.displayStart.format('HH:mm') }}</strong>
              <span>{{ item.displayEnd.format('HH:mm') }}</span>
              <small>{{ formatDuration(item.displayStart, item.displayEnd) }}</small>
            </div>
            <div class="agenda-task">
              <strong>{{ agendaTaskName(item) }}</strong>
              <button type="button" class="agenda-project-link" @click="openProject(item.project_id)">
                {{ item.project_code }} · {{ item.project_name }}
              </button>
            </div>
            <div class="agenda-resource">
              <span class="agenda-resource-icon" aria-hidden="true"><ExperimentOutlined /></span>
              <span class="agenda-resource-label">{{ instrumentText(item) }}</span>
            </div>
            <div class="agenda-status">
              <a-tag v-if="item.isOverdue" color="orange"><ClockCircleOutlined /> 延期未完成</a-tag>
              <a-tag v-if="item.hasConflict" color="red"><WarningOutlined /> 时间重叠</a-tag>
              <a-tag :color="taskStatusColor(item.execution_status)">{{ taskStatusLabel(item.execution_status) }}</a-tag>
            </div>
            <div class="agenda-actions">
              <a-button
                v-if="canStart(item)"
                v-operation.readonly="'start'"
                size="small"
                type="primary"
                :disabled="!isOwnAgenda"
                @click="startAgendaItem(item)"
              ><PlayCircleOutlined /> {{ startActionLabel(item) }}</a-button>
              <a-button
                v-if="canComplete(item)"
                v-operation.readonly="'complete'"
                size="small"
                :disabled="!isOwnAgenda"
                @click="openComplete(item)"
              ><CheckCircleOutlined /> 确认完成</a-button>
              <a-button
                v-if="canPause(item)"
                v-operation.readonly="'pause'"
                size="small"
                danger
                :disabled="!isOwnAgenda"
                @click="openPause(item)"
              ><PauseCircleOutlined /> 暂停</a-button>
              <a-button
                v-if="canReportDelay(item)"
                v-operation.readonly="'delay'"
                size="small"
                :disabled="!isOwnAgenda"
                @click="openDelay(item)"
              ><ClockCircleOutlined /> 延期使用</a-button>
            </div>
          </article>
        </div>
        <a-empty v-else :image="simpleImage" description="暂无安排" class="agenda-empty" />
      </section>
    </div>

    <a-modal v-model:open="completeOpen" title="确认结束任务" :footer="null">
      <p v-if="completeItem">确认结束“{{ agendaTaskName(completeItem) }}”吗？</p>
      <div class="agenda-modal-actions">
        <a-button @click="completeOpen = false">取消</a-button>
        <a-button :loading="actionLoading" @click="submitComplete(false)">仅标记完成</a-button>
        <a-button type="primary" :loading="actionLoading" @click="submitComplete(true)">释放仪器并前移</a-button>
      </div>
    </a-modal>

    <a-modal v-model:open="delayOpen" title="报告延期使用" ok-text="提交" cancel-text="取消" :confirm-loading="actionLoading" @ok="submitDelay">
      <a-form layout="vertical">
        <a-form-item label="延期时长" required><a-input-number v-model:value="delayHours" :min="0.5" :step="0.5" addon-after="小时" style="width: 100%" /></a-form-item>
        <a-form-item label="延期原因" required><a-textarea v-model:value="delayReason" :rows="4" placeholder="填写样品、仪器或方法原因" /></a-form-item>
      </a-form>
    </a-modal>

    <PauseTaskModal v-model:open="pauseOpen" :task="pauseTask" @completed="loadAgenda" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Empty, message } from 'ant-design-vue'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue'
import { useRouter } from 'vue-router'
import dayjs, { type Dayjs } from 'dayjs'
import { completeTask, getUserDirectory, reportTaskDelay, startTask, type UserDirectoryEntry } from '@/services/api'
import { getMyAgenda, type AgendaItem, type AgendaResult } from '@/services/workspaceApi'
import { agendaTaskName, buildAgendaDays, type AgendaDisplayItem } from '@/domains/tasks/agenda'
import {
  canCompleteWorkspaceTask,
  canPauseWorkspaceTask,
  canStartWorkspaceTask,
  type WorkspaceTask,
} from '@/domains/tasks/workspaceTask'
import PauseTaskModal from './PauseTaskModal.vue'
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'
import './MyAgenda.css'

const router = useRouter()
const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE
const result = ref<AgendaResult | null>(null)
const loading = ref(true)
const usersLoading = ref(false)
const users = ref<UserDirectoryEntry[]>([])
const selectedAssigneeId = ref<number>()
const activePreset = ref('7')
const actionLoading = ref(false)
const completeOpen = ref(false)
const delayOpen = ref(false)
const pauseOpen = ref(false)
const completeItem = ref<AgendaDisplayItem | null>(null)
const delayItem = ref<AgendaDisplayItem | null>(null)
const pauseTask = ref<WorkspaceTask | null>(null)
const delayHours = ref(1)
const delayReason = ref('')
const dateRange = ref<[Dayjs, Dayjs]>([dayjs().startOf('day'), dayjs().add(6, 'day').startOf('day')])
const presetOptions = [
  { label: '未来7天', value: '7' },
  { label: '未来14天', value: '14' },
  { label: '未来30天', value: '30' },
  { label: '自定义', value: 'custom' },
]

const currentUserId = storedCurrentUserId()
const canSelectAssignee = computed(() => Boolean(result.value?.can_select_assignee))
const isOwnAgenda = computed(() => result.value?.assignee.id === currentUserId)
const userOptions = computed(() => users.value
  .filter(isSelectableAgendaUser)
  .map(user => ({ value: user.id, label: user.display_name })))
const agendaDays = computed(() => buildAgendaDays(result.value?.items || [], dateRange.value[0], dateRange.value[1]))
const rangeLabel = computed(() => `${dateRange.value[0].format('YYYY-MM-DD')} 至 ${dateRange.value[1].format('YYYY-MM-DD')}`)

function storedCurrentUserId() {
  try {
    const user = JSON.parse(localStorage.getItem('user') || '{}') as { id?: unknown }
    return typeof user.id === 'number' ? user.id : null
  } catch {
    return null
  }
}

function userRoles(user: UserDirectoryEntry) {
  return user.roles?.length ? user.roles : [user.role]
}

function isSelectableAgendaUser(user: UserDirectoryEntry) {
  if (!user.is_active) return false
  const roles = userRoles(user)
  return roles.includes('系统管理员') || !roles.includes('项目管理员')
}

function applyPreset(value: string | number) {
  if (value === 'custom') return
  const days = Number(value)
  dateRange.value = [dayjs().startOf('day'), dayjs().add(days - 1, 'day').startOf('day')]
  void loadAgenda()
}

function applyCustomRange(value: [Dayjs, Dayjs] | null) {
  if (!value) return
  dateRange.value = value
  activePreset.value = 'custom'
  void loadAgenda()
}

async function loadAgenda() {
  loading.value = true
  try {
    result.value = await getMyAgenda({
      start_date: dateRange.value[0].format('YYYY-MM-DD'),
      end_date: dateRange.value[1].format('YYYY-MM-DD'),
      ...(selectedAssigneeId.value ? { assignee_id: selectedAssigneeId.value } : {}),
    })
    selectedAssigneeId.value = result.value.assignee.id
    if (result.value.can_select_assignee && !users.value.length) await loadUsers()
  } catch (error: unknown) {
    message.error(errorDetail(error, '安排加载失败'))
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  usersLoading.value = true
  try {
    users.value = await getUserDirectory()
  } catch {
    message.error('人员列表加载失败')
  } finally {
    usersLoading.value = false
  }
}

function instrumentText(item: AgendaItem) {
  return [item.instrument_code, item.instrument_name].filter(Boolean).join(' · ') || '无需仪器'
}

function formatDuration(start: Dayjs, end: Dayjs) {
  const hours = end.diff(start, 'minute') / 60
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
}

function weekdayLabel(day: number) {
  return ['日', '一', '二', '三', '四', '五', '六'][day]
}

function openProject(projectId: number) {
  void router.push({ path: '/kanban/project-gantt', query: { project_id: projectId } })
}

function canStart(item: AgendaDisplayItem) {
  return canStartWorkspaceTask(toWorkspaceTask(item))
}

function canComplete(item: AgendaDisplayItem) {
  return canCompleteWorkspaceTask(toWorkspaceTask(item))
}

function canPause(item: AgendaDisplayItem) {
  return canPauseWorkspaceTask(toWorkspaceTask(item))
}

function canReportDelay(item: AgendaDisplayItem) {
  return item.execution_status !== 'completed'
}

async function startAgendaItem(item: AgendaDisplayItem) {
  const isPaused = item.execution_status === 'paused'
  await runAction(() => startTask(item.slot_id), isPaused ? '任务已恢复' : '任务已开始')
}

function startActionLabel(item: AgendaDisplayItem) {
  return item.execution_status === 'paused' ? '恢复' : '开始'
}

function openComplete(item: AgendaDisplayItem) {
  completeItem.value = item
  completeOpen.value = true
}

async function submitComplete(releaseInstrument: boolean) {
  if (!completeItem.value) return
  await runAction(
    () => completeTask(completeItem.value!.slot_id, { release_instrument: releaseInstrument }),
    releaseInstrument ? '任务已完成，仪器已释放' : '任务已完成',
    () => { completeOpen.value = false },
  )
}

function openPause(item: AgendaDisplayItem) {
  pauseTask.value = toWorkspaceTask(item)
  pauseOpen.value = true
}

function openDelay(item: AgendaDisplayItem) {
  delayItem.value = item
  delayHours.value = 1
  delayReason.value = ''
  delayOpen.value = true
}

async function submitDelay() {
  if (!delayItem.value || !delayReason.value.trim()) {
    message.warning('请填写延期原因')
    return
  }
  await runAction(
    () => reportTaskDelay(delayItem.value!.slot_id, { delay_hours: delayHours.value, reason: delayReason.value.trim() }),
    '延期已提交',
    () => { delayOpen.value = false },
  )
}

async function runAction(action: () => Promise<unknown>, successText: string, after?: () => void) {
  if (actionLoading.value) return
  actionLoading.value = true
  try {
    await action()
    message.success(successText)
    after?.()
    await loadAgenda()
  } catch (error: unknown) {
    message.error(errorDetail(error, '操作失败'))
  } finally {
    actionLoading.value = false
  }
}

function toWorkspaceTask(item: AgendaDisplayItem): WorkspaceTask {
  const slot = {
    id: item.slot_id,
    instrument_id: item.instrument_id,
    instrument_name: item.instrument_name,
    instrument_code: item.instrument_code,
    plan_start: item.plan_start,
    plan_end: item.plan_end,
    actual_start: item.actual_start,
    actual_end: item.actual_end,
    tier: 'confirmed',
    status: item.execution_status,
  }
  return {
    task_id: item.task_id,
    task_name: item.task_name,
    top_level_task_name: item.top_level_task_name,
    task_type: null,
    assignee_id: null,
    assignee_name: result.value?.assignee.display_name || null,
    project_id: item.project_id,
    project_name: item.project_name,
    project_code: item.project_code,
    execution_status: item.execution_status,
    est_duration_hours: null,
    actual_duration_hours: null,
    task_window: { start: item.plan_start, end: item.plan_end },
    actual_window: { start: item.actual_start, end: item.actual_end },
    actionable_slot: slot,
    segments: [slot],
    delay: { status: 'not_delayed', hours: null, reason: null, reported_at: null },
    resume_priority: null,
  }
}

function errorDetail(error: unknown, fallback: string) {
  const candidate = error as { response?: { data?: { detail?: string } } }
  return candidate.response?.data?.detail || fallback
}

onMounted(loadAgenda)
</script>
