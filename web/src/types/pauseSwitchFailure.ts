/** 「暂停并切换」失败的诊断结构。

    这条路以前复用项目计划排程的 ScheduleFailureDiagnostic：仪器工时表、占用明细
    表回答的是"项目排不下"，而暂停并切换只有一种失败——切换之后某个项目会超出与
    客户签的结题日期。原因和方案因此是一一对应的：会导致哪个项目延期几天，方案
    就是把那个项目的结题日期延后几天。 */

export interface PauseSwitchParty {
  task_name: string
  project_label: string
  assignee_name: string
}

export interface PauseSwitchOverrun {
  project_id: number
  project_label: string
  deadline: string
  projected_completion: string
  delay_days: number
  blocking_task_name: string
  blocking_task_assignee: string
  suggested_deadline: string
}

export interface PauseSwitchFailure {
  title: string
  kind: 'project_deadline_overrun' | 'scheduling_conflict' | 'undetermined'
  summary: string
  switch_time: string
  source: PauseSwitchParty
  target: PauseSwitchParty
  overruns: PauseSwitchOverrun[]
  /** 求解器的原话，只在没能归结到结题日期时有值，供排查用。 */
  solver_message?: string | null
}

export interface PauseSwitchFailureResult {
  message?: string
  pause_switch_failure?: PauseSwitchFailure | null
}
