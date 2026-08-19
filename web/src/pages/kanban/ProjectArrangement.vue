<template>
  <div class="project-arrangement">
    <button v-if="pastDays.length" type="button" class="project-arrangement-history-toggle" :aria-expanded="isHistoryExpanded" @click="isHistoryExpanded = !isHistoryExpanded">
      {{ isHistoryExpanded ? '收起今天之前的日期' : `展开今天之前的日期（${pastDays.length}天）` }}
    </button>
    <section v-for="day in visibleDays" :key="day.key" class="project-arrangement-day">
      <header class="project-arrangement-day-header" :class="{ 'is-unscheduled': day.isUnscheduled }">
        <template v-if="day.date">
          <strong>{{ day.date.format('MM月DD日') }}</strong>
          <span>周{{ weekdayLabel(day.date.day()) }}</span>
        </template>
        <strong v-else>未排程</strong>
        <a-tag v-if="day.date?.isSame(dayjs(), 'day')" color="blue">今天</a-tag>
      </header>
      <div class="project-arrangement-list">
        <article v-for="item in day.items" :key="`${day.key}-${item.slot_id ?? item.task_id}`" class="project-arrangement-item" :class="{ 'is-overdue': item.isOverdue }">
          <div class="project-arrangement-time">
            <strong>{{ planTime(item) }}</strong>
            <small v-if="item.plan_start && item.plan_end">{{ formatDuration(item.plan_start, item.plan_end) }}</small>
          </div>
          <div class="project-arrangement-task">
            <strong>{{ taskName(item) }}</strong>
            <small>{{ item.is_external_gate ? '方案签批' : item.slot_status ? '项目任务' : '尚未生成时间片' }}</small>
          </div>
          <div class="project-arrangement-assignee"><UserOutlined /><span>{{ item.assignee_name || '未指定负责人' }}</span></div>
          <div class="project-arrangement-resource"><ExperimentOutlined /><span>{{ instrumentText(item) }}</span></div>
          <div class="project-arrangement-result">
            <span class="project-arrangement-actual">{{ actualTime(item) }}</span>
            <div class="project-arrangement-status">
              <a-tag v-if="item.dailyState === 'approval' && item.isOverdue" color="orange"><ClockCircleOutlined /> 延期未完成</a-tag>
              <a-tag :color="dailyStatusColor(item)">{{ dailyStatusLabel(item) }}</a-tag>
            </div>
          </div>
        </article>
      </div>
    </section>
    <a-empty v-if="!days.length" description="暂无项目安排" />
  </div>
</template>

<script setup lang="ts">
import dayjs from 'dayjs'
import { computed, ref } from 'vue'
import { ClockCircleOutlined, ExperimentOutlined, UserOutlined } from '@ant-design/icons-vue'
import type { ProjectArrangementItem } from '@/services/api'
import { buildProjectArrangementDays, projectArrangementActualText, type ProjectArrangementDisplayItem } from '@/domains/tasks/projectArrangement'
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'

const props = defineProps<{ items: ProjectArrangementItem[] }>()
const days = computed(() => buildProjectArrangementDays(props.items).sort((left, right) => {
  const today = dayjs().startOf('day')
  const rank = (day: typeof left) => !day.date ? 3 : day.date.isBefore(today, 'day') ? 0 : day.date.isSame(today, 'day') ? 1 : 2
  return rank(left) - rank(right) || (left.date?.valueOf() || Number.MAX_SAFE_INTEGER) - (right.date?.valueOf() || Number.MAX_SAFE_INTEGER)
}))
const isHistoryExpanded = ref(false)
const pastDays = computed(() => days.value.filter(day => day.date?.isBefore(dayjs(), 'day')))
const visibleDays = computed(() => isHistoryExpanded.value
  ? days.value
  : days.value.filter(day => !day.date || !day.date.isBefore(dayjs(), 'day')))

function taskName(item: ProjectArrangementDisplayItem) {
  return item.top_level_task_name && item.top_level_task_name !== item.task_name
    ? `${item.top_level_task_name}·${item.task_name}`
    : item.task_name
}
function weekdayLabel(day: number) { return ['日', '一', '二', '三', '四', '五', '六'][day] }
function planTime(item: ProjectArrangementDisplayItem) {
  if (!item.plan_start || !item.plan_end) return item.expected_approval_at ? `预计 ${dayjs(item.expected_approval_at).format('HH:mm')}` : '未排程'
  return `${dayjs(item.plan_start).format('HH:mm')}–${dayjs(item.plan_end).format('HH:mm')}`
}
function actualTime(item: ProjectArrangementDisplayItem) {
  return projectArrangementActualText(item)
}
function dailyStatusLabel(item: ProjectArrangementDisplayItem) {
  if (item.dailyState === 'missed') return '未按计划执行'
  if (['running', 'continuing'].includes(item.dailyState)) return taskStatusLabel('running')
  if (item.dailyState === 'completed') return taskStatusLabel('completed')
  if (item.dailyState === 'pending') return taskStatusLabel('scheduled')
  return taskStatusLabel(item.task_status)
}
function dailyStatusColor(item: ProjectArrangementDisplayItem) {
  if (item.dailyState === 'missed') return '#dc2626'
  if (['running', 'continuing'].includes(item.dailyState)) return taskStatusColor('running')
  if (item.dailyState === 'completed') return taskStatusColor('completed')
  if (item.dailyState === 'pending') return taskStatusColor('scheduled')
  return taskStatusColor(item.task_status)
}
function formatDuration(start: string, end: string) {
  const hours = dayjs(end).diff(dayjs(start), 'minute') / 60
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
}
function instrumentText(item: ProjectArrangementDisplayItem) { return [item.instrument_code, item.instrument_name].filter(Boolean).join(' · ') || '无需仪器' }
</script>

<style scoped src="./ProjectArrangement.css"></style>
