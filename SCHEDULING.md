# 系统排程逻辑

> 最后核对：2026-09-11；代码基线：`3fda302`。
>
> 本文说明当前系统中会触发排程的业务动作、CP-SAT 求解规则、时间槽落库、失败诊断以及已知旁路。它是实现导览，不替代页面操作手册。

## 文档口径

本文使用三种标记：

- **业务规则**：已经确认的业务口径，例如项目结题日期是合同硬边界。
- **当前实现**：以生产代码和现有测试为依据的真实行为。
- **已知差异**：配置未接线、旁路、重复算法或前后端不一致。

证据优先级为：生产代码 → 现有测试（实现证据，不代表会自动执行）→ 业务培训材料 → 产品或界面原型。`PRODUCT.md` 用于说明产品定位，[培训大纲](docs/基因毒组系统运行规范培训PPT大纲.md)用于说明业务操作规范；两者都不能单独证明某项逻辑已经实现。文中引用以相对路径和稳定的类、函数名为主，不写易漂移的行号。

需要先明确一个边界：系统以 [SchedulerService](server/app/services/scheduler.py) 编排的 OR-Tools CP-SAT 为正式排程主链，但仍存在直接铺槽、手工改单槽等旁路。因此不能把“所有排程变更都经过同一求解器、epoch 和统一校验”当作当前事实。

## 1. 一页结论：不可误读的业务规则

| 主题 | 业务规则 | 当前实现 | 状态 |
| --- | --- | --- | --- |
| 项目结题日期 | 是与客户约定的合同日期，排不下必须失败 | `project_window` 把项目开始、结题时间作为正式任务硬边界 | 一致 |
| 未签批下游 | 计入项目能否按期完成的测算，签批前不占具体时间轴 | 按真实资源和依赖参与临时求解，落库时跳过未签批下游 | 一致 |
| 暂停/中断任务 | 位置可重排；状态和已经完成的有效工时必须保留 | 会重新进入求解，落库时保留 `paused/interrupted` 状态并只排剩余工时 | 一致 |
| 暂停切换 | 接替任务必须第一个开始 | 场景传入 `first_start_task_id` 硬约束 | 一致 |
| 切换完成 | 接替任务完成后不自动恢复原任务 | 原任务保持暂停，继续由人手动操作 | 一致 |
| 仪器故障 | 按最高优先级插单；维修完成后恢复原任务 | 优先走资源闭包 CP-SAT；无法无损重建时存在直接铺槽 fallback | 部分一致 |
| 连续后续 | 只要求先后，中间允许其他任务 | 依赖是 finish-to-start；空档只进入软惩罚 | 一致 |
| 桥接占机 | 按同一仪器队列判定，可跨项目 | 求解期与落库后均有桥接识别，但存在两套算法 | 部分一致 |
| 非工作时间执行 | 原始记录保留，但不计有效工时和进度 | 执行段保留；已执行分钟按工作日历累计 | 一致 |
| 项目优先级 | 用于业务上的先后取舍 | 主要影响候选移动和软目标，不是全局硬顺序 | 部分一致 |
| 排程失败 | 一次返回当前可计算出的完整诊断 | 项目排程最完整；全局重排等入口的展示口径尚未统一 | 部分一致 |
| 旧排程保护 | 新方案正式可用前不能破坏旧方案 | 项目计划主链先求解后软作废；局部重排仍有先提交删除的旁路 | 部分一致 |

上述“部分一致”不是业务规则可以放宽，而是当前实现需要维护者特别留意的边界；详见“已知旁路、配置脱节与重复实现”。

## 2. 核心概念与状态

### 2.1 领域对象

- **项目（Project）**：提供项目优先级、开始日期和合同结题日期。
- **任务（Task）**：真正进入求解的是叶子任务；父任务用于计划分组、工时汇总和依赖展开。
- **前置依赖（TaskDependency）**：当前核心语义是 finish-to-start，即后续任务不能早于前置任务完成。
- **时间槽（TimeSlot）**：任务在某段有效工作时间对人员、仪器或二者的计划占用。一个任务可因跨夜、跨休息日或允许拆分而有多个槽。
- **执行段（TaskExecutionSegment）**：保留任务真实开始、暂停、继续和结束记录；排程进度只累计有效工作日历内的分钟。
- **方案签批门**：本身不占人员和仪器；签批前下游工时参与完工风险测算，但没有正式时间槽。
- **桥接占机**：非仪器任务夹在同一负责人、同一仪器队列的前后仪器任务之间时，该人工任务期间仪器仍被视为占用。
- **临时队列依赖**：插单、暂停切换、故障、延期等场景为了本次求解临时增加的顺序，不写回业务依赖表。

主要模型见 [models.py](server/app/models/models.py)，求解值对象见 [planning_problem.py](server/app/services/planning_problem.py)。

### 2.2 三组容易混淆的状态

1. **任务状态**描述业务执行进度，例如 `pending`、`scheduled`、`running`、`paused`、`interrupted`、`completed`。
2. **时间槽层级**描述排程确定性：
   - `frozen`：冻结期内，按正式主链视为固定占用；
   - `confirmed`：确认期内；
   - `forecast`：更远期预测。
3. **时间槽生命周期**描述一条历史记录是否仍代表现行计划：
   - `active`：现行时间槽；
   - `superseded`：已被新排程取代，保留历史而不是物理删除。

