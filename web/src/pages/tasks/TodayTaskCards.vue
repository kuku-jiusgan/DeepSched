<template>
  <section class="today-work">
    <div class="today-work-header">
      <h3>今日任务卡片</h3>
    </div>

    <div class="today-card-grid">
      <div
        v-for="group in todayCardGroups"
        :key="group.key"
        class="today-card-group"
        :class="'today-card-group-' + group.key"
      >
        <div class="today-card-group-title">
          <span class="today-card-dot" :class="'today-card-dot-' + group.key" />
          <span>{{ group.title }}</span>
        </div>

        <div v-if="group.cards.length" class="today-card-stack">
          <article v-for="card in group.cards" :key="card.key" class="today-card">
            <div v-if="card.category === 'exception'" class="today-card-meta">
              <a-tag :color="card.tagColor">{{ card.tagText }}</a-tag>
              <span>{{ card.statusText }}</span>
            </div>

            <div class="today-card-lines">
              <div><span>项目：</span>{{ card.projectText }}</div>
              <div><span>任务：</span>{{ card.taskText }}</div>
              <div><span>仪器：</span>{{ card.instrumentText }}</div>
              <div><span>负责人：</span>{{ card.ownerText }}</div>
              <div><span>计划时间：</span>{{ card.scheduleText }}</div>
              <div><span>实际时间：</span>{{ card.actualText }}</div>
              <div v-if="card.delayText"><span>延期：</span>{{ card.delayText }}</div>
            </div>

            <div class="today-card-choice">请选择：</div>
            <div v-if="card.nightRunSummary" class="today-card-night-summary">
              {{ card.nightRunSummary }}
            </div>
            <div class="today-card-actions">
              <a-button
                v-if="canStartTask(card.task)"
                v-operation="'start'"
                size="small"
                class="workspace-action-button workspace-action-button-primary"
                @click="emit('start', card.task)"
              >
                <PlayCircleOutlined /> {{ card.task.execution_status === 'paused' ? '恢复任务' : '开始任务' }}
              </a-button>
              <a-button v-if="canCompleteTask(card.task)" v-operation="'complete'" size="small" class="workspace-action-button workspace-action-button-success" @click="emit('complete', card.task)">确认完成</a-button>
              <a-button v-if="card.task.execution_status === 'running'" v-operation="'complete'" size="small" @click="emit('pause', card.task)">
                <PauseCircleOutlined /> 暂停
              </a-button>
              <a-tooltip :title="card.nightRunDisabledReason">
                <span v-operation="'night_run'">
                  <a-button size="small" class="workspace-action-button workspace-action-button-primary" :loading="card.isNightRunLoading" :disabled="!card.canNightRun" @click="openAutoSequence(card)">夜间运行</a-button>
                </span>
              </a-tooltip>
              <a-button v-operation="'delay'" size="small" class="workspace-action-button workspace-action-button-danger" @click="openDelayReport(card)">延期使用</a-button>
            </div>
          </article>
        </div>

        <div v-else class="today-card-empty">
          暂无{{ group.title }}事项
        </div>
      </div>
      <slot name="additional-group" />
    </div>

    <a-modal
      v-model:open="autoSequenceOpen"
      title="夜间运行"
      ok-text="提交"
      cancel-text="取消"
      :confirm-loading="autoSequenceSubmitting"
      @ok="submitAutoSequence"
      @cancel="handleAutoSequenceCancel"
    >
      <div v-if="selectedCard" class="modal-task-summary">
        {{ selectedCard.projectText }} · {{ selectedCard.taskText }} · {{ selectedCard.instrumentText }}
      </div>
      <a-form layout="vertical">
        <a-form-item label="夜间运行时长" :extra="nightRunDurationHint">
          <a-input-number
            v-model:value="autoSequenceForm.durationHours"
            :min="0.5"
            :max="nightRunMaxHours || undefined"
            :step="0.5"
            addon-after="小时"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="开始时间">
          <a-time-picker
            v-model:value="selectedStartTime"
            format="HH:mm"
            value-format="HH:mm"
            :minute-step="30"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="结束时间" extra="根据开始时间和夜间运行时长自动推断，可调整">
          <a-time-picker
            v-model:value="selectedEndTime"
            format="HH:mm"
            value-format="HH:mm"
            :minute-step="30"
            style="width: 100%"
          />
          <div v-if="endTimeDisplayLabel" class="night-run-time-hint">
            实际结束：{{ endTimeDisplayLabel }}
          </div>
        </a-form-item>
        <a-form-item label="是否需要人在场启动">
          <a-radio-group v-model:value="autoSequenceForm.requiresOperator">
            <a-radio :value="true">是</a-radio>
            <a-radio :value="false">否</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="autoSequenceForm.remark" :rows="3" placeholder="补充序列、样品或交接注意事项" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="delayReportOpen"
      title="异常/延期确认"
      ok-text="提交并顺延排程"
      cancel-text="取消"
      :confirm-loading="delaySubmitting"
      @ok="submitDelayReport"
    >
      <div v-if="selectedCard" class="modal-task-summary">
        {{ selectedCard.projectText }} · {{ selectedCard.taskText }} · {{ selectedCard.instrumentText }}
      </div>
      <a-form layout="vertical">
        <a-form-item label="需要延期多久">
          <a-input-number v-model:value="delayForm.delayHours" :min="0.5" :step="0.5" addon-after="小时" style="width: 100%" />
        </a-form-item>
        <a-form-item label="异常原因" required>
          <a-textarea v-model:value="delayForm.reason" :rows="4" placeholder="例如：样品前处理超时、仪器状态异常、方法参数需要重新确认" />
        </a-form-item>
      </a-form>
      <div class="delay-hint">
        提交后会延长当前任务排程，并将同项目或同仪器的后续排程整体后移。
      </div>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons-vue'
