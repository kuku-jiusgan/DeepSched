import type { Task } from '@/types'
import { allocateTemplateHours } from './planBreakdownUtils'

interface StandardPlanDraftOptions {
  projectId: number
  estimatedHours: number
  managerId: number
  groupNumber: number
  nextDraftId: number
  getAssigneeName: (id: number | null) => string | null
}

export function buildStandardPlanDrafts(options: StandardPlanDraftOptions) {
  let nextDraftId = options.nextDraftId
  const [methodHours, schemeHours, validationHours, reportHours] = allocateTemplateHours(options.estimatedHours)
  const group = draftTask(options, nextDraftId--, '标准计划' + options.groupNumber, 'group', false, 0, [], null)
  const method = draftTask(options, nextDraftId--, '方法开发', 'FFKF_001', true, methodHours, [], group.id)
  const scheme = draftTask(options, nextDraftId--, '方案撰写', 'QCFA_001', false, schemeHours, [method.id], group.id)
  const restriction = approvalGateDraft(options, nextDraftId--, scheme.id, group.id)
  const validation = draftTask(options, nextDraftId--, '方法验证', 'FFYZ_001', true, validationHours, [restriction.id], group.id)
  const report = draftTask(options, nextDraftId--, '报告撰写', 'ZXBG_001', false, reportHours, [validation.id], group.id)
  validation.status = 'waiting_external'; validation.schedule_dirty = false
  report.status = 'waiting_external'; report.schedule_dirty = false
  ;[method, scheme, restriction, validation, report].forEach((task, index) => { task.plan_order = index })
  return { group, tasks: [group, method, scheme, restriction, validation, report], nextDraftId }
}

function draftTask(
  options: StandardPlanDraftOptions,
  id: number,
  name: string,
  taskType: string,
  requiresInstrument: boolean,
  hours: number,
  predecessorIds: number[],
  parentId: number | null,
): Task {
  const isGroup = taskType === 'group'
  return {
    id,
    project_id: options.projectId,
    name,
    task_type: taskType,
    requires_instrument: requiresInstrument,
    requires_human: !isGroup,
    est_duration_hours: isGroup ? undefined : hours,
    switchover_hours: 0,
    status: 'pending',
    delay_status: 'not_delayed',
    schedule_dirty: !isGroup,
    schedule_lock_status: 'none',
    can_edit_schedule_fields: true,
    can_edit_basic_fields: true,
    can_edit_schedule_window: true,
    can_edit_resource_fields: true,
    priority_weight: 1,
    allow_split: false,
    instrument_ids: [],
    predecessor_ids: predecessorIds,
    assignee_id: isGroup ? null : options.managerId,
    assignee_name: isGroup ? null : options.getAssigneeName(options.managerId),
    parent_id: parentId,
    is_local_draft: true,
  }
}

function approvalGateDraft(
  options: StandardPlanDraftOptions,
  id: number,
  predecessorId: number,
  parentId: number,
): Task {
  return {
    ...draftTask(options, id, '方案签批', 'approval_gate', false, 0, [predecessorId], parentId),
    requires_human: false,
    est_duration_hours: undefined,
    status: 'waiting_external',
    schedule_dirty: false,
    assignee_id: options.managerId,
    assignee_name: options.getAssigneeName(options.managerId),
    is_external_gate: true,
    gate_status: 'not_submitted',
  }
}