`running/paused/interrupted` 是执行状态，`frozen/confirmed/forecast` 是时间层级，`active/superseded` 是记录生命周期，三者不能互相替代。

### 2.3 排程运行和并发标识

- `schedule_run_id` 关联同一次成功写回创建的时间槽、槽变更日志、日历快照和桥接预留；当前没有独立的统一 `ScheduleRun` 主表。
- **epoch** 是全局排程版本。求解装载世界时记住版本，写回时通过一条带版本条件的原子更新占用下一版本，旧方案不能在世界已经变化后落地。
- **preview token** 是插单或跨项目影响预览的指纹；确认前若任务或排程已变化，必须重新预览。
- **排程请求队列**保存锁忙时可延后执行的项目计划、签批等请求；它与 CP-SAT 求解队列不是同一个概念。

## 3. 触发排程的业务入口

| 用户动作 | 主要入口与服务 | 预览/确认 | 锁忙及结果 |
| --- | --- | --- | --- |
| 仅保存项目计划 | 项目计划拆解；`project_plan_draft_service` | 不求解 | 标记待重新排程，旧甘特不变 |
| 保存并排程 | [project_plan_apply_service.py](server/app/services/project_plan_apply_service.py) | 跨项目移动时返回预览令牌 | 无影响则直接应用；锁忙可入请求队列 |
| 新建/编辑检测任务 | [detection_task_service.py](server/app/services/detection_task_service.py) | 可能要求确认跨项目影响 | 保存动作与排程结果绑定 |
| 独立插单 | [schedule_insert_service.py](server/app/services/schedule_insert_service.py) | 先计算影响，再确认 | 临时队列顺序不改业务前置关系 |
| 局部/项目/全局重排 | [schedule_reschedule_service.py](server/app/services/schedule_reschedule_service.py) | 全局页面二次确认 | 三种策略的事务语义不同 |
| 提交预计签批时间 | `approval_gate_service.submit_approval_gate` | 生成交付风险预测 | 不创建签批后正式槽 |
| 正式签批 | [approval_gate_schedule_service.py](server/app/services/approval_gate_schedule_service.py) | 必要时处理跨项目影响 | 签批与下游正式排程处于同一事务；失败回滚签批 |
| 开始/完成任务 | `task_execution_service`、[schedule_completion_service.py](server/app/services/schedule_completion_service.py) | 提前完成可选择释放资源 | 释放后重排资源队列 |
| 报告延期 | [schedule_delay_service.py](server/app/services/schedule_delay_service.py) | 无 | 优先走 CP-SAT；特定状态下 fallback |
| 暂停并切换 | `task_pause_service`、[task_pause_solver_service.py](server/app/services/task_pause_solver_service.py) | 选择接替任务 | 接替任务硬性第一开始；失败整体回滚 |
| 仪器故障/维修完成 | [instrument_fault_schedule_service.py](server/app/services/instrument_fault_schedule_service.py) | 展示受影响任务 | 优先资源闭包求解，无法无损重建时 fallback |
| 夜间运行 | [schedule_night_run_service.py](server/app/services/schedule_night_run_service.py) | 指定时段 | 单独登记特殊槽与原始记录，不等同于完整重排 |

API 路由集中在 [schedules.py](server/app/api/schedules.py)、[project_plan_schedules.py](server/app/api/project_plan_schedules.py)、[detection_tasks.py](server/app/api/detection_tasks.py) 和 [approval_gates.py](server/app/api/approval_gates.py)。

### 3.1 项目计划应用的关键步骤

`apply_project_plan` 先校验项目预计工时和必选仪器，选出新增、待排或标记为待重排的叶子任务，再扩展同资源上允许移动的其他项目任务：

1. 没有其他项目受影响：直接执行一次权威重排。
2. 有跨项目影响：在数据库 savepoint 内完整求解一次。
3. 没有任务被移动：保留该结果并提交。
4. 有任务被移动：回滚预览，返回影响明细和 preview token。
5. 用户确认后重新校验指纹，再执行正式求解。

需要释放的旧槽以 `released_slot_ids` 传给求解器，求解前不删除；只有成功计划写回时才作废。这是项目计划主链与部分遗留重排入口的重要区别。

### 3.2 锁忙与持久化请求队列

`SchedulerService.generate` 受进程内可重入锁保护，抢不到锁时立即返回“正在处理中”，不会等待数据库锁直至超时。项目计划、保存并排程和签批等部分入口会将请求写入 [schedule_request_service.py](server/app/services/schedule_request_service.py)，由 [schedule_request_worker.py](server/app/services/schedule_request_worker.py) 重放；相同项目和请求类型的活动请求会去重。

当前锁是单进程锁；若服务改为多进程或多实例，必须替换为数据库租约或分布式锁。

## 4. CP-SAT 权威主链

