import { h } from 'vue'
import { impactCard } from './impactCard'
import type {
  ScheduleFailureDiagnostic,
  ScheduleFailureInstrument,
  ScheduleFailureOccupancy,
  ScheduleFailureRecommendation,
  ScheduleFailureResult,
  ScheduleFailureWindow,
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

/** 一个方案里每个要改期的项目一张卡片。

    后端本来就按项目返回了结构化的 changes（原结题日、建议结题日、延期天数），
    以前却渲染成一句用分号连起来的话，几个项目挤在一行，跟插单确认弹窗改造前
    是同一个毛病。这里改成和插单一样的卡片。 */
function recommendationBody(row: ScheduleFailureRecommendation) {
  const changes = row.changes ?? []
  if (!changes.length) return [h('p', row.description)]
  // 每套方案只调整一个项目，方案之间互相独立——后端不再产出"几个项目一起调整"
  // 的组合方案，那种方案看不出每个项目为什么被牵进来，业务上也没法执行。
  return [
    ...changes.map(change => impactCard(change.project_label, [
      { label: '原结题日', value: change.original_deadline },
      { label: '建议结题日', value: change.suggested_deadline },
      { label: '延期天数', value: `${change.delay_days} 天`, tone: 'danger' },
    ])),
  ]
}

function recommendations(diagnostic: ScheduleFailureDiagnostic) {
  const rows = diagnostic.recommendations ?? []
  const isSearching = ['pending', 'running'].includes(diagnostic.recommendation_job?.status || '')
  return h('section', { class: 'schedule-failure-section schedule-failure-recommendations' }, [
    h('h3', '调整方案'),
    ...(rows.length
      ? rows.map((row, index) => h('div', { class: 'schedule-failure-recommendation', key: `${index}-${row.title}` }, [
          h('strong', `方案 ${index + 1} · ${row.title}`),
          ...recommendationBody(row),
          h('span', { class: 'is-verified' }, '求解器已验证'),
        ]))
      : [h('div', { class: 'schedule-failure-empty' }, isSearching
          ? '正在通过完整排程约束计算可行调整方案，通常需要 1–2 分钟，完成后显示在这里。'
          : '当前搜索范围内没有能使排程成功的日期调整方案。')]),
  ])
}

function failureHeader(diagnostic: ScheduleFailureDiagnostic, deadline: string) {
  const days = diagnostic.days_remaining === undefined ? '' : `（距今 ${diagnostic.days_remaining} 天）`
  return h('header', { class: 'schedule-failure-header' }, [
    h('strong', diagnostic.project_label || '当前项目'),
    h('span', `项目结题日：${deadline}${days}`),
  ])
}

function windowSection(window: ScheduleFailureWindow, deadline: string) {
  return h('section', { class: 'schedule-failure-section' }, [
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
}

function capacityContent(diagnostic: ScheduleFailureDiagnostic) {
  const deadline = diagnostic.deadline || diagnostic.groups[0]?.deadline || '未知'
  return h('div', { class: 'schedule-failure-content' }, [
    failureHeader(diagnostic, deadline),
    instrumentTable(diagnostic.instruments ?? []),
    occupancyTable(diagnostic.occupancy ?? []),
    recommendations(diagnostic),
    h('div', { class: 'schedule-failure-action' }, '调整后，请重新点击“保存并开始排程”。'),
  ])
}

function constraintContent(diagnostic: ScheduleFailureDiagnostic) {
  const deadline = diagnostic.deadline || '未知'
  const window = diagnostic.window
  // 排不进去不等于没余量。这类失败常见的形态是余量被别的项目切得零碎，或者
  // 让位之后那些项目自己越过结题日——两种都得看占用明细和调整方案才说得清。
  // 以前这里只画一句「受连续时间段、人员、前置依赖或仪器切换等约束限制」，
  // 后端算出来的仪器余量、占用明细和求解器验证过的调整方案全被丢掉了。
  return h('div', { class: 'schedule-failure-content' }, [
    failureHeader(diagnostic, deadline),
    window
      ? windowSection(window, deadline)
      : h('div', { class: 'schedule-failure-plain schedule-failure-section' }, diagnostic.summary),
    ...(diagnostic.instruments?.length ? [instrumentTable(diagnostic.instruments)] : []),
    ...(diagnostic.occupancy?.length ? [occupancyTable(diagnostic.occupancy)] : []),
    recommendations(diagnostic),
    h('div', { class: 'schedule-failure-action' }, window
      ? '请修正项目日期或调整任务工时后重新排程。'
      : '请检查前置关系、负责人可用时间、仪器连续可用时段和切换时间后重新排程。'),
  ])
}

export function scheduleFailureContent(result: ScheduleFailureResult) {
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
