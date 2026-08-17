import { h } from 'vue'

export function scheduleFailureContent(rawMessage: string) {
  const marker = '。这不是系统故障，请按以下顺序检查：'
  const [summary, checklistText] = rawMessage.split(marker)
  if (!checklistText) return rawMessage

  const projectPattern = /【([^】]+)】项目时间：(.+?) 至 (.+?)，待排总工时约 ([\d.]+) 小时（其中仪器工时 ([\d.]+) 小时）/g
  const projects: Array<{ name: string; start: string; end: string; total: string; instrument: string }> = []
  let match: RegExpExecArray | null
  while ((match = projectPattern.exec(summary)) !== null) {
    projects.push({ name: match[1], start: match[2], end: match[3], total: match[4], instrument: match[5] })
  }
  const checks = checklistText.replace(/调整后请重新点击“保存并开始排程”。?$/, '').split('；').filter(Boolean)
  return h('div', { class: 'schedule-failure-content' }, [
    h('div', { class: 'schedule-failure-summary' }, summary.split('。')[0]),
    projects.length ? h('div', { class: 'schedule-failure-projects' }, [
      h('div', { class: 'schedule-failure-section-title' }, '项目概况'),
      ...projects.map(project => h('div', { class: 'schedule-failure-project' }, [
        h('strong', project.name),
        h('div', `时间：${project.start} 至 ${project.end}`),
        h('div', `待排工时：${project.total} 小时（仪器 ${project.instrument} 小时）`),
      ])),
    ]) : null,
    h('div', { class: 'schedule-failure-section-title' }, '请按顺序检查'),
    h('ol', { class: 'schedule-failure-checks' }, checks.map(check => h('li', check))),
    h('div', { class: 'schedule-failure-action' }, '调整后请重新点击“保存并开始排程”。'),
  ])
}