```text
业务/API 入口
→ 确定受影响任务、资源闭包、临时顺序和可释放槽
→ SchedulerService.generate（全局互斥）
→ build_planning_problem（时间原点、视界、规则、日历、仪器、epoch）
→ 装载叶子任务、固定槽、维护/故障窗口和桥接预留
→ 前置校验与未签批下游处理
→ 构造任务变量、资源约束、依赖与软惩罚
→ add_scheduler_objective
→ CP-SAT Solve
├─ 不可行/未在时限内找到解：业务诊断与结题日建议
└─ OPTIMAL/FEASIBLE：构造纯值 SchedulePlan
   → 原子占用 epoch
   → 记录日历与规则快照
   → soft supersede 旧槽
   → 创建新槽和槽变更日志
   → 更新任务状态并重建桥接预留
   → 校验人员、仪器、业务依赖、临时依赖和桥接一致性
   → 校验通过后发送通知
   → 由调用层提交事务
```

主编排器是 [scheduler.py](server/app/services/scheduler.py) 中的 `SchedulerService`。任务筛选在 [scheduler_data.py](server/app/services/scheduler_data.py)，求解快照由 [planning_problem.py](server/app/services/planning_problem.py) 构造。核心变量与约束分布在：

- [scheduler_task_variables.py](server/app/services/scheduler_task_variables.py)：任务开始、结束、仪器选择及项目时间窗；
- [scheduler_split_tasks.py](server/app/services/scheduler_split_tasks.py)：可拆分任务的离散时间单元；
- [scheduler_fixed_slots.py](server/app/services/scheduler_fixed_slots.py)：现有人员/仪器占用、冻结与运行中槽；
- [scheduler_soft_constraints.py](server/app/services/scheduler_soft_constraints.py)：依赖、第一开始、里程碑、稳定性等；
- [scheduler_cross_project_setup.py](server/app/services/scheduler_cross_project_setup.py)：跨项目切换间隔；
- [scheduler_instrument_bridging.py](server/app/services/scheduler_instrument_bridging.py)：人工任务桥接占机；
- [scheduler_objective.py](server/app/services/scheduler_objective.py)：加权目标函数。

`feasibility_only=True` 只验证候选条件是否能找到解，不创建时间槽、桥接记录或通知，主要用于结题日建议。正式求解默认时限为 30 秒、4 个搜索 worker、固定随机种子 1；`OPTIMAL` 或 `FEASIBLE` 才能写回，`INFEASIBLE` 和 `UNKNOWN` 等其他状态都进入失败路径。

求解输入正在向“一次装载、之后纯内存”的 `PlanningProblem` 收拢；当前时间原点、视界、规则、日历和仪器已在其中，部分任务关联和诊断数据仍会在后续读取数据库，不能把迁移中的目标写成已经完全实现。

## 5. 时间轴、工时与任务拆分

### 5.1 当前时间单位和视界

[scheduler_helpers.py](server/app/services/scheduler_helpers.py) 当前硬编码：

- `TIME_UNIT_MINUTES = 30`；
- `HORIZON_DAYS = 90`；
- 默认人员及非仪器任务工作时段为 08:30–20:00。

求解起点向上对齐到 30 分钟边界，默认向后规划 90 天；场景可显式收窄起止范围。规则管理中的“时间粒度”和“规划窗口”目前没有驱动这两个常量。

### 5.2 剩余工作量

正式求解处理的是任务剩余有效工时，而不是从头重排。口径由 [scheduler_task_duration.py](server/app/services/scheduler_task_duration.py) 等服务计算：

```text
计划工作量
= 预计工时
+ 追加计划工时
+ 任务自身切换准备时间

剩余工作量
= 计划工作量
- 已执行有效工作分钟
```

预计工时和任务自身切换时间分别向上取整到 30 分钟单元；零切换时间不额外产生单元。任务自身 `switchover_hours` 属于任务工作量，和不同项目在同一仪器间的 `cross_project_setup.setup_hours` 是两件事。

实际执行段按原始时间完整保留，但已执行分钟只累计工作日历和有效工作时段内的部分。因此夜间、休息日等非工作时间的执行事实可审计，却不增加排程进度。

### 5.3 普通任务与可拆分任务

**普通任务（`allow_split=false`）**的开始到结束可以跨夜间、周末或休息日，但跨度内必须恰好包含所需的有效工作单元。写回时 [schedule_action_plan.py](server/app/services/schedule_action_plan.py) 只为有效工作单元创建槽，所以同一任务可能形成跨多天的多个时间槽。

**可拆分任务（`allow_split=true`）**在同一台被选中的仪器上选择恰好所需数量的有效 30 分钟单元：

- 不能跨多台仪器拆分；
- 当前没有最小连续长度硬约束；
- 离散单元写回时只合并相邻片段；
- 目标函数通过最小化任务跨度软性鼓励片段靠拢，但不保证连续。

需要人员的可拆分任务还会占用其最早到最晚片段形成的人员时间包络，这可能比仪器实际离散占用更宽。

### 5.4 工作日历和资源时间

- 人员及不需要仪器的任务使用全局工作时段。
- 仪器任务使用每台仪器自己的有效开始、结束时间。
- 周末、节假日、调休按系统日历和规则开关判断。
- 维护窗口和故障窗口既从有效工时中扣除，也注册为仪器不可重叠区间。
- 没有预计修复时间的仪器故障会阻塞该仪器在当前求解视界内的可用时间。
- 人工修改日历会把受影响任务标记为待重排，不会自动立即重排。

实现见 [scheduler_working_calendar.py](server/app/services/scheduler_working_calendar.py)、[instrument_working_time_service.py](server/app/services/instrument_working_time_service.py) 和 [calendar_service.py](server/app/services/calendar_service.py)。

