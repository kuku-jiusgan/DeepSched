/** 业务代码的统一中文兜底；服务端目录可在登录后通过 /audit-logs/translation-catalog 扩展。 */
const labels: Record<string, string> = {
  pending: '待处理', ready: '待处理', scheduled: '待执行', running: '运行中',
  paused: '已暂停', blocked: '已阻塞', interrupted: '已中断', done: '已完成',
  completed: '已完成', waiting_external: '等待外部签批', waiting_approval: '等待客户签批', success: '成功', failed: '失败',
  system: '系统', account: '账号与权限', project: '项目与计划', task: '任务管理',
  schedule: '排程与执行', resource: '仪器与资源', instrument: '仪器', time_slot: '任务排程时间段',
  schedule_queue_compacted: '压紧排程队列', schedule_generated: '生成排程',
  schedule_rescheduled: '重新排程',
}

export function translateCode(value: string | null | undefined, fallback = '未知') {
  if (!value) return fallback
  return labels[value] ?? value
}

export function translateObject(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(translateObject).join('、')
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${translateCode(key)}：${translateObject(item)}`).join('；')
  return String(value)
}
