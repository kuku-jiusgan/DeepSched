from sqlalchemy import inspect, text


def ensure_runtime_schema(engine) -> None:
    from app.models import (
        ScheduleCalendarSnapshot,
        DashboardStatsSnapshot,
        LabStatusSnapshot,
        ScheduleDeadlineRecommendationJob,
        InstrumentBridgeReservation,
        ScheduleSlotChangeLog,
        TaskNightRun,
    )

    TaskNightRun.__table__.create(bind=engine, checkfirst=True)
    ScheduleCalendarSnapshot.__table__.create(bind=engine, checkfirst=True)
    DashboardStatsSnapshot.__table__.create(bind=engine, checkfirst=True)
    LabStatusSnapshot.__table__.create(bind=engine, checkfirst=True)
    ScheduleSlotChangeLog.__table__.create(bind=engine, checkfirst=True)
    ScheduleDeadlineRecommendationJob.__table__.create(bind=engine, checkfirst=True)
    InstrumentBridgeReservation.__table__.create(bind=engine, checkfirst=True)
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    if "sys_calendar" in table_names:
        calendar_columns = {column["name"] for column in inspector.get_columns("sys_calendar")}
        with engine.begin() as connection:
            if "source" not in calendar_columns:
                connection.execute(text(
                    "ALTER TABLE sys_calendar ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'default'"
                ))
            connection.execute(text(
                "UPDATE sys_calendar SET source = 'default' WHERE source IS NULL OR source = ''"
            ))

    if "user" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        with engine.begin() as connection:
            if "roles" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN roles JSON"))
            connection.execute(text(
                "UPDATE user SET role = '分析所所长' WHERE role = '项目负责人'"
            ))
            connection.execute(text(
                "UPDATE user SET role = '技术员' WHERE role = '分析员'"
            ))
            connection.execute(text(
                "UPDATE user SET roles = REPLACE(roles, '\"分析员\"', '\"技术员\"') "
                "WHERE roles IS NOT NULL"
            ))

    if "project" in table_names:
        project_columns = {column["name"] for column in inspector.get_columns("project")}
        with engine.begin() as connection:
            if "estimated_hours" not in project_columns:
                connection.execute(text("ALTER TABLE project ADD COLUMN estimated_hours FLOAT"))
            if "project_kind" not in project_columns:
                connection.execute(text(
                    "ALTER TABLE project ADD COLUMN project_kind VARCHAR(20) DEFAULT 'project'"
                ))
            connection.execute(text(
                "UPDATE project SET project_kind = 'project' WHERE project_kind IS NULL"
            ))
            if "sla_level" in project_columns:
                connection.execute(text("ALTER TABLE project DROP COLUMN sla_level"))
            if "profit_weight" in project_columns:
                connection.execute(text("ALTER TABLE project DROP COLUMN profit_weight"))
            connection.execute(text("UPDATE project SET priority = 1 WHERE priority IS NULL OR priority < 1"))
            connection.execute(text("UPDATE project SET priority = 3 WHERE priority > 3"))
            if engine.dialect.name == "mysql":
                connection.execute(text(
                    "UPDATE project SET start_date = DATE(start_date) "
                    "WHERE start_date IS NOT NULL"
                ))
                connection.execute(text(
                    "UPDATE project SET end_date = "
                    "DATE_ADD(DATE(end_date), INTERVAL 1 DAY) - INTERVAL 1 SECOND "
                    "WHERE end_date IS NOT NULL"
                ))
    if "task" in table_names:
        task_columns = {column["name"] for column in inspector.get_columns("task")}
        with engine.begin() as connection:
            if "plan_order" not in task_columns:
                connection.execute(text("ALTER TABLE task ADD COLUMN plan_order INTEGER NOT NULL DEFAULT 0"))
            if "schedule_dirty" not in task_columns:
                connection.execute(text("ALTER TABLE task ADD COLUMN schedule_dirty BOOLEAN DEFAULT 0"))
    if "task_dependency" in table_names:
        dependency_columns = {column["name"] for column in inspector.get_columns("task_dependency")}
        with engine.begin() as connection:
            if "dependency_type" not in dependency_columns:
                connection.execute(text(
                    "ALTER TABLE task_dependency ADD COLUMN dependency_type VARCHAR(30) "
                    "NOT NULL DEFAULT 'predecessor'"
                ))
            if engine.dialect.name == "mysql":
                connection.execute(text(
                    "UPDATE task_dependency AS dependency "
                    "JOIN task AS child ON child.id = dependency.task_id "
                    "JOIN task AS parent ON parent.id = dependency.predecessor_id "
                    "SET dependency.dependency_type = 'continuous_successor' "
                    "WHERE child.task_type IN ('QCFA_001', 'ZXBG_001') "
                    "AND parent.task_type IN ('FFKF_001', 'FFYZ_001')"
                ))
            else:
                connection.execute(text(
                    "UPDATE task_dependency SET dependency_type = 'continuous_successor' "
                    "WHERE task_id IN (SELECT child.id FROM task child "
                    "JOIN task parent ON parent.id = task_dependency.predecessor_id "
                    "WHERE child.task_type IN ('QCFA_001', 'ZXBG_001') "
                    "AND parent.task_type IN ('FFKF_001', 'FFYZ_001'))"
                ))
                connection.execute(text("UPDATE task SET schedule_dirty = 0 WHERE schedule_dirty IS NULL"))
            if "delay_status" not in task_columns:
                connection.execute(text(
                    "ALTER TABLE task ADD COLUMN delay_status VARCHAR(20) DEFAULT 'not_delayed'"
                ))
                connection.execute(text(
                    "UPDATE task SET delay_status = 'not_delayed' WHERE delay_status IS NULL"
                ))
            if "executed_minutes" not in task_columns:
                connection.execute(text(
                    "ALTER TABLE task ADD COLUMN executed_minutes INTEGER NOT NULL DEFAULT 0"
                ))
            connection.execute(text("UPDATE task SET executed_minutes = 0 WHERE executed_minutes IS NULL"))
            if "additional_planned_minutes" not in task_columns:
                connection.execute(text(
                    "ALTER TABLE task ADD COLUMN additional_planned_minutes INTEGER NOT NULL DEFAULT 0"
                ))
            connection.execute(text(
                "UPDATE task SET additional_planned_minutes = 0 WHERE additional_planned_minutes IS NULL"
            ))
            slot_columns = {column["name"] for column in inspector.get_columns("time_slot")} if "time_slot" in table_names else set()
            lifecycle_columns = {
                "lifecycle_status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
                "superseded_at": "DATETIME",
                "superseded_by_slot_id": "INTEGER",
                "superseded_reason": "VARCHAR(200)",
                "superseded_by": "INTEGER",
            }
            for column_name, column_type in lifecycle_columns.items():
                if column_name not in slot_columns:
                    connection.execute(text(f"ALTER TABLE time_slot ADD COLUMN {column_name} {column_type}"))
            approval_columns = {
                "is_external_gate": "BOOLEAN DEFAULT 0",
                "gate_status": "VARCHAR(30) DEFAULT 'not_submitted'",
                "expected_approval_at": "DATETIME",
                "submitted_at": "DATETIME",
                "approved_at": "DATETIME",
                "approved_by": "INTEGER",
                "approval_note": "TEXT",
                "approval_schedule_status": "VARCHAR(30)",
                "approval_schedule_message": "TEXT",
                "approval_preview_token": "VARCHAR(128)",
                "approval_schedule_run_id": "VARCHAR(64)",
                "approval_moved_tasks": "INTEGER DEFAULT 0",
            }
            for column_name, column_type in approval_columns.items():
                if column_name not in task_columns:
                    connection.execute(text(
                        f"ALTER TABLE task ADD COLUMN {column_name} {column_type}"
                    ))
            connection.execute(text(
                "UPDATE task SET assignee_id = ("
                "SELECT project.manager_id FROM project WHERE project.id = task.project_id"
                ") WHERE is_external_gate = 1 AND assignee_id IS NULL"
            ))

    if "instrument_fault" in table_names:
        fault_columns = {column["name"] for column in inspector.get_columns("instrument_fault")}
        if "estimated_resolved_at" not in fault_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE instrument_fault ADD COLUMN estimated_resolved_at DATETIME"))

    if "instrument" in table_names:
        instrument_columns = {column["name"] for column in inspector.get_columns("instrument")}
        with engine.begin() as connection:
            if "availability_status" not in instrument_columns:
                connection.execute(text("ALTER TABLE instrument ADD COLUMN availability_status VARCHAR(20) DEFAULT 'available'"))
            if "effective_work_start" not in instrument_columns:
                connection.execute(text(
                    "ALTER TABLE instrument ADD COLUMN effective_work_start VARCHAR(5) NOT NULL DEFAULT '08:30'"
                ))
            if "effective_work_end" not in instrument_columns:
                connection.execute(text(
                    "ALTER TABLE instrument ADD COLUMN effective_work_end VARCHAR(5) NOT NULL DEFAULT '20:00'"
                ))

    if "schedule_calendar_snapshot" in table_names:
        snapshot_columns = {
            column["name"] for column in inspector.get_columns("schedule_calendar_snapshot")
        }
        if "instrument_working_hours" not in snapshot_columns:
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE schedule_calendar_snapshot ADD COLUMN instrument_working_hours JSON"
                ))
                connection.execute(text(
                    "UPDATE schedule_calendar_snapshot SET instrument_working_hours = '{}' "
                    "WHERE instrument_working_hours IS NULL"
                ))

    if "notification" in table_names:
        notification_columns = {column["name"] for column in inspector.get_columns("notification")}
        with engine.begin() as connection:
            if "channel" not in notification_columns:
                connection.execute(text("ALTER TABLE notification ADD COLUMN channel VARCHAR(20) DEFAULT 'site'"))
            if "delivery_status" not in notification_columns:
                connection.execute(text("ALTER TABLE notification ADD COLUMN delivery_status VARCHAR(20) DEFAULT 'success'"))
            if "error_message" not in notification_columns:
                connection.execute(text("ALTER TABLE notification ADD COLUMN error_message TEXT"))

    if "task_type_config" in table_names:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE task_type_config SET predecessor_type_ids = '[]' "
                "WHERE predecessor_type_ids IS NULL"
            ))
            existing = connection.execute(text(
                "SELECT id FROM task_type_config WHERE code = 'approval_gate' LIMIT 1"
            )).first()
            if not existing:
                connection.execute(text(
                    "INSERT INTO task_type_config "
                    "(name, code, resource_type, description, is_active, sort_order) "
                    "VALUES ('方案签批', 'approval_gate', 'none', '外部审批限制，不占用人员或仪器', 1, 0)"
                ))
            connection.execute(text(
                "UPDATE task_type_config SET name = '方案签批', "
                "description = '外部审批限制，不占用人员或仪器' "
                "WHERE code = 'approval_gate'"
            ))
            connection.execute(text(
                "UPDATE task SET name = '方案签批' "
                "WHERE task_type = 'approval_gate' "
                "AND name IN ('客户方案签批限制', '客户方案签批', '客户签批限制')"
            ))

    if "role_permission" in table_names:
        role_permission_columns = {
            column["name"] for column in inspector.get_columns("role_permission")
        }
        if "action_permissions" not in role_permission_columns:
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE role_permission ADD COLUMN action_permissions JSON"
                ))
        with engine.begin() as connection:
            legacy_count = connection.execute(text(
                "SELECT COUNT(*) FROM role_permission WHERE role = '分析员'"
            )).scalar()
            if legacy_count:
                connection.execute(text("DELETE FROM role_permission WHERE role = '技术员'"))
                connection.execute(text(
                    "UPDATE role_permission SET role = '技术员' WHERE role = '分析员'"
                ))

    if "alert_rule" in table_names:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE alert_rule SET notify_roles = "
                "REPLACE(notify_roles, '\"分析员\"', '\"技术员\"') "
                "WHERE notify_roles IS NOT NULL"
            ))

    if "push_channel_config" in table_names:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE push_channel_config SET "
                "wecom_corp_id = TRIM(wecom_corp_id), "
                "wecom_agent_id = TRIM(wecom_agent_id), "
                "wecom_secret = TRIM(wecom_secret)"
            ))

    if "time_slot" in table_names:
        time_slot_columns = {column["name"] for column in inspector.get_columns("time_slot")}
        with engine.begin() as connection:
            if "schedule_run_id" not in time_slot_columns:
                connection.execute(text("ALTER TABLE time_slot ADD COLUMN schedule_run_id VARCHAR(64) DEFAULT 'legacy'"))
                connection.execute(text("UPDATE time_slot SET schedule_run_id = 'legacy' WHERE schedule_run_id IS NULL"))
            if "is_night_run" not in time_slot_columns:
                connection.execute(text("ALTER TABLE time_slot ADD COLUMN is_night_run BOOLEAN DEFAULT 0"))
                connection.execute(text("UPDATE time_slot SET is_night_run = 0 WHERE is_night_run IS NULL"))

    if "alert_rule" in table_names:
        alert_columns = {column["name"] for column in inspector.get_columns("alert_rule")}
        with engine.begin() as connection:
            if "enable_site" not in alert_columns:
                connection.execute(text("ALTER TABLE alert_rule ADD COLUMN enable_site BOOLEAN DEFAULT 1"))
            if "enable_wecom" not in alert_columns:
                connection.execute(text("ALTER TABLE alert_rule ADD COLUMN enable_wecom BOOLEAN DEFAULT 1"))
            connection.execute(text("UPDATE alert_rule SET enable_site = 1, enable_wecom = 1"))

    if {"project", "task", "time_slot"}.issubset(table_names):
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE project SET status = 'pending' "
                "WHERE status = 'active' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM task WHERE task.project_id = project.id "
                "AND task.status IN ('running', 'paused', 'done', 'completed', 'interrupted')"
                ") "
                "AND NOT EXISTS ("
                "SELECT 1 FROM time_slot JOIN task ON task.id = time_slot.task_id "
                "WHERE task.project_id = project.id AND time_slot.actual_start IS NOT NULL"
                ")"
            ))