import { getScheduleRules, recordNightRun, reportTaskDelay } from '@/services/api'
import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { actionableSlotId } from '@/domains/tasks/workspaceTask'
import dayjs from 'dayjs'
import {
  actualText, canCompleteTask, canStartTask, currentUserName, formatHours,
  formatInstrumentText, formatProjectText, formatTaskTime, getDelayText, getNightRunEligibility,
  isCompletionConfirmTask, isHalfHourDuration, isWorkspaceExceptionConfirmTask, maxNightRunHours,
  nightRunEndTime, normalizeWorkdayEndTime, parseNightClock, scheduleText, statusLabel,
} from './todayTaskCardUtils'

type TodayCardCategory = 'completion' | 'exception'
type TodayCardGroupKey = TodayCardCategory

interface Props {
  tasks: WorkspaceTask[]
}

interface TodayTaskCard {
  key: string
  category: TodayCardCategory
  task: WorkspaceTask
  projectText: string
  taskText: string
  instrumentText: string
  ownerText: string
  scheduleText: string
  actualText: string
  tagText: string
  tagColor: string
  statusText: string
  earliestStart: string
  latestEnd: string
  nightRunSummary: string
  delayText: string
  canNightRun: boolean
  isNightRunLoading: boolean
  nightRunDisabledReason: string
}

interface TodayCardGroup {
  key: TodayCardGroupKey
  title: string
  cards: TodayTaskCard[]
}

interface AutoSequenceForm {
  durationHours: number
  startTime: string
  endTime: string
  requiresOperator: boolean
  remark: string
}

interface StoredAutoSequenceForm extends AutoSequenceForm {
  committed: true
  savedAt: string
}

interface DelayForm {
  delayHours: number
  reason: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  start: [task: WorkspaceTask]
  complete: [task: WorkspaceTask]
  pause: [task: WorkspaceTask]
  refreshed: []
}>()

const NIGHT_RESERVE_END = '次日 08:30'
const DEFAULT_NIGHT_START = '17:30'
const DEFAULT_SEQUENCE_DURATION_HOURS = 8
const DEFAULT_DELAY_HOURS = 1
const NIGHT_RUN_STORAGE_PREFIX = 'deepsched:today-night-run'
const WORKING_HOURS_RULE_CODE = 'working_hours'

