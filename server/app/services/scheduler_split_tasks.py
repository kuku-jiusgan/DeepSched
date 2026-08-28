from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from app.services.scheduler_helpers import optional_time_domain


def add_split_task_variables(
    model: cp_model.CpModel,
    task,
    candidates,
    duration_units: int,
    project_start_unit: int,
    project_end_unit: int,
    task_start_max: int,
    task_end_min: int,
    total_units: int,
    instrument_prefix_sums: dict[int, list[int]],
    task_start,
    task_end,
    presences,
    inst_starts,
    inst_ends,
    capacity_intervals,
    split_unit_presences,
) -> bool:
    choices = []
    start_candidates = []
    end_candidates = []
    for instrument in candidates:
        working_prefix_sum = instrument_prefix_sums[instrument.id]
        valid_units = [
            unit
            for unit in range(
                max(0, project_start_unit),
                min(total_units, project_end_unit),
            )
            if working_prefix_sum[unit + 1] - working_prefix_sum[unit] == 1
        ]
        if len(valid_units) < duration_units:
            continue

        key = (task.id, instrument.id)
        choice = model.NewBoolVar(f"presence_t{task.id}_i{instrument.id}")
        presences[key] = choice
        choices.append(choice)
        unit_choices = []
        for unit in valid_units:
            unit_choice = model.NewBoolVar(f"split_t{task.id}_i{instrument.id}_u{unit}")
            split_unit_presences[(task.id, instrument.id, unit)] = unit_choice
            unit_choices.append(unit_choice)
            capacity_intervals[instrument.id].append(model.NewOptionalIntervalVar(
                unit,
                1,
                unit + 1,
                unit_choice,
                f"split_iv_t{task.id}_i{instrument.id}_u{unit}",
            ))
            model.AddImplication(unit_choice, choice)

            start_candidate = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues([unit, total_units]),
                f"split_start_t{task.id}_i{instrument.id}_u{unit}",
            )
            model.Add(start_candidate == unit).OnlyEnforceIf(unit_choice)
            model.Add(start_candidate == total_units).OnlyEnforceIf(unit_choice.Not())
            start_candidates.append(start_candidate)

            end_candidate = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues([0, unit + 1]),
                f"split_end_t{task.id}_i{instrument.id}_u{unit}",
            )
            model.Add(end_candidate == unit + 1).OnlyEnforceIf(unit_choice)
            model.Add(end_candidate == 0).OnlyEnforceIf(unit_choice.Not())
            end_candidates.append(end_candidate)

        model.Add(sum(unit_choices) == duration_units * choice)
        inst_start = model.NewIntVarFromDomain(
            optional_time_domain(project_start_unit, task_start_max),
            f"start_t{task.id}_i{instrument.id}",
        )
        inst_end = model.NewIntVarFromDomain(
            optional_time_domain(task_end_min, project_end_unit),
            f"end_t{task.id}_i{instrument.id}",
        )
        inst_starts[key] = inst_start
        inst_ends[key] = inst_end
        model.Add(inst_start == task_start).OnlyEnforceIf(choice)
        model.Add(inst_end == task_end).OnlyEnforceIf(choice)
        model.Add(inst_start == 0).OnlyEnforceIf(choice.Not())
        model.Add(inst_end == 0).OnlyEnforceIf(choice.Not())

    if not choices:
        return False

    model.AddExactlyOne(choices)
    model.AddMinEquality(task_start, start_candidates)
    model.AddMaxEquality(task_end, end_candidates)
    return True
