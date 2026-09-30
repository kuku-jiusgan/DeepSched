import { describe, expect, it } from 'vitest'
import { auditFieldLabel, formatAuditValue } from './auditLogFormatting'

describe('操作日志详情格式化', () => {
  it('递归格式化对象数组而不显示对象字符串', () => {
    const text = formatAuditValue([
      { 任务名称: '方法开发', 预计工时: 16, 需要仪器: true },
      { 任务名称: '报告撰写', 预计工时: null },
    ])

    expect(text).toContain('第 1 项：任务名称：方法开发')
    expect(text).toContain('预计工时：16')
    expect(text).toContain('需要仪器：是')
    expect(text).toContain('第 2 项：任务名称：报告撰写')
    expect(text).not.toContain('[object Object]')
  })

  it('保留中文业务字段并阻止英文实现键泄漏', () => {
    expect(auditFieldLabel('项目名称')).toBe('项目名称')
    expect(auditFieldLabel('client_name')).toBe('其他字段')
    expect(formatAuditValue({ client_name: '某客户', unknown_key: 0 })).toBe(
      '其他字段：某客户；其他字段（2）：0',
    )
  })

  it('统一格式化空值、布尔值和普通数组', () => {
    expect(formatAuditValue(null)).toBe('未设置')
    expect(formatAuditValue(false)).toBe('否')
    expect(formatAuditValue([])).toBe('无')
    expect(formatAuditValue(['方法开发', '报告撰写'])).toBe('方法开发、报告撰写')
  })
})
