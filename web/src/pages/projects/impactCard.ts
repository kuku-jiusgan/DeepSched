import { h } from 'vue'

export interface ImpactCardRow {
  label: string
  value: string
  tone?: 'danger' | 'safe'
}

/** 一个项目一张卡片，几项数据横排一行。

    插单确认弹窗和排程失败的调整方案讲的是同一件事——某个项目会被推到什么时候。
    两处曾经各写各的：插单是卡片，调整方案是一段用分号把几个项目连起来的话，
    关键数字埋在文字里。这里统一成同一个模板，再有别处需要也从这里取。 */
export function impactCard(title: string, rows: ImpactCardRow[]) {
  return h('div', { class: 'impact-card' }, [
    h('div', { class: 'impact-card-title' }, title),
    h('dl', { class: 'impact-card-rows' }, rows.map(row => h('div', { key: row.label }, [
      h('dt', row.label),
      h('dd', { class: row.tone ? `impact-card-${row.tone}` : undefined }, row.value),
    ]))),
  ])
}
