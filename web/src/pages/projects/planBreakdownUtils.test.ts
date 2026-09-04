import { describe, expect, it } from 'vitest'
import type { Task } from '@/types'
import {
  addChildDisabledReason,
  canAddChildTask,
  localDraftDependsOnTask,
  taskTreeIds,
} from './planBreakdownUtils'

function task(overrides: Partial<Task>): Task {
  const base: Task = {
    id: 1,
    project_id: 1,
    name: '任务',
    task_type: 'FFKF_001',
    requires_instrument: true,
    requires_human: true,
    est_duration_hours: 8,
    switchover_hours: 0.5,
    priority_weight: 1,
    allow_split: false,
    status: 'pending',
    delay_status: 'not_delayed',
    schedule_dirty: false,
    schedule_lock_status: 'none',
    can_edit_schedule_fields: true,
    can_edit_basic_fields: true,
    can_edit_schedule_window: true,
    can_edit_resource_fields: true,
    predecessor_ids: [],
    instrument_ids: [],
    assignee_id: null,
    assignee_name: null,
    parent_id: null,
  }
  return { ...base, ...overrides }
}

describe('本地草稿关联保护', () => {
  it('识别草稿对已有任务的父级或前置引用', () => {
    const tasks = [
      task({ id: 10 }),
      task({ id: -1, is_local_draft: true, parent_id: 10 }),
      task({ id: -2, is_local_draft: true, predecessor_ids: [10] }),
    ]

    expect(localDraftDependsOnTask(tasks, 10)).toBe(true)
    expect(localDraftDependsOnTask(tasks, 99)).toBe(false)
  })

  it('收集删除任务的完整子树', () => {
    const tasks = [
      task({ id: 10 }),
      task({ id: 11, parent_id: 10 }),
      task({ id: -1, is_local_draft: true, parent_id: 11 }),
      task({ id: 20 }),
    ]

    expect(taskTreeIds(tasks, 10)).toEqual(new Set([10, 11, -1]))
  })
})

describe('已开工任务不能再挂子任务', () => {
  it('运行中和已完成的任务禁用添加子任务', () => {
    expect(canAddChildTask(task({ status: 'running' }))).toBe(false)
    expect(canAddChildTask(task({ status: 'completed' }))).toBe(false)
    expect(canAddChildTask(task({ status: 'done' }))).toBe(false)
    expect(addChildDisabledReason(task({ status: 'running' }))).toBe('任务已开始或已完成，不能再添加子任务')
  })

  it('待排和暂停的任务仍然可以挂子任务', () => {
    expect(canAddChildTask(task({ status: 'scheduled' }))).toBe(true)
    expect(canAddChildTask(task({ status: 'pending' }))).toBe(true)
    expect(canAddChildTask(task({ status: 'paused' }))).toBe(true)
    expect(addChildDisabledReason(task({ status: 'scheduled' }))).toBe('添加子任务')
  })
})