## 6. 硬约束目录

| 约束 | 正式排程语义 | 实现依据与边界 |
| --- | --- | --- |
| 项目时间窗 | 任务开始不得早于项目开始，任务结束不得晚于项目结题日 | [scheduler_task_variables.py](server/app/services/scheduler_task_variables.py)；结题日归一到当天 23:59:59。没有结题日时以求解视界为上界 |
| 显式仪器 | 任务指定了 `instrument_ids` 时，只能从这些仪器中选择 | [scheduler_helpers.py](server/app/services/scheduler_helpers.py)；不受能力匹配开关影响 |
| 能力匹配 | 未指定具体仪器且任务有能力要求时，候选仪器须包含全部要求标签 | 能力匹配规则开启时生效；匹配为精确标签对 |
| 唯一仪器分配 | 每个需要仪器的任务必须且只能选择一台兼容仪器 | 求解模型无条件 `AddExactlyOne`；管理规则被锁定 |
| 仪器不重叠 | 同一仪器上的任务、固定槽、维护和故障窗口不能重叠 | `non_overlap` 规则控制仪器 `AddNoOverlap` |
| 人员不重叠 | 同一负责人承担的人员任务和固定任务不能重叠 | 无独立开关；每个任务当前只有一个负责人 |
| 有效工作时间 | 所需工作量必须落在人员或仪器对应的有效工作单元 | 物理跨度可跨非工作时间，但非工作单元不计工作量 |
| 前置依赖 | 后续任务开始不得早于前置任务结束 | [scheduler_soft_constraints.py](server/app/services/scheduler_soft_constraints.py)；父任务依赖展开到叶子任务，集合外前置取实际/计划结束常量 |
| 临时队列依赖 | 插单、暂停切换、延期、故障等场景可为本次求解增加硬顺序 | 参与写后校验，但通常不写入业务依赖表 |
| 跨项目切换 | 同一仪器在不同项目任务之间预留准备时间 | [scheduler_cross_project_setup.py](server/app/services/scheduler_cross_project_setup.py)；默认 0.5 小时，可由约束规则控制 |
| 固定与保护槽 | 冻结槽及已有真实开始、尚未结束的槽占用既有资源 | [scheduler_fixed_slots.py](server/app/services/scheduler_fixed_slots.py)；真正运行中的事实段不可被重排覆盖 |
| 桥接占机 | 人工任务被同负责人、同一兼容仪器的前后仪器任务夹住时，人工期间仪器保持占用 | [scheduler_instrument_bridging.py](server/app/services/scheduler_instrument_bridging.py)；落库后另有桥接重建算法 |
| 未签批下游 | 签批后任务按真实资源和依赖测算，签批前不落正式槽 | [approval_gate_graph_service.py](server/app/services/approval_gate_graph_service.py)、[schedule_action_plan.py](server/app/services/schedule_action_plan.py)；临时求解位置不形成正式资源预留 |
| 暂停切换第一开始 | 接替任务不得晚于受影响闭包内其他任务开始 | 场景通过 `first_start_task_id` 建模；失败时不降级为普通优先级 |
| 保留执行状态 | 暂停/中断任务可移动，但新槽与任务继续保持暂停/中断状态 | 已完成有效工时从剩余工作量扣除，不从头排 |

### 6.1 合同结题日与里程碑不是一回事

**业务规则**：项目结题日期是合同日期，正式排程装不下时必须失败，由负责人修改项目日期或减少工时后重新排程，不能只给警告仍把时间槽写到合同日期之后。

**当前实现**：项目 `end_date` 是变量硬上界；单任务最早可开始时间加剩余工时已经超过项目结束时，会在建模阶段直接失败。里程碑 `due_date` 则产生可被目标函数惩罚的逾期变量，是软目标。

诊断和结题日建议可以临时覆盖候选项目结束日期做可行性探测，但探测结果不自动修改项目，更不改变正式排程的合同硬边界。

### 6.2 依赖只保证先后，不保证紧接

业务依赖的硬语义只有“后继开始不早于前置结束”。目标函数还会惩罚部分依赖之间的空档，但这是软偏好。因此“方法开发 → 方案撰写”等连续后续中间允许插入其他任务，不应通过把空档强制为零来实现。

### 6.3 暂停任务可移动

`paused` 和 `interrupted` 任务会重新进入模型。排程必须保住的是执行状态、已经完成的有效工时和真实执行段，而不是原时间槽位置。真正运行中的事实段以及冻结/固定占用才受到位置保护。

## 7. 目标函数与优先级

[scheduler_objective.py](server/app/services/scheduler_objective.py) 当前使用一个硬编码加权和：

```text
尽早开始惩罚 × 100000
+ 原排程稳定性偏差 × 10000
+（加权里程碑逾期 + 最大完工时间）× 1000
+ 项目优先完工项 × 10
+ 同父任务最晚完工项 × 同级任务权重
+ 依赖空档 × 500
+ 任务物理跨度 × 1
+ 跨项目同仪器切换项 × 500
+ 项目使用仪器数量 × 30
```

含义和边界：

