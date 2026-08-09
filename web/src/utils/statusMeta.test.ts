import { describe, expect, it } from 'vitest'
import {
  getTaskStatusMeta,
  isTaskCompleted,
  taskStatusMatchesGroup,
} from './statusMeta'

describe('task status metadata', () => {
  it('uses one label and color for every task status consumer', () => {
    expect(getTaskStatusMeta('scheduled')).toMatchObject({
      label: '待执行',
      color: '#2563eb',
      group: 'pending',
    })
    expect(getTaskStatusMeta('blocked')).toMatchObject({
      label: '已阻塞',
      color: '#dc2626',
      group: 'active',
    })
  })

  it('centralizes tab grouping and terminal status checks', () => {
    expect(taskStatusMatchesGroup('paused', 'active')).toBe(true)
    expect(taskStatusMatchesGroup('completed', 'completed')).toBe(true)
    expect(isTaskCompleted('done')).toBe(true)
    expect(isTaskCompleted('running')).toBe(false)
  })

  it('keeps unknown statuses visible without treating them as completed', () => {
    expect(getTaskStatusMeta('custom_status').label).toBe('custom_status')
    expect(isTaskCompleted('custom_status')).toBe(false)
  })
})
