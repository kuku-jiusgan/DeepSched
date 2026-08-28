"""方案签批的审计留痕与通知推送。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import AuditLog, Task, User
from app.services.approval_gate_access_service import APPROVAL_WRITE_ROLES
from app.services.push_notification_service import push_by_rule
from app.services.user_role_service import has_any_role


def record_gate_audit(db, user: User, action: str, gate: Task, detail: dict) -> None:
    db.add(AuditLog(
        user_name=user.display_name or user.username,
        action=action,
        target_type="approval_gate",
        target_id=gate.id,
        detail={"project_id": gate.project_id, **detail},
    ))

def notify_gate(db, gate: Task, rule_type: str, title: str, content: str) -> int:
    users = db.query(User).filter(User.is_active.is_(True)).all()
    recipients = [
        user for user in users
        if user.id == gate.assignee_id or has_any_role(user, APPROVAL_WRITE_ROLES)
    ]
    return push_by_rule(
        db,
        rule_type,
        recipients,
        title,
        content,
        related_entity_type="approval_gate",
        related_entity_id=gate.id,
        context_roles=["任务负责人"],
    )

def scan_approval_deadlines(db) -> int:
    now = datetime.now()
    gates = db.query(Task).filter(
        Task.is_external_gate.is_(True),
        Task.gate_status == "waiting_approval",
        Task.expected_approval_at.isnot(None),
    ).all()
    sent = 0
    for gate in gates:
        action = "approval_overdue_notified" if gate.expected_approval_at < now else "approval_upcoming_notified"
        if gate.expected_approval_at >= now + timedelta(days=2):
            continue
        if db.query(AuditLog.id).filter(AuditLog.action == action, AuditLog.target_id == gate.id).first():
            continue
        state = "已超过预计签批时间" if gate.expected_approval_at < now else "将在两天内到期"
        sent += notify_gate(
            db,
            gate,
            "approval_due",
            "方案签批时间提醒",
            f"项目【{gate.project.code}】的方案签批{state}，请关注客户反馈和结题风险。",
        )
        db.add(AuditLog(
            user_name="system",
            action=action,
            target_type="approval_gate",
            target_id=gate.id,
            detail={"expected_approval_at": gate.expected_approval_at.isoformat()},
        ))
    db.commit()
    return sent