- 这是加权和，不是严格的多阶段词典序优化；大量低层项理论上仍可能与较高层项权衡。
- 默认会鼓励本次任务尽早开始，减少资源释放后的无解释空档。
- 稳定性惩罚只在场景明确提供原排程与稳定任务集合时发挥作用。
- 里程碑逾期再乘任务 `priority_weight × 10`；合同结题日不在逾期目标里，因为它已是硬约束。
- 项目优先完工项按结题日、项目优先级、创建时间和任务编号形成稳定顺序；它只产生软倾向。
- 同父任务靠拢、依赖空档、跨度、跨项目切换和仪器分散都不是硬规则。

项目优先级还用于筛选哪些低优先级任务可以为插单移动。系统不存在“所有高优先级项目的所有任务必须排在所有低优先级项目之前”的全局硬规则；需要绝对顺序的场景通过临时队列依赖建模。

## 8. 签批与动态事件

### 8.1 方案签批

未签批方案的下游任务需要同时满足两个看似相反的要求：

1. **必须计入能否按合同日期完工的测算**，否则签批前会虚假显示项目健康；
2. **不能占用具体人员、仪器和时间槽**，因为实际签批时间尚未发生。

当前主链通过 [approval_gate_graph_service.py](server/app/services/approval_gate_graph_service.py) 标记未签批下游，保留这些任务参与人员、仪器、工作日历和前置依赖建模，由 [schedule_action_plan.py](server/app/services/schedule_action_plan.py) 在落库时跳过它们。不同资源上的分支可以并行，共享资源的分支仍受容量约束；不再把整个项目的后续工时串行相加、统一提前所有任务的完工上界。

未提供预计签批时间时，测算不额外假设等待时长；提供了预计时间时，正常求解将其作为下游开始下界，立即签批探测忽略该预计时间。临时解不会改变等待状态、生成正式下游时间槽或预留桥接占用。正式签批由 [approval_gate_schedule_service.py](server/app/services/approval_gate_schedule_service.py) 在同一事务中创建或激活下游任务并执行正式排程。正式求解失败时，回滚试排改动和签批状态，只保留可重试的失败信息。

`project_completion_projection_service.py` 还提供按依赖和工作日历向前推演的完工预测；它不等价于考虑全局资源竞争的 CP-SAT 权威解，只能用于诊断或外围预测。

### 8.2 开始、完成和提前释放

开始任务会建立真实执行段，并使正在执行的槽成为受保护事实。完成任务会收口执行段和时间槽：

- 正常完成可保持后续排程不变；
- 提前完成并选择释放资源时，[schedule_early_completion_replan_service.py](server/app/services/schedule_early_completion_replan_service.py) 以释放时刻为边界，对同仪器、同负责人及依赖闭包运行 CP-SAT，使后续任务可以前移；
- 如果刚完成的是暂停切换的接替任务，原任务仍保持暂停，不会自动继续。

### 8.3 延期

[schedule_delay_service.py](server/app/services/schedule_delay_service.py) 记录追加工作量和原因，并扩展同仪器、同负责人及依赖任务形成的资源闭包：

- 能用统一模型无损重建时，调用 `replan_resource_closure`，以最早开始边界和剩余工时重新求解；
- 任务含冻结槽、实际执行历史或其他无法完整重建的状态时，可能退回按工作日历直接向后铺槽；
- 无论走哪条路径，计划结束都不得越过项目合同结题日，否则整次延期处理失败。

因此“延期会影响后续排程”是业务事实，但当前并非每次延期都经过相同的 epoch、`SchedulePlan` 和写后校验。

### 8.4 暂停并切换

暂停动作首先截断源任务的真实执行段，保留已经完成的有效工时，并要求用户选择接替任务。随后 [task_pause_solver_service.py](server/app/services/task_pause_solver_service.py)：

1. 以切换时刻为源任务和接替任务的边界；
2. 扩展受影响资源闭包和临时队列依赖；
3. 通过 `first_start_task_id` 保证接替任务第一个开始；
4. 用统一求解器重排剩余工时；
5. 失败时回滚暂停、截断和试排，不留下半套结果。

如果正式合同边界导致无解，[task_pause_switch_diagnosis_service.py](server/app/services/task_pause_switch_diagnosis_service.py) 会临时放宽项目结束日期再求解，用于判断哪些项目会超期并给出建议日期。该诊断不落槽，也不允许正式结果越过合同日期。

### 8.5 仪器故障与维修完成

故障被视为最高优先级资源中断：当前任务在报告时刻截断，已执行部分保留，剩余工作量和同仪器后续队列参与重排。维修完成后，系统以实际维修完成时间释放仪器并尝试恢复被故障中断的原任务；它与“暂停切换后接替任务完成”不是同一种恢复规则。

[instrument_fault_schedule_service.py](server/app/services/instrument_fault_schedule_service.py) 优先调用资源闭包 CP-SAT。若存在冻结槽、复杂执行历史、无负责人等无法无损重建的条件，则由 [schedule_forward_slot_service.py](server/app/services/schedule_forward_slot_service.py) 按工作日历直接重建后移时间槽。文档和代码维护都必须把这条 fallback 与权威主链分开说明。

### 8.6 夜间运行

[schedule_night_run_service.py](server/app/services/schedule_night_run_service.py) 在指定夜间窗口登记特殊运行槽和原始执行记录。夜间自然时长不进入日间有效工时和排程进度，但记录必须保留供统计与审计。夜间运行是特殊时间槽登记，不是一次完整的全局资源求解。

