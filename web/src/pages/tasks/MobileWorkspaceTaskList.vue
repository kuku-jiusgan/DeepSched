<template>
  <section class="mobile-task-list" :aria-label="title">
    <div v-if="tasks.length" class="mobile-task-stack">
      <article v-for="task in tasks" :key="task.task_id" class="mobile-task-card">
        <div class="mobile-task-card-head">
          <a-tag :color="statusColor(task.execution_status)">{{ statusLabel(task.execution_status) }}</a-tag>
          <span class="mobile-task-time">{{ planTime(task) }}</span>
        </div>
        <div class="mobile-task-project">{{ projectText(task) }}</div>
        <div class="mobile-task-name">{{ taskName(task) }}</div>
        <div class="mobile-task-actions">
          <a-button
            v-if="canStartWorkspaceTask(task)"
            v-operation="'start'"
            type="primary"
            class="mobile-task-primary"
            :loading="actingId === task.actionable_slot?.id"
            @click="emit('start', task)"
          >
            <PlayCircleOutlined /> {{ workspaceActionStatus(task) === 'paused' ? '恢复任务' : '开始任务' }}
          </a-button>
          <a-button
            v-else-if="canCompleteWorkspaceTask(task)"
            v-operation="'complete'"
            class="mobile-task-primary mobile-task-complete"
            :loading="actingId === task.actionable_slot?.id"
            @click="emit('complete', task)"
          >
            <CheckCircleOutlined /> 确认完成
          </a-button>
          <span v-else class="mobile-task-readonly">{{ task.actionable_slot ? '当前无需操作' : '未排程' }}</span>
          <a-dropdown v-if="canPauseWorkspaceTask(task)" trigger="click">
            <a-button class="mobile-task-more" aria-label="更多任务操作"><MoreOutlined /></a-button>
            <template #overlay>
              <a-menu><a-menu-item key="pause" @click="emit('pause', task)"><PauseCircleOutlined /> 暂停任务</a-menu-item></a-menu>
            </template>
          </a-dropdown>
        </div>
        <a-collapse ghost class="mobile-task-details">
          <a-collapse-panel key="details" header="查看任务详情">
            <dl>
              <div><dt>仪器</dt><dd>{{ instrumentText(task) }}</dd></div>
              <div><dt>负责人</dt><dd>{{ task.assignee_name || '未指定' }}</dd></div>
              <div><dt>计划时间</dt><dd>{{ planTime(task) }}</dd></div>
              <div><dt>实际时间</dt><dd>{{ actualTime(task) }}</dd></div>
              <div><dt>预计工时</dt><dd>{{ hoursText(task.est_duration_hours) }}</dd></div>
              <div v-if="task.actual_duration_hours !== undefined"><dt>实际工时</dt><dd>{{ hoursText(task.actual_duration_hours) }}</dd></div>
            </dl>
          </a-collapse-panel>
        </a-collapse>
      </article>
    </div>
    <a-empty v-else :description="emptyText" />
  </section>
</template>

<script setup lang="ts">
import { CheckCircleOutlined, MoreOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import type { WorkspaceTask } from '@/domains/tasks/workspaceTask'
import { canCompleteWorkspaceTask, canPauseWorkspaceTask, canStartWorkspaceTask, workspaceActionStatus } from '@/domains/tasks/workspaceTask'
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'

interface Props {
  tasks: WorkspaceTask[]
  title: string
  emptyText: string
  actingId: number | null
}

defineProps<Props>()
const emit = defineEmits<{ start: [task: WorkspaceTask]; complete: [task: WorkspaceTask]; pause: [task: WorkspaceTask] }>()

function statusColor(status: string) { return taskStatusColor(status) }
function statusLabel(status: string) { return taskStatusLabel(status) }
function taskName(task: WorkspaceTask) {
  return task.top_level_task_name && task.top_level_task_name !== task.task_name
    ? `${task.top_level_task_name} · ${task.task_name || '未命名任务'}`
    : task.task_name || '未命名任务'
}
function projectText(task: WorkspaceTask) {
  return [task.project_code, task.project_name].filter(Boolean).join(' · ') || '未关联项目'
}
function instrumentText(task: WorkspaceTask) {
  const slot = task.actionable_slot
  return [slot?.instrument_code, slot?.instrument_name].filter(Boolean).join(' · ') || '未指定仪器'
}
function dateTime(value: string | null) { return value ? dayjs(value).format('MM-DD HH:mm') : '未记录' }
function planTime(task: WorkspaceTask) { return `${dateTime(task.task_window.start)} 至 ${dateTime(task.task_window.end)}` }
function actualTime(task: WorkspaceTask) { return `${dateTime(task.actual_window.start)} 至 ${dateTime(task.actual_window.end)}` }
function hoursText(value: number | null | undefined) { return value === null || value === undefined ? '未记录' : `${value} 小时` }
</script>

<style scoped>
.mobile-task-list { padding-top: 4px; }
.mobile-task-stack { display: flex; flex-direction: column; gap: 10px; }
.mobile-task-card { padding: 14px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; }
.mobile-task-card-head, .mobile-task-actions { display: flex; align-items: center; gap: 8px; }
.mobile-task-card-head { justify-content: space-between; }
.mobile-task-time { min-width: 0; overflow: hidden; color: var(--color-text-secondary); font-size: 12px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.mobile-task-project { margin-top: 12px; color: var(--color-accent); font-size: 12px; line-height: 1.5; }
.mobile-task-name { margin-top: 3px; color: var(--color-text-primary); font-size: 16px; font-weight: 600; line-height: 1.5; }
.mobile-task-actions { margin-top: 14px; }
.mobile-task-primary.ant-btn { min-height: 44px; flex: 1; }
.mobile-task-complete.ant-btn { color: #fff; background: var(--color-success); border-color: var(--color-success); }
.mobile-task-more.ant-btn { width: 44px; height: 44px; padding: 0; }
.mobile-task-readonly { color: var(--color-text-secondary); font-size: 13px; }
.mobile-task-details { margin-top: 8px; }
.mobile-task-details :deep(.ant-collapse-header) { min-height: 40px; padding: 8px 0 !important; color: var(--color-text-secondary) !important; font-size: 13px; }
.mobile-task-details dl { display: grid; gap: 8px; margin: 0; }
.mobile-task-details dl div { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; }
.mobile-task-details dt { color: var(--color-text-tertiary); }
.mobile-task-details dd { margin: 0; color: var(--color-text-primary); overflow-wrap: anywhere; }
</style>
