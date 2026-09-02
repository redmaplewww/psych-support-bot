from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from psych_support_bot.domain.plans.service import (
    enroll_user,
    get_plan,
    get_progress,
    get_today_step,
    list_plans,
    mark_day_complete,
)
from psych_support_bot.infra.db.session import SessionLocal

router = APIRouter(prefix="/v1/plans", tags=["plans"])


@router.get("")
def get_plans() -> dict[str, dict[str, object]]:
    return list_plans()


@router.get("/{plan_id}")
def get_plan_by_id(plan_id: str) -> dict[str, object]:
    try:
        return get_plan(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plan not found") from exc


@router.get("/{plan_id}/days/{day}")
def get_plan_day(plan_id: str, day: int) -> dict[str, object]:
    try:
        return get_today_step(plan_id, day)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class EnrollRequest(BaseModel):
    user_id: str


@router.post("/{plan_id}/enroll")
def enroll_in_plan(plan_id: str, req: EnrollRequest) -> dict[str, object]:
    with SessionLocal() as session:
        try:
            enrollment = enroll_user(plan_id, req.user_id, session)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Plan not found") from exc
        return {
            "enrollment_id": enrollment.id,
            "plan_id": plan_id,
            "current_day": enrollment.current_day,
            "status": enrollment.status,
        }


@router.get("/{plan_id}/progress")
def get_plan_progress(
    plan_id: str,
    user_id: str = Query(..., description="User ID"),
) -> dict[str, object]:
    with SessionLocal() as session:
        try:
            return get_progress(plan_id, user_id, session)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Plan not found") from exc


class CompleteDayRequest(BaseModel):
    user_id: str


@router.post("/{plan_id}/days/{day}/complete")
def complete_day(
    plan_id: str,
    day: int,
    req: CompleteDayRequest,
) -> dict[str, object]:
    with SessionLocal() as session:
        try:
            return mark_day_complete(plan_id, req.user_id, day, session)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Plan not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