## 9. 落库、并发与一致性保证

正式主链把“求解结果”先转成不接触数据库的 `SchedulePlan`，再集中执行。实现见 [schedule_action_plan.py](server/app/services/schedule_action_plan.py)、[scheduler_result_service.py](server/app/services/scheduler_result_service.py) 和 [scheduler_persistence.py](server/app/services/scheduler_persistence.py)。

### 9.1 成功写回顺序

1. 捕获原任务窗口和装载时的 `base_epoch`。
2. CP-SAT 找到可行解，构造要作废的旧槽、新槽、状态和通知动作。
3. `schedule_epoch_service.claim` 用 `UPDATE ... WHERE version = expected` 原子校验并推进 epoch。
4. 保存本次使用的工作日历、仪器工作时段、维护窗口和规则快照。
5. 通过 `supersede_slot` 软作废旧槽；该原语同步收口执行状态、失效相关桥接/夜跑记录并写变更日志。
6. 创建新时间槽，按冻结/确认边界计算 tier，并写槽变更日志。
7. 更新非保留任务的状态。
8. 全量重建 `InstrumentBridgeReservation`。
9. [schedule_replan_validation_service.py](server/app/services/schedule_replan_validation_service.py) 校验仪器冲突、人员冲突、业务依赖、临时队列依赖和桥接预留。
10. 校验成功后才发送提前或延后通知。
11. 由最外层业务场景提交事务；任一数据库或一致性校验失败则回滚。

通知刻意位于一致性校验之后，因为已发送的外部通知无法随数据库事务回滚。

### 9.2 历史不是删除

旧时间槽通过 `lifecycle_status = superseded` 和 superseded 原因退出现行排程，保留原计划与变更链。读取甘特和排程表时只使用活动槽；审计与排查仍可追溯旧槽。该口径见 [schedule_slot_change_log_service.py](server/app/services/schedule_slot_change_log_service.py)。

### 9.3 当前并发边界

- 求解期间由单进程 `RLock` 避免两个主求解同时运行。
- epoch 防止基于旧世界构造的方案在其他排程已经写回后继续落地。
- 预览令牌防止用户确认一份已过期的插单影响。
- 持久化请求队列保存锁忙时可重试的业务请求，并记录其基线 epoch。
- 手工改单槽和部分直接铺槽旁路并未完整纳入上述并发体系，详见第 12 节。

## 10. 排程失败与结题日建议

### 10.1 失败层级

系统按以下顺序识别问题：

1. **业务输入预检**：需要人员但没有负责人、项目工时不一致、必选仪器缺失或固定原仪器不可用。
2. **建模即时失败**：没有兼容仪器、单任务自身剩余工时已经超出项目时间窗、可拆分任务没有足够有效单元。
3. **求解失败**：CP-SAT 在约束下证明不可行，或在时限内未找到可行解。
4. **写后失败**：epoch 已变化、持久化缺失仪器分配，或写后冲突校验未通过。
5. **系统忙**：未取得排程锁。这不是“排不下”，应即时提示稍后重试或进入队列。

### 10.2 业务诊断

[scheduler_failure_response.py](server/app/services/scheduler_failure_response.py)、[scheduler_diagnostics.py](server/app/services/scheduler_diagnostics.py) 和 [scheduler_failure_diagnostics.py](server/app/services/scheduler_failure_diagnostics.py) 会根据本次求解输入计算：

- 冲突项目、任务及合同截止时间；
- 每台候选仪器的总有效工时、固定占用、剩余容量、本次需求和缺口；
- 其他项目在同资源上的占用；
- 人员容量、依赖边界、维护/故障窗口和桥接占机；
- 未签批下游需要预留的工时；
- 可操作的结题日期调整建议入口。

这是一层应用业务归因，不是 CP-SAT 的数学最小不可满足约束集（unsat core）。应将其理解为“系统按当前输入能够完整计算出的业务原因”，而不是唯一的数学证明。

### 10.3 经求解器验证的结题日建议

失败诊断可创建异步建议任务。它保存原失败请求的可回放参数，改变候选项目的结束日期，并以 `feasibility_only=True` 重跑同一求解器：

- 只返回已经找到可行解的候选方案；
- 超时未证明的候选标记为未确定，不能宣称延期无效；
- 建议日期对齐到真正能增加有效容量的工作日；
- 优先最小化总延期天数，同等情况下优先调整低优先级项目；
- 建议不会自动修改项目合同日期，仍需负责人确认和修改后重新排程。

实现见 [scheduler_deadline_recommendation.py](server/app/services/scheduler_deadline_recommendation.py) 和 [schedule_deadline_recommendation_job_service.py](server/app/services/schedule_deadline_recommendation_job_service.py)。多项目方案中的项目并非一定要全部同时调整；每条建议表达的是求解器验证过的一个可行组合。

## 11. 排程结果如何被系统消费

