"""同一台仪器上跨项目相邻任务的切换准备时间与共存惩罚。

两个不同项目的任务排在同一台仪器上时，中间需要留出切换准备时间；
同时给这种共存加一个惩罚项，让求解器倾向于把同项目任务聚在一起。
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from app.services.scheduler_helpers import to_units


def cross_project_setup_units(constraints) -> int:
    """跨项目切换所需的准备时间，换算成排程时间单元。"""
    setup_rule = constraints["cross_project_setup"]
    setup_hours = (
        (setup_rule.params or {}).get("setup_hours", 0.5)
        if setup_rule.is_enabled
        else 0
    )
    return to_units(setup_hours) if setup_hours else 0


def add_cross_project_switch_constraints(
    model: cp_model.CpModel,
    *,
    tasks,
    instruments,
    presences,
    inst_starts,
    inst_ends,
    setup_units: int,
    setup_exempt_task_pairs: set[frozenset[int]] | None,
) -> list:
    """返回跨项目共存惩罚变量，并就地加上切换准备时间约束。"""
    switch_penalties = []
    setup_exempt_task_pairs = setup_exempt_task_pairs or set()
    tasks_by_id = {t.id: t for t in tasks}

    for inst in instruments:
        inst_task_ids = [key[0] for key in presences if key[1] == inst.id]
        for i in range(len(inst_task_ids)):
            for j in range(i + 1, len(inst_task_ids)):
                tA_id = inst_task_ids[i]
                tB_id = inst_task_ids[j]
                tA = tasks_by_id[tA_id]
                tB = tasks_by_id[tB_id]

                if tA.project_id == tB.project_id:
                    continue

                pA = presences[(tA_id, inst.id)]
                pB = presences[(tB_id, inst.id)]
                startA, endA = inst_starts[(tA_id, inst.id)], inst_ends[(tA_id, inst.id)]
                startB, endB = inst_starts[(tB_id, inst.id)], inst_ends[(tB_id, inst.id)]

                a_before_b = model.NewBoolVar(f"seq_{tA_id}_before_{tB_id}_on_{inst.id}")
                b_before_a = model.NewBoolVar(f"seq_{tB_id}_before_{tA_id}_on_{inst.id}")

                # Ordering: when both present, exactly one precedes the other
                model.Add(a_before_b + b_before_a == 1).OnlyEnforceIf([pA, pB])
                # When not co-present, force ordering vars to 0
                model.Add(a_before_b == 0).OnlyEnforceIf(pA.Not())
                model.Add(b_before_a == 0).OnlyEnforceIf(pA.Not())
                model.Add(a_before_b == 0).OnlyEnforceIf(pB.Not())
                model.Add(b_before_a == 0).OnlyEnforceIf(pB.Not())

                if frozenset((tA_id, tB_id)) not in setup_exempt_task_pairs:
                    # Setup time between ordinary cross-project tasks.
                    model.Add(startB >= endA + setup_units).OnlyEnforceIf([pA, pB, a_before_b])
                    model.Add(startA >= endB + setup_units).OnlyEnforceIf([pA, pB, b_before_a])

                # Collect cross-project co-presence for penalty
                both_present = model.NewBoolVar(f"both_{tA_id}_{tB_id}_on_{inst.id}")

                # Proper AND: both_present = pA AND pB
                model.AddImplication(both_present, pA)
                model.AddImplication(both_present, pB)
                model.AddBoolOr([pA.Not(), pB.Not()]).OnlyEnforceIf(both_present.Not())

                switch_penalties.append(both_present)

    return switch_penalties
