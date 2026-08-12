
<template>
  <div class="gantt-page" :class="{ 'is-fullscreen': isFullscreen }" @click="dismissTooltip">
    <div class="page-header">
      <h2>仪器甘特图</h2>
    </div>

    <div class="action-bar" :class="{ 'is-screen-toolbar': isFullscreen }">
      <a-button-group>
        <a-button :type="viewMode === 'day' ? 'primary' : 'default'" @click="switchView('day')">日</a-button>
        <a-button :type="viewMode === 'week' ? 'primary' : 'default'" @click="switchView('week')">周</a-button>
        <a-button :type="viewMode === 'month' ? 'primary' : 'default'" @click="switchView('month')">月</a-button>
      </a-button-group>
      <a-button @click="goPrev"><LeftOutlined /></a-button>
      <span class="period-label">{{ periodLabel }}</span>
      <a-button @click="goNext"><RightOutlined /></a-button>
      <a-button @click="goToday">今天</a-button>
      <a-button @click="toggleFullscreen"><component :is="isFullscreen ? FullscreenExitOutlined : FullscreenOutlined" /> 全屏</a-button>
      <span class="auto-scroll-control">
        <a-switch v-model:checked="autoScrollEnabled" size="small" />
        <span>全屏自动滚动</span>
      </span>
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />

    <div v-else-if="!instruments.length" style="padding: 80px; text-align: center; color: #94a3b8">
      暂无仪器数据，请先在「基础资源台账」中添加仪器并生成排程
    </div>

    <div
      v-else
      class="gantt-container"
      :class="{ 'is-week-view': viewMode === 'week' }"
      ref="containerRef"
    >
      <div class="gantt-left">
        <div class="gantt-header-cell">仪器</div>
        <div v-for="row in flatRows" :key="'l-' + row.inst.id + '-q' + row.quarter"
          class="gantt-left-row" :class="{ 'is-subrow': row.isSubrow, 'is-last': row.isLast || (viewMode === 'week' && !row.isSubrow), 'has-segment-rail': viewMode === 'week' && !row.isSubrow }"
          :style="getLeftRowStyle(row)">
          <template v-if="!row.isSubrow || viewMode !== 'week'">
            <div class="inst-meta-line">
              <span class="inst-code">{{ row.inst.code }}</span>
              <span class="inst-status-chip" :class="'inst-status-' + getInstrumentStatusMeta(row.inst.status).key">
                {{ getInstrumentStatusMeta(row.inst.status).label }}
              </span>
            </div>
            <div class="inst-name">{{ row.inst.name }}</div>
            <div class="inst-model" v-if="row.inst.model">{{ row.inst.model }}</div>
            <div v-if="viewMode === 'week'" class="segment-rail" aria-label="每日 8 小时分段">
              <span v-for="segment in WEEK_SEGMENT_COUNT" :key="segment" class="segment-rail-label">
                {{ getSegmentLabel(segment - 1) }}
              </span>
            </div>
          </template>
        </div>
      </div>

      <div class="gantt-right" ref="rightRef">
        <div class="gantt-timeline-header" :style="{ width: totalWidth + 'px' }">
          <div v-for="col in timeColumns" :key="col.key" class="gantt-col-header" :style="{ width: colWidth + 'px' }"
            :class="{ 'is-weekend': col.isWeekend, 'is-today': col.isToday, 'is-current': col.isCurrent }">
            <div class="col-label-primary">{{ col.label }}</div>
            <div v-if="col.subLabel" class="col-label-sub">{{ col.subLabel }}</div>
          </div>
        </div>
        <div class="gantt-timeline-body" :style="{ width: totalWidth + 'px' }">
          <div v-for="row in flatRows" :key="'r-' + row.inst.id + '-q' + row.quarter"
            class="gantt-entity-row" :class="{ 'is-subrow': row.isSubrow, 'is-last': row.isLast || (viewMode === 'week' && !row.isSubrow) }"
            :style="{ height: Math.max(12, rowHeight) + 'px' }">
            <div v-for="col in timeColumns" :key="col.key" class="gantt-grid-cell"
              :style="{ width: colWidth + 'px' }" :class="{ 'is-weekend': col.isWeekend, 'is-today': col.isToday, 'is-current': col.isCurrent }" />
            <div v-for="slot in getSlotsForQuarter(row.inst.id, row.quarter)" :key="slot.renderKey || slot.id"
              class="gantt-bar" :class="getBarClasses(slot, row.quarter)"
              :style="getBarStyle(slot, row.quarter)"
              role="button"
              tabindex="0"
              :aria-label="`查看 ${getBarProjectText(slot)} ${slot.task_name || '任务'}详情`"
              @mouseenter="e => showTooltip(slot, e)"
              @mouseleave="hideTooltip"
              @click.stop="e => toggleTooltip(slot, e)"
              @keydown.enter.stop="e => toggleTooltip(slot, e)"
              @keydown.space.prevent.stop="e => toggleTooltip(slot, e)">
              <span
                v-if="hasPausedExecution(slot)"
                class="bar-actual-execution"
                :style="getPausedExecutionStyle(slot, row.quarter)"
              />
              <span v-if="hasDelay(slot)" class="bar-delay-segment" :style="getDelaySegmentStyle(slot, row.quarter)">
                <span class="bar-delay-badge">延</span>
              </span>
              <span v-if="getWeekBarDisplay(slot, row.quarter).showIcon" class="bar-tag">
                <span class="bar-status-dot"></span>
                <component v-if="getTaskIcon(slot.task_type)" :is="getTaskIcon(slot.task_type)" />
              </span>
              <span v-else-if="getWeekBarDisplay(slot, row.quarter).showStatusMarker" class="bar-compact-marker"></span>
              <span v-if="getWeekBarDisplay(slot, row.quarter).showLabel" class="bar-label"
                :class="{ 'is-project-only': getWeekBarDisplay(slot, row.quarter).isProjectOnly }">
                <span v-if="getWeekBarDisplay(slot, row.quarter).projectText" class="bar-project">{{ getWeekBarDisplay(slot, row.quarter).projectText }}</span>
                <span v-if="getWeekBarDisplay(slot, row.quarter).taskText" class="bar-task">{{ getWeekBarDisplay(slot, row.quarter).taskText }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
      <div v-if="isFullscreen && autoScrollEnabled && !hasVerticalOverflow" class="auto-scroll-note">
        当前仪器已全部展示，无需滚动
      </div>
    </div>

    <div v-if="hoveredSlot" class="gantt-tooltip" :style="tooltipStyle">
      <div class="tooltip-title">{{ hoveredSlot.task_name }}</div>
      <div class="tooltip-row"><span>类型</span>{{ hoveredSlot.status === 'fault' ? '仪器故障' : getTaskTypeLabel(hoveredSlot.task_type) }}</div>
      <div class="tooltip-row"><span>项目</span>{{ getBarProjectText(hoveredSlot) }}</div>
      <div class="tooltip-row"><span>负责人</span>{{ hoveredSlot.assignee_name || '-' }}</div>
      <div class="tooltip-row"><span>开始</span>{{ dayjs(hoveredSlot.plan_start).format('MM-DD HH:mm') }}</div>
      <div class="tooltip-row"><span>结束</span>{{ dayjs(hoveredSlot.plan_end).format('MM-DD HH:mm') }}</div>
      <div class="tooltip-row"><span>状态</span>{{ statusLabel(hoveredSlot) }}</div>
      <div v-if="hoveredSlot.status === 'fault' && hoveredSlot.faultDescription" class="tooltip-row">
        <span>原因</span>{{ hoveredSlot.faultDescription }}
      </div>
      <div v-if="hasDelay(hoveredSlot)" class="tooltip-row is-delay"><span>延期</span>{{ getDelayText(hoveredSlot) }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useInstrumentGanttPage } from './instrumentGanttPage'

const {
  FullscreenExitOutlined, FullscreenOutlined, LeftOutlined, RightOutlined,
  WEEK_SEGMENT_COUNT, autoScrollEnabled, colWidth,
  containerRef, dayjs, dismissTooltip, flatRows, getBarClasses, getBarProjectText, getBarStyle, getDelaySegmentStyle,
  getDelayText, getInstrumentStatusMeta, getLeftRowStyle, getPausedExecutionStyle, getSegmentLabel, getSlotsForQuarter,
  getTaskIcon, getTaskTypeLabel, getWeekBarDisplay, goNext, goPrev, goToday, hasDelay,
  hasVerticalOverflow, hideTooltip, hoveredSlot, instruments, isCompactBar, isFullscreen, leftRef,
  hasPausedExecution, loading, periodLabel, rightRef, rowHeight, showTooltip, statusLabel, switchView, timeColumns,
  toggleFullscreen, toggleTooltip, tooltipStyle, totalWidth, viewMode,
} = useInstrumentGanttPage()
</script>

<style scoped src="./InstrumentGantt.css"></style>