| 视角 | 页面/服务 | 主要用途 |
| --- | --- | --- |
| 全部排程 | [ScheduleManager.vue](web/src/pages/ScheduleManager.vue) | 查看活动时间槽、冻结/确认/预测数量，发起全局重排 |
| 仪器 | [InstrumentGantt.vue](web/src/pages/InstrumentGantt.vue) | 展示计划、执行、暂停、故障、桥接占位和待签批预测段 |
| 项目 | [ProjectGantt.vue](web/src/pages/kanban/ProjectGantt.vue) | 展示项目内并行任务、已完成实际时间和签批里程碑 |
| 人员 | [HumanGantt.vue](web/src/pages/kanban/HumanGantt.vue) | 展示负责人工作安排、并行 lane 和延期尾段 |
| 个人执行 | `PersonalWorkspace.vue`、`MyAgenda.vue` | 开始、完成、暂停、延期、夜跑及签批操作 |
| 项目交付 | `ProjectProgress.vue`、项目健康服务 | 计划/实际轨迹、预测完工与合同日期偏差 |
| 运营态势 | `LabOperationsCockpit.vue` | 当前仪器、任务、最近完成和延期状态 |
| 统计报表 | 仪器利用率、项目工时、夜间运行报表 | 汇总计划占用、实际运行和执行工时 |

### 11.1 当前前端差异

- “自动排程引擎”页面当前主要提供**全局重排**和全部时间槽表，不包含原型中描述的完整局部修复、项目级控制台和每日滚动按钮。
- [api.ts](web/src/services/api.ts) 存在 `generateSchedule` 和 `dailyRoll` 封装，但当前没有明显页面调用；不能因此称其为用户可用入口。
- 项目计划返回 `queued` 后只提示稍后查看，不轮询请求结果；签批页面会轮询排程请求直至成功或失败。
- 项目计划失败使用结构化 `schedule_failure`；全局重排页面仍依赖固定中文错误文本解析，错误格式改变时会退化为纯文本。
- 仪器、项目、人力甘特路由都存在，但主菜单只明显列出仪器甘特；项目甘特可从其他页面跳转，人力甘特缺少同等显式入口。
- 夜间运行前端声明的 `requires_operator`、`remark` 等字段没有在当前 API 路由中完整传入 `record_night_run`，其业务效果应视为待核实，不能写成已参与排程。

## 12. 已知旁路、配置脱节与重复实现

### 12.1 配置定义不等于求解器已消费

| 现象 | 当前事实 | 影响 |
| --- | --- | --- |
| 时间粒度 | 规则表可选 15/30/60 分钟；核心固定 `TIME_UNIT_MINUTES = 30` | 管理页修改不能改变实际建模粒度 |
| 规划窗口 | 规则表可选 30/60/90/120 天；核心默认固定 `HORIZON_DAYS = 90` | 管理页修改不能改变默认视界 |
| 三条 objective 配置 | 规则表保存延迟、makespan、切换权重；目标函数使用另一组硬编码权重 | 页面权重不是实际目标参数 |
| `freezing.is_enabled` | 核心读取 `freeze_days`，并保护已有 frozen 槽 | 关闭规则未明显关闭冻结层级行为 |
| `strict` | 多数规则带该元数据 | 核心没有统一把它解释成软/硬切换 |
| `Instrument.switchover_base_hours` | 字段被快照和展示读取 | 正式核心跨项目间隔使用规则 `setup_hours`，未消费该字段 |
| `Task.allow_transfer` | 可保存、审计 | 当前没有多人员池或跨仪器转移逻辑使用它 |
| 通用 `earliest_start/latest_due` | 健康度和诊断会使用部分字段 | 正式核心任务窗口主要来自项目日期和场景传入的 `earliest_start_bounds` |
| `SOLVER_TIMEOUT_SECONDS` | 配置对象中存在 | `SchedulerService._generate` 当前默认直接使用 30 秒 |

规则定义见 [schedule_rule_service.py](server/app/services/schedule_rule_service.py)，真实常量和目标分别见 [scheduler_helpers.py](server/app/services/scheduler_helpers.py) 与 [scheduler_objective.py](server/app/services/scheduler_objective.py)。只有“配置定义”和“实际消费点”同时存在时，文档才能将一项配置标为生效。

### 12.2 不经过完整权威主链的路径

| 路径 | 差异 | 风险边界 |
| --- | --- | --- |
| 故障/延期 fallback | [schedule_forward_slot_service.py](server/app/services/schedule_forward_slot_service.py) 按工作日历直接向后铺槽 | 适用于无法用 CP-SAT 无损重建的历史/冻结状态；不具备完整主链的统一语义 |
| 手工调整时间槽 | [schedule_manual_update_service.py](server/app/services/schedule_manual_update_service.py) 直接改开始、结束、仪器或 tier | 只禁止 frozen 槽，不走 CP-SAT、epoch、桥接重建和统一冲突校验 |
| 局部重排 | 先删除指定任务 confirmed/forecast 槽并提交，再调用求解器 | 后续失败时旧槽不能靠同一事务自动恢复 |
| 项目重排 | 在 savepoint 中删除并求解 | 失败回滚 savepoint，旧排程可恢复 |
| 全局重排 | 同一事务删除可移动槽并求解 | 成功提交，失败整体回滚；锁定任务不动 |

项目计划“保存并排程”的实现与上述局部重排不同：它把可释放槽作为求解输入，成功后才通过 `SchedulePlan` 软作废旧槽。

### 12.3 重复语义