const autoSequenceOpen = ref(false)
const delayReportOpen = ref(false)
const autoSequenceSubmitting = ref(false)
const delaySubmitting = ref(false)
const selectedCard = ref<TodayTaskCard | null>(null)
const autoSequenceForm = reactive<AutoSequenceForm>({
  durationHours: DEFAULT_SEQUENCE_DURATION_HOURS,
  startTime: DEFAULT_NIGHT_START,
  endTime: NIGHT_RESERVE_END,
  requiresOperator: false,
  remark: '',
})
const delayForm = reactive<DelayForm>({
  delayHours: DEFAULT_DELAY_HOURS,
  reason: '',
})
const nightRunRevision = ref(0)
const workdayEndTime = ref<string | null>(null)
const isWorkingHoursLoading = ref(true)

const todayCardGroups = computed<TodayCardGroup[]>(() => {
  const completionCards = props.tasks
    .filter(task => isCompletionConfirmTask(task))
    .map(task => buildTodayCard(task, 'completion'))
  const exceptionCards = props.tasks
    .filter(task => isWorkspaceExceptionConfirmTask(task))
    .map(task => buildTodayCard(task, 'exception'))

  return [
    { key: 'completion', title: '任务完成确认', cards: completionCards },
    { key: 'exception', title: '异常/延期确认', cards: exceptionCards },
  ]
})

const nightRunMaxHours = computed(() => {
  return maxNightRunHours(autoSequenceForm.startTime, NIGHT_RESERVE_END)
})

const selectedNightRunHours = computed(() => {
  return maxNightRunHours(autoSequenceForm.startTime, autoSequenceForm.endTime)
})

const nightRunDurationHint = computed(() => {
  if (!nightRunMaxHours.value) return '请选择有效的开始时间'
  return `结束时间将自动推断，最晚可运行至 ${NIGHT_RESERVE_END}`
})

const selectedStartTime = computed<string | null>({
  get: () => autoSequenceForm.startTime || null,
  set: value => {
    autoSequenceForm.startTime = value || ''
    syncEndTimeWithDuration()
  },
})

const selectedEndTime = computed<string | null>({
  get: () => formatPickerTime(autoSequenceForm.endTime),
  set: value => {
    autoSequenceForm.endTime = formatStoredEndTime(value || '')
  },
})

const endTimeDisplayLabel = computed(() => formatNightDisplayLabel(autoSequenceForm.endTime))

watch(
  () => autoSequenceForm.durationHours,
  () => {
    if (autoSequenceOpen.value) syncEndTimeWithDuration()
  },
)

fetchWorkingHours()

async function fetchWorkingHours() {
  isWorkingHoursLoading.value = true
  try {
    const rules = await getScheduleRules()
    const workingRule = rules.find(rule => rule.code === WORKING_HOURS_RULE_CODE)
    const dayEnd = workingRule?.params?.day_end
    workdayEndTime.value = normalizeWorkdayEndTime(dayEnd)
  } catch {
    workdayEndTime.value = null
  } finally {
    isWorkingHoursLoading.value = false
  }
}

function canNightRunTask(task: WorkspaceTask, storedNightRun?: StoredAutoSequenceForm | null) {
  if (storedNightRun) return true
  if (isWorkingHoursLoading.value) return false
  if (!workdayEndTime.value) return false
  return getNightRunEligibility(task, workdayEndTime.value).isEligible
}

function nightRunDisabledReason(task: WorkspaceTask, storedNightRun?: StoredAutoSequenceForm | null) {
  if (canNightRunTask(task, storedNightRun)) return ''
  if (!task.actionable_slot?.plan_end) return '当前可执行时间段没有计划结束时间，不能继续夜间运行'
  if (isWorkingHoursLoading.value) return '正在读取排程规则中的有效工作时段'
  if (!workdayEndTime.value) return '未读取到排程规则中的有效工作时段，暂不能继续夜间运行'
  return getNightRunEligibility(task, workdayEndTime.value).reason
}

