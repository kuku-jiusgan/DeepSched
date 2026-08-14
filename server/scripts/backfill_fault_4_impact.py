from app.core.database import SessionLocal
from app.models import AuditLog


FAULT_ID = 4
IMPACT_DETAILS = [
    {
        "task_id": 48,
        "task_name": "方法开发",
        "project_id": 19,
        "project_name": "奥拉帕利中9个基因毒杂质方法研究",
        "project_code": "XM2026208",
        "assignee_name": "王福芳",
        "original_start": "2026-08-13T08:30:00",
        "original_end": "2026-08-14T13:00:00",
        "shifted_start": "2026-08-17T08:30:00",
        "shifted_end": "2026-08-20T16:30:00",
        "can_shift": True,
        "reason": "",
    },
    {
        "task_id": 49,
        "task_name": "方案撰写",
        "project_id": 19,
        "project_name": "奥拉帕利中9个基因毒杂质方法研究",
        "project_code": "XM2026208",
        "assignee_name": "王福芳",
        "original_start": "2026-08-21T16:00:00",
        "original_end": "2026-08-21T20:00:00",
        "shifted_start": "2026-08-24T08:30:00",
        "shifted_end": "2026-08-24T12:30:00",
        "can_shift": True,
        "reason": "",
    },
    {
        "task_id": 96,
        "task_name": "方法开发",
        "project_id": 26,
        "project_name": "奋乃静片中N-亚硝基-N-去甲基丙氯拉嗪杂质分析方法研究",
        "project_code": "XM2026218",
        "assignee_name": "王福芳",
        "original_start": "2026-08-25T18:00:00",
        "original_end": "2026-08-27T13:00:00",
        "shifted_start": "2026-08-27T18:00:00",
        "shifted_end": "2026-08-31T13:00:00",
        "can_shift": True,
        "reason": "",
    },
    {
        "task_id": 97,
        "task_name": "方案撰写",
        "project_id": 26,
        "project_name": "奋乃静片中N-亚硝基-N-去甲基丙氯拉嗪杂质分析方法研究",
        "project_code": "XM2026218",
        "assignee_name": "王福芳",
        "original_start": "2026-08-28T16:30:00",
        "original_end": "2026-08-28T18:30:00",
        "shifted_start": "2026-08-31T13:00:00",
        "shifted_end": "2026-08-31T15:00:00",
        "can_shift": True,
        "reason": "",
    },
]


def main() -> None:
    db = SessionLocal()
    try:
        existing = db.query(AuditLog.id).filter(
            AuditLog.action == "instrument_fault_rescheduled",
            AuditLog.target_type == "instrument_fault",
            AuditLog.target_id == FAULT_ID,
        ).first()
        if existing:
            return
        db.add(AuditLog(
            user_name="system",
            action="instrument_fault_rescheduled",
            target_type="instrument_fault",
            target_id=FAULT_ID,
            detail={
                "impact": {
                    "shifted_slots": 9,
                    "affected_tasks": len(IMPACT_DETAILS),
                    "notified_users": 0,
                    "risk_tasks": 0,
                    "affected_task_details": IMPACT_DETAILS,
                },
            },
        ))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
