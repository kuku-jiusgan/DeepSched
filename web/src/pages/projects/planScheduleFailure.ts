import { h } from 'vue'
import type {
  ProjectPlanApplyResult,
  ScheduleFailureDiagnostic,
  ScheduleFailureInstrument,
  ScheduleFailureOccupancy,
} from '@/types'

function formatHours(value: number) {
  return `${Number(value.toFixed(2))}h`
}

function cell(value: string, className?: string) {
  return h('td', { class: className }, value)
}

function instrumentTable(rows: ScheduleFailureInstrument[]) {
  return h('section', { class: 'schedule-failure-section' }, [
    h('h3', '仪器信息'),
    h('div', { class: 'schedule-failure-table-scroll' }, [
      h('table', { class: 'schedule-failure-table' }, [
        h('thead', [h('tr', [
          h('th', '仪器'), h('th', '总可用工时'), h('th', '已被使用'),
          h('th', '剩余可排工时'), h('th', '本次需求'), h('th', '缺口'),
        ])]),
        h('tbody', rows.map(row => h('tr', { key: row.instrument_id }, [
          cell(row.instrument_label, 'schedule-failure-name'),
          cell(formatHours(row.available_hours)),
          cell(formatHours(row.occupied_hours)),
          cell(formatHours(row.remaining_hours)),
          cell(formatHours(row.required_hours)),
          cell(formatHours(row.deficit_hours), row.deficit_hours > 0 ? 'schedule-failure-shortage' : undefined),
        ]))),
      ]),
    ]),
  ])
}

function occupancyTable(rows: ScheduleFailureOccupancy[]) {
  return h('section', { class: 'schedule-failure-section' }, [
    h('h3', '占用明细'),
    rows.length === 0
      ? h('div', { class: 'schedule-failure-empty' }, '当前时间窗内没有其他项目占用。')
      : h('div', { class: 'schedule-failure-table-scroll' }, [
          h('table', { class: 'schedule-failure-table' }, [
            h('thead', [h('tr', [
              h('th', '占用项目'), h('th', '仪器'), h('th', '仪器占用'),
              h('th', '人工占用'), h('th', '预测工时'), h('th', '合计'),
            ])]),
            h('tbody', rows.map(row => h('tr', { key: `${row.instrument_id}-${row.project_id}` }, [
              cell(row.project_label, 'schedule-failure-name'),
              cell(row.instrument_label),
              cell(formatHours(row.scheduled_hours)),
              cell(formatHours(row.bridged_hours)),
              cell(formatHours(row.forecast_hours)),
              cell(formatHours(row.total_hours)),
            ]))),
          ]),
        ]),
  ])
}

function recommendations(diagnostic: ScheduleFailureDiagnostic) {
  const rows = diagnostic.recommendations ?? []
  const isSearching = ['pending', 'running'].includes(diagnostic.recommendation_job?.status || '')
  return h('section', { class: 'schedule-failure-section schedule-failure-recommendations' }, [
    h('h3', '调整方案'),
    ...(rows.length
      ? rows.map((row, index) => h('div', { class: 'schedule-failure-recommendation', key: `${index}-${row.title}` }, [
          h('strong', `方案 ${index + 1} · ${row.title}`),
          h('p', row.description),
          h('span', { class: 'is-verified' }, '求解器已验证'),
        ]))
      : [h('div', { class: 'schedule-failure-empty' }, isSearching
          ? '正在通过完整排程约束计算可行调整方案，通常需要 1–2 分钟，结果会更新到上方“调整方案”区域。'
          : '当前搜索范围内没有能使排程成功的日期调整方案。')]),
  ])
}

function capacityContent(diagnostic: ScheduleFailureDiagnostic) {
  const deadline = diagnostic.deadline || diagnostic.groups[0]?.deadline || '未知'
  const days = diagnostic.days_remaining === undefined ? '' : `（距今 ${diagnostic.days_remaining} 天）`
  return h('div', { class: 'schedule-failure-content' }, [
    h('header', { class: 'schedule-failure-header' }, [
      h('strong', diagnostic.project_label || '当前项目'),
      h('span', `项目结题日：${deadline}${days}`),
    ]),
    instrumentTable(diagnostic.instruments ?? []),
    occupancyTable(diagnostic.occupancy ?? []),
    recommendations(diagnostic),
    h('div', { class: 'schedule-failure-action' }, '调整后，请重新点击“保存并开始排程”。'),
  ])
}

function constraintContent(diagnostic: ScheduleFailureDiagnostic) {
  const deadline = diagnostic.deadline || '未知'
  const window = diagnostic.window
  return h('div', { class: 'schedule-failure-content' }, [
    h('header', { class: 'schedule-failure-header' }, [
      h('strong', diagnostic.project_label || '当前项目'),
      h('span', `项目结题日：${deadline}`),
    ]),
    window
      ? h('section', { class: 'schedule-failure-section' }, [
          h('h3', '约束明细'),
          h('div', { class: 'schedule-failure-table-scroll' }, [
            h('table', { class: 'schedule-failure-table' }, [
              h('thead', [h('tr', [
                h('th', '任务'), h('th', '最早可开始'), h('th', '截止时间'),
                h('th', '所需工时'), h('th', '窗口跨度'),
              ])]),
              h('tbody', [h('tr', [
                cell(window.task_name, 'schedule-failure-name'),
                cell(window.earliest_start),
                cell(window.deadline || deadline),
                cell(formatHours(window.required_hours)),
                cell(formatHours(window.available_hours)),
              ])]),
            ]),
          ]),
        ])
      : h('div', { class: 'schedule-failure-plain schedule-failure-section' }, diagnostic.summary),
    h('div', { class: 'schedule-failure-action' }, window
      ? '请修正项目日期或调整任务工时后重新排程。'
      : '请检查前置关系、负责人可用时间、仪器连续可用时段和切换时间后重新排程。'),
  ])
}

export function scheduleFailureContent(result: ProjectPlanApplyResult) {
  const diagnostic = result.schedule_failure
  if (!diagnostic) {
    return h('div', { class: 'schedule-failure-content' }, [
      h('div', { class: 'schedule-failure-plain' }, result.message || '当前计划无法在已有排程中安排。'),
    ])
  }
  if (diagnostic.kind === 'instrument_capacity' && diagnostic.instruments) {
    return capacityContent(diagnostic)
  }
  if (diagnostic.kind === 'scheduling_constraints') {
    return constraintContent(diagnostic)
  }
  return h('div', { class: 'schedule-failure-content' }, [
    h('div', { class: 'schedule-failure-plain' }, `${diagnostic.summary}（截止日期：${diagnostic.deadline || '未知'}）`),
    h('div', { class: 'schedule-failure-action' }, '请检查前置关系、负责人可用时间、仪器连续可用时段和切换时间后重新排程。'),
  ])
}