function buildTodayCard(task: WorkspaceTask, category: TodayCardCategory): TodayTaskCard {
  const nightStart = formatTaskTime(nightRunEndTime(task), DEFAULT_NIGHT_START)
  nightRunRevision.value
  const storedNightRun = readNightRunForm(task)
  const tagTextMap: Record<TodayCardCategory, string> = {
    completion: '完成',
    exception: '异常',
  }
  const tagColorMap: Record<TodayCardCategory, string> = {
    completion: 'green',
    exception: 'red',
  }

  return {
    key: `${category}-${actionableSlotId(task) || task.task_id}`,
    category,
    task,
    projectText: formatProjectText(task),
    taskText: task.task_name || '未命名任务',
    instrumentText: formatInstrumentText(task),
    ownerText: task.assignee_name || currentUserName(),
    scheduleText: scheduleText(task),
    actualText: actualText(task),
    tagText: tagTextMap[category],
    tagColor: tagColorMap[category],
    statusText: statusLabel(task.execution_status),
    earliestStart: nightStart,
    latestEnd: NIGHT_RESERVE_END,
    nightRunSummary: storedNightRun ? formatNightRunSummary(storedNightRun) : '',
    delayText: getDelayText(task),
    canNightRun: canNightRunTask(task, storedNightRun),
    isNightRunLoading: isWorkingHoursLoading.value,
    nightRunDisabledReason: nightRunDisabledReason(task, storedNightRun),
  }
}

function openAutoSequence(card: TodayTaskCard) {
  if (!card.canNightRun) {
    message.warning(card.nightRunDisabledReason)
    return
  }
  selectedCard.value = card
  const savedForm = readNightRunForm(card.task)
  setAutoSequenceForm(savedForm || {
    durationHours: DEFAULT_SEQUENCE_DURATION_HOURS,
    startTime: card.earliestStart,
    endTime: card.latestEnd,
    requiresOperator: false,
    remark: '',
  })
  syncEndTimeWithDuration()
  autoSequenceOpen.value = true
}

function handleAutoSequenceCancel() {
  autoSequenceOpen.value = false
  resetAutoSequenceDraft()
}

function openDelayReport(card: TodayTaskCard) {
  selectedCard.value = card
  delayForm.delayHours = DEFAULT_DELAY_HOURS
  delayForm.reason = ''
  delayReportOpen.value = true
}

async function submitAutoSequence() {
  if (!selectedCard.value) return
  const slotId = actionableSlotId(selectedCard.value.task)
  if (!slotId) {
    message.warning('当前任务没有可执行时间段')
    return
  }
  if (typeof autoSequenceForm.durationHours !== 'number' || autoSequenceForm.durationHours <= 0) {
    message.warning('请填写夜间运行时长')
    return
  }
  if (!isHalfHourDuration(autoSequenceForm.durationHours)) {
    message.warning('夜间运行时长需以 0.5 小时为颗粒度')
    return
  }
  if (!nightRunMaxHours.value || autoSequenceForm.durationHours > nightRunMaxHours.value) {
    message.warning(`夜间运行时长不能超过 ${formatHours(nightRunMaxHours.value)} 小时`)
    return
  }
  if (autoSequenceForm.durationHours > selectedNightRunHours.value) {
    message.warning('结束时间不能早于按夜间运行时长推断的时间')
    return
  }
  autoSequenceSubmitting.value = true
  try {
      await recordNightRun(slotId, {
        duration_hours: autoSequenceForm.durationHours,
        earliest_start: autoSequenceForm.startTime,
        latest_end: autoSequenceForm.endTime,
        requires_operator: autoSequenceForm.requiresOperator,
        remark: autoSequenceForm.remark.trim() || undefined,
      })
    message.success(`${selectedCard.value.taskText} 已记录为夜间运行`)
    saveNightRunForm(selectedCard.value.task)
    autoSequenceOpen.value = false
    emit('refreshed')
  } catch {
    message.error('夜间运行记录失败')
  } finally {
    autoSequenceSubmitting.value = false
  }
}

function setAutoSequenceForm(form: AutoSequenceForm) {
  autoSequenceForm.durationHours = form.durationHours
  autoSequenceForm.startTime = form.startTime
  autoSequenceForm.endTime = form.endTime
  autoSequenceForm.requiresOperator = form.requiresOperator
  autoSequenceForm.remark = form.remark
}

function resetAutoSequenceDraft() {
  setAutoSequenceForm({
    durationHours: DEFAULT_SEQUENCE_DURATION_HOURS,
    startTime: DEFAULT_NIGHT_START,
    endTime: NIGHT_RESERVE_END,
    requiresOperator: false,
    remark: '',
  })
  selectedCard.value = null
}

