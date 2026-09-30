const ENGLISH_IMPLEMENTATION_KEY = /^[A-Za-z][A-Za-z0-9_.\-[\]]*$/

export function auditFieldLabel(key: string): string {
  return ENGLISH_IMPLEMENTATION_KEY.test(key) ? '其他字段' : key
}

export function formatAuditValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未设置'
  if (value === true) return '是'
  if (value === false) return '否'
  if (Array.isArray(value)) return formatArray(value)
  if (typeof value === 'object') return formatObject(value as Record<string, unknown>)
  return String(value)
}

function formatArray(value: unknown[]): string {
  if (!value.length) return '无'
  if (value.some(item => item !== null && typeof item === 'object')) {
    return value.map((item, index) => `第 ${index + 1} 项：${formatAuditValue(item)}`).join('；')
  }
  return value.map(formatAuditValue).join('、')
}

function formatObject(value: Record<string, unknown>): string {
  const labelCounts = new Map<string, number>()
  return Object.entries(value).map(([key, item]) => {
    const baseLabel = auditFieldLabel(key)
    const count = (labelCounts.get(baseLabel) || 0) + 1
    labelCounts.set(baseLabel, count)
    const label = count === 1 ? baseLabel : `${baseLabel}（${count}）`
    return `${label}：${formatAuditValue(item)}`
  }).join('；') || '无'
}