- **桥接占机**：求解时按依赖图识别，落库后又按现行时间轴重建 `InstrumentBridgeReservation`。业务规则修改时必须同步检查 [scheduler_instrument_bridging.py](server/app/services/scheduler_instrument_bridging.py) 和 [instrument_bridge_sync_service.py](server/app/services/instrument_bridge_sync_service.py)。
- **项目完工时间**：CP-SAT 给出权威资源竞争结果；[project_completion_projection_service.py](server/app/services/project_completion_projection_service.py) 按依赖和日历前向推演，只适用于外围预测或诊断。
- **优先级**：同时出现在候选移动筛选、临时依赖、目标权重和结题日建议排序中；维护时必须区分硬顺序与软倾向。

这些差异必须保留在文档中，直到代码真正合并到单一口径；不能为了让说明“看起来简洁”而省略。

## 13. 实现证据与测试索引

以下测试是相应规则的代表性实现证据，不表示所有入口都自动获得同样的事务与一致性保证：

| 规则域 | 代表性测试 |
| --- | --- |
| 30 分钟粒度与执行工时 | [test_scheduler_duration_granularity.py](server/tests/test_scheduler_duration_granularity.py)、[test_task_execution_minutes_calendar.py](server/tests/test_task_execution_minutes_calendar.py) |
| 项目时间窗与固定占用 | [test_project_window_capacity.py](server/tests/test_project_window_capacity.py)、[test_scheduler_fixed_slots.py](server/tests/test_scheduler_fixed_slots.py) |
| 依赖与桥接占机 | [test_scheduler_parent_dependencies.py](server/tests/test_scheduler_parent_dependencies.py)、[test_scheduler_instrument_bridging.py](server/tests/test_scheduler_instrument_bridging.py)、[test_cross_project_bridge.py](server/tests/test_cross_project_bridge.py) |
| 目标函数 | [test_scheduler_objective.py](server/tests/test_scheduler_objective.py) |
| 未签批下游 | [test_pending_approval_forecast.py](server/tests/test_pending_approval_forecast.py)、[test_scheduler_approval_gate_forecast.py](server/tests/test_scheduler_approval_gate_forecast.py)、[test_approval_gate_schedule_dependencies.py](server/tests/test_approval_gate_schedule_dependencies.py) |
| 暂停任务与切换顺序 | [test_paused_task_is_movable.py](server/tests/test_paused_task_is_movable.py)、[test_pause_switch_paused_target.py](server/tests/test_pause_switch_paused_target.py)、[test_task_pause_followup_order.py](server/tests/test_task_pause_followup_order.py) |
| 故障、延期和提前完成 | [test_instrument_fault_schedule_service.py](server/tests/test_instrument_fault_schedule_service.py)、[test_schedule_delay_propagation_cp_sat.py](server/tests/test_schedule_delay_propagation_cp_sat.py)、[test_schedule_early_completion_replan_service.py](server/tests/test_schedule_early_completion_replan_service.py) |
| 落库、epoch 与写后校验 | [test_schedule_action_plan.py](server/tests/test_schedule_action_plan.py)、[test_schedule_epoch_lock.py](server/tests/test_schedule_epoch_lock.py)、[test_schedule_replan_validation_service.py](server/tests/test_schedule_replan_validation_service.py) |
| 失败诊断 | [test_scheduler_infeasibility_diagnostics.py](server/tests/test_scheduler_infeasibility_diagnostics.py)、[test_scheduler_failure_bridge_attribution.py](server/tests/test_scheduler_failure_bridge_attribution.py) |

## 14. 维护约定

排程逻辑变更时按以下清单维护本文：

1. 修改排程入口、`SchedulerService` 参数、硬约束、目标函数、工时口径、落库事务、epoch、写后校验、fallback 或前端入口时，同一变更必须检查本文。
2. 业务规则发生变化时先更新“业务规则”；代码完成并有证据后，再更新“当前实现”和状态。目标态不能提前写成现状。
3. 新增配置时同时记录定义位置和实际消费位置；缺少消费点的配置只能标为未接线。
4. 新增旁路时必须记录触发条件、是否使用 CP-SAT、是否使用 epoch、是否执行写后校验、失败能否完整回滚。
5. 删除旁路或统一算法时，更新差异表并附对应测试证据，不要只删掉差异说明。
6. 关键规则在本文只维护一个权威条目，其他章节链接到它，避免在多处复制权重和常量。
7. 代码路径引用以文件和稳定符号为主；文件移动或函数重命名时统一修复链接。
8. 培训材料、产品说明或原型更新时，不自动改写“当前实现”，必须重新核对代码和测试。
9. 发布前分别由熟悉业务口径和熟悉求解器实现的人员核对规则与技术事实。

## 15. 参考资料

- [PRODUCT.md](PRODUCT.md)：产品定位、目标用户和产品原则。
- [基因毒组系统运行规范培训 PPT 大纲](docs/基因毒组系统运行规范培训PPT大纲.md)：角色操作顺序和业务培训口径。
- [scheduler.py](server/app/services/scheduler.py)：CP-SAT 主编排入口。
- [schedule_action_plan.py](server/app/services/schedule_action_plan.py)：求解结果转落库动作及通知闸门。
- [schedule_rule_service.py](server/app/services/schedule_rule_service.py)：规则目录和管理端配置定义。

Git 历史承担变更记录；本文不另建与代码容易失去同步的手写版本流水账。