function nightRunStorageKey(task: WorkspaceTask) {
  const todayKey = dayjs().format('YYYY-MM-DD')
  const resourceKey = task.actionable_slot?.instrument_id || actionableSlotId(task)
  return `${NIGHT_RUN_STORAGE_PREFIX}:${todayKey}:${task.task_id}:${resourceKey}`
}

function readNightRunForm(task: WorkspaceTask): StoredAutoSequenceForm | null {
  try {
    const rawValue = localStorage.getItem(nightRunStorageKey(task))
    if (!rawValue) return null
    const parsedValue = JSON.parse(rawValue) as Partial<StoredAutoSequenceForm> & {
      earliestStart?: string
      latestEnd?: string
    }
    if (parsedValue.committed !== true) return null
    const startTime = parsedValue.startTime || parsedValue.earliestStart
    const endTime = parsedValue.endTime || parsedValue.latestEnd
    if (!parsedValue.durationHours || !startTime || !endTime) return null
    return {
      committed: true,
      durationHours: parsedValue.durationHours,
      startTime,
      endTime,
      requiresOperator: Boolean(parsedValue.requiresOperator),
      remark: parsedValue.remark || '',
      savedAt: parsedValue.savedAt || dayjs().toISOString(),
    }
  } catch {
    return null
  }
}

function saveNightRunForm(task: WorkspaceTask) {
  const storedForm: StoredAutoSequenceForm = {
    committed: true,
    durationHours: autoSequenceForm.durationHours,
    startTime: autoSequenceForm.startTime,
    endTime: autoSequenceForm.endTime,
    requiresOperator: autoSequenceForm.requiresOperator,
    remark: autoSequenceForm.remark,
    savedAt: dayjs().toISOString(),
  }
  localStorage.setItem(nightRunStorageKey(task), JSON.stringify(storedForm))
  nightRunRevision.value += 1
}

function formatNightRunSummary(form: StoredAutoSequenceForm) {
  const operatorText = form.requiresOperator ? '需人在场启动' : '无需人在场'
  return `今日夜间运行已填写：${form.startTime} - ${form.endTime}，${form.durationHours}h，${operatorText}`
}

function syncEndTimeWithDuration() {
  const start = parseNightClock(dayjs(), autoSequenceForm.startTime)
  if (!start || !isHalfHourDuration(autoSequenceForm.durationHours)) return
  autoSequenceForm.endTime = formatStoredEndTime(start.add(autoSequenceForm.durationHours, 'hour').format('HH:mm'), start)
}

function formatPickerTime(value: string) {
  return value.replace('次日', '').trim() || null
}

function formatNightDisplayLabel(value: string) {
  if (!value) return ''
  if (value.startsWith('次日')) return value
  return value
}

function formatStoredEndTime(value: string, startTime?: dayjs.Dayjs) {
  if (!value) return ''
  const start = startTime || parseNightClock(dayjs(), autoSequenceForm.startTime)
  const end = parseNightClock(start || dayjs(), value)
  if (!end) return value
  if (start && end.isAfter(start) && !end.isSame(start, 'day')) {
    return `次日 ${end.format('HH:mm')}`
  }
  return end.format('HH:mm')
}

async function submitDelayReport() {
  if (!selectedCard.value) return
  const slotId = actionableSlotId(selectedCard.value.task)
  if (!slotId) {
    message.warning('当前任务没有可执行时间段')
    return
  }
  if (!delayForm.reason.trim()) {
    message.warning('请填写异常原因')
    return
  }
  delaySubmitting.value = true
  try {
    const result = await reportTaskDelay(slotId, {
      delay_hours: delayForm.delayHours,
      reason: delayForm.reason.trim(),
    })
    message.success(`已顺延排程，影响 ${result.shifted_slots} 个时间槽`)
    delayReportOpen.value = false
    emit('refreshed')
  } catch {
    message.error('异常延期提交失败')
  } finally {
    delaySubmitting.value = false
  }
}
</script>

<style scoped src="./todayTaskCards.css"></style>
