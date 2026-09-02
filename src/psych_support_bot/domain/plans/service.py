import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from psych_support_bot.domain.plans.templates import PLAN_TEMPLATES
from psych_support_bot.infra.db.models import PlanEnrollment


def list_plans() -> dict[str, dict[str, object]]:
    return PLAN_TEMPLATES


def get_plan(plan_id: str) -> dict[str, object]:
    if plan_id not in PLAN_TEMPLATES:
        raise KeyError(plan_id)
    return PLAN_TEMPLATES[plan_id]


def get_today_step(plan_id: str, day: int) -> dict[str, object]:
    """Get the daily step for a specific day number."""
    plan = get_plan(plan_id)
    daily = plan.get("daily_steps", {})
    key = f"day_{day}"
    if key not in daily:
        raise KeyError(f"Day {day} not found in plan {plan_id}")
    return daily[key]


def enroll_user(plan_id: str, user_id: str, session: Session) -> PlanEnrollment:
    """Enroll a user in a plan. If already enrolled, return existing."""
    if plan_id not in PLAN_TEMPLATES:
        raise KeyError(plan_id)

    existing = session.execute(
        select(PlanEnrollment).where(
            PlanEnrollment.user_id == user_id,
            PlanEnrollment.plan_id == plan_id,
            PlanEnrollment.status == "active",
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    enrollment = PlanEnrollment(
        id=str(uuid4()),
        user_id=user_id,
        plan_id=plan_id,
        enrolled_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        completed_days_json="[]",
        current_day=1,
        status="active",
    )
    session.add(enrollment)
    session.commit()
    return enrollment


def get_progress(plan_id: str, user_id: str, session: Session) -> dict[str, object]:
    """Get a user's progress on a plan."""
    if plan_id not in PLAN_TEMPLATES:
        raise KeyError(plan_id)

    enrollment = session.execute(
        select(PlanEnrollment).where(
            PlanEnrollment.user_id == user_id,
            PlanEnrollment.plan_id == plan_id,
            PlanEnrollment.status == "active",
        )
    ).scalar_one_or_none()

    if not enrollment:
        return {
            "plan_id": plan_id,
            "enrolled": False,
            "current_day": 0,
            "completed_days": [],
            "total_days": PLAN_TEMPLATES[plan_id]["days"],
        }

    completed_days = json.loads(enrollment.completed_days_json or "[]")
    today_step = get_today_step(plan_id, enrollment.current_day) if enrollment.current_day <= PLAN_TEMPLATES[plan_id]["days"] else None

    return {
        "plan_id": plan_id,
        "enrolled": True,
        "enrollment_id": enrollment.id,
        "current_day": enrollment.current_day,
        "completed_days": completed_days,
        "total_days": PLAN_TEMPLATES[plan_id]["days"],
        "today_step": today_step,
    }


def mark_day_complete(plan_id: str, user_id: str, day: int, session: Session) -> dict[str, object]:
    """Mark a specific day as completed and advance current_day."""
    if plan_id not in PLAN_TEMPLATES:
        raise KeyError(plan_id)

    enrollment = session.execute(
        select(PlanEnrollment).where(
            PlanEnrollment.user_id == user_id,
            PlanEnrollment.plan_id == plan_id,
            PlanEnrollment.status == "active",
        )
    ).scalar_one_or_none()

    if not enrollment:
        raise ValueError(f"User {user_id} is not enrolled in plan {plan_id}")

    completed_days = json.loads(enrollment.completed_days_json or "[]")
    if day not in completed_days:
        completed_days.append(day)
        completed_days.sort()
    enrollment.completed_days_json = json.dumps(completed_days)

    total_days = PLAN_TEMPLATES[plan_id]["days"]
    if day >= enrollment.current_day and day < total_days:
        enrollment.current_day = day + 1
    elif day >= total_days:
        enrollment.current_day = total_days
        enrollment.status = "completed"

    session.commit()

    return {
        "plan_id": plan_id,
        "completed_days": completed_days,
        "current_day": enrollment.current_day,
        "status": enrollment.status,
        "total_days": total_days,
    }
