import { h } from 'vue'
import { impactCard } from '@/pages/projects/impactCard'
import type {
  PauseSwitchFailure,
  PauseSwitchOverrun,
  PauseSwitchParty,
} from '@/types/pauseSwitchFailure'

function cell(value: string, className?: string) {
  return h('td', { class: className }, value)
}

function header(failure: PauseSwitchFailure) {
  return h('header', { class: 'schedule-failure-header' }, [
    h('strong', failure.summary),
    h('span', `切换时刻：${failure.switch_time}`),
  ])
}

function partyRow(role: string, party: PauseSwitchParty) {
  return h('tr', { key: role }, [
    cell(role, 'schedule-failure-name'),
    cell(party.task_name),
    cell(party.project_label),
    cell(party.assignee_name),
  ])
}

function switchSection(failure: PauseSwitchFailure) {
  return h('section', { class: 'schedule-failure-section' }, [
    h('h3', '切换内容'),
    h('div', { class: 'schedule-failure-table-scroll' }, [
      h('table', { class: 'schedule-failure-table' }, [
        h('thead', [h('tr', [
          h('th', ''), h('th', '任务'), h('th', '所属项目'), h('th', '负责人'),
        ])]),
        h('tbody', [
          partyRow('暂停', failure.source),
          partyRow('接替', failure.target),
        ]),
      ]),
    ]),
  ])
}

/** 失败原因：切换之后哪个项目做不完、是哪个任务顶出去的、要晚多少天。 */
function reasonSection(overruns: PauseSwitchOverrun[]) {
  return h('section', { class: 'schedule-failure-section' }, [
    h('h3', '失败原因'),
    h('div', { class: 'schedule-failure-table-scroll' }, [
      h('table', { class: 'schedule-failure-table' }, [
        h('thead', [h('tr', [
          h('th', '项目'), h('th', '最晚完工的任务'), h('th', '负责人'),
          h('th', '项目结题日期'), h('th', '预计完工'), h('th', '超出天数'),
        ])]),
        h('tbody', overruns.map(row => h('tr', { key: row.project_id }, [
          cell(row.project_label, 'schedule-failure-name'),
          cell(row.blocking_task_name),
          cell(row.blocking_task_assignee),
          cell(row.deadline),
          cell(row.projected_completion, 'schedule-failure-shortage'),
          cell(`${row.delay_days} 天`, 'schedule-failure-shortage'),
        ]))),
      ]),
    ]),
  ])
}

/** 调整方案：一条原因对应一条方案，就是把那个项目的结题日期延到哪一天。

    这里不再有"求解器已验证"的标记，因为方案不再是另外搜出来再回头验证的——
    它直接来自"把这次切换原样再跑一遍、只放开结题日期"那次求解的结果。 */
function planSection(overruns: PauseSwitchOverrun[]) {
  return h('section', { class: 'schedule-failure-section' }, [
    h('h3', '调整方案'),
    h('div', { class: 'schedule-failure-recommendations' }, overruns.map(row => impactCard(
      `${row.project_label} 延期 ${row.delay_days} 天`,
      [
        { label: '现结题日期', value: row.deadline },
        { label: '建议结题日期', value: row.suggested_deadline, tone: 'danger' },
        { label: '延期天数', value: `${row.delay_days} 天` },
      ],
    ))),
    overruns.length > 1
      ? h('div', { class: 'schedule-failure-empty' }, '以上项目需一并调整，只改其中一个仍然排不下。')
      : null,
  ])
}

/** 归结不到结题日期的两种收场：求解器证明了放开也排不下，或者压根没算出结论。

    没有结论时必须说没有结论。把一次超时说成"排程约束冲突"，或者随便挑个项目
    让人去延期，都是拿没验证过的判断冒充结论——这正是这次要改掉的毛病。 */
function plainContent(failure: PauseSwitchFailure) {
  const conflict = failure.kind === 'scheduling_conflict'
  return h('div', { class: 'schedule-failure-content' }, [
    header(failure),
    switchSection(failure),
    h('section', { class: 'schedule-failure-section' }, [
      h('h3', '失败原因'),
      h('div', { class: 'schedule-failure-plain' }, conflict
        ? '放开全部项目结题日期后，这次切换仍然排不下——卡住的不是结题日期，改项目日期解决不了。'
        : '未能在限定时间内判定这次切换的失败原因，因此不给出调整方案。'),
      failure.solver_message
        ? h('div', { class: 'schedule-failure-empty' }, `求解器原始信息：${failure.solver_message}`)
        : null,
    ]),
    h('div', { class: 'schedule-failure-action' }, conflict
      ? '请联系管理员排查排程约束。'
      : '请稍后重试；若反复出现请联系管理员。'),
  ])
}

function overrunContent(failure: PauseSwitchFailure) {
  return h('div', { class: 'schedule-failure-content' }, [
    header(failure),
    switchSection(failure),
    reasonSection(failure.overruns),
    planSection(failure.overruns),
    h('div', { class: 'schedule-failure-action' }, '调整项目结题日期后，请重新执行「暂停并切换」。'),
  ])
}

export function pauseSwitchFailureContent(failure: PauseSwitchFailure) {
  return failure.kind === 'project_deadline_overrun' && failure.overruns.length > 0
    ? overrunContent(failure)
    : plainContent(failure)
}
