import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.domain.assessments.schemas import (
    AssessmentAnswerSet,
    AssessmentResult,
    AssessmentType,
    QuestionnaireGuide,
    QuestionnaireSessionAnswerRequest,
    QuestionnaireSessionResult,
    QuestionnaireSessionStartRequest,
    QuestionnaireSessionView,
)
from psych_support_bot.domain.assessments.service import (
    build_assessment_result,
    build_questionnaire_session_view,
    list_questionnaire_guides,
    questionnaire_guide,
    score_from_answers,
)
from psych_support_bot.infra.db.repositories import (
    append_questionnaire_answer,
    complete_questionnaire_session,
    create_questionnaire_session,
    get_questionnaire_session,
    get_user_assessments,
    save_assessment,
)
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/assessments", tags=["assessments"])


class AssessmentRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    assessment_type: AssessmentType
    score: int | None = Field(default=None, ge=0)
    answers: list[int] | None = None


@router.get("/questionnaires", response_model=list[QuestionnaireGuide])
def get_questionnaires() -> list[QuestionnaireGuide]:
    return list_questionnaire_guides()


@router.get("/questionnaires/{assessment_type}", response_model=QuestionnaireGuide)
def get_questionnaire(assessment_type: AssessmentType) -> QuestionnaireGuide:
    return questionnaire_guide(assessment_type)


@router.post("", response_model=AssessmentResult)
def create_assessment(
    payload: AssessmentRequest,
    session: Session = Depends(get_db_session),
) -> AssessmentResult:
    answer_set = AssessmentAnswerSet(answers=payload.answers) if payload.answers is not None else None
    assessment = build_assessment_result(
        payload.assessment_type,
        score=payload.score,
        answers=answer_set,
    )
    save_assessment(session, payload.user_id, assessment)
    return assessment


@router.post("/sessions", response_model=QuestionnaireSessionView)
def start_questionnaire_session(
    payload: QuestionnaireSessionStartRequest,
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    record = create_questionnaire_session(session, payload.user_id, payload.assessment_type)
    return build_questionnaire_session_view(
        session_id=record.id,
        user_id=record.user_id,
        assessment_type=payload.assessment_type,
        answers=[],
        status=record.status,
    )


@router.get("/sessions/{session_id}", response_model=QuestionnaireSessionView)
def get_questionnaire_session_view(
    session_id: str,
    user_id: str = Query(..., min_length=1, description="User ID for ownership verification"),
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    record = get_questionnaire_session(session, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Questionnaire session not found")
    if record.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")
    answers = json.loads(record.answers_json or "[]")
    return build_questionnaire_session_view(
        session_id=record.id,
        user_id=record.user_id,
        assessment_type=record.assessment_type,  # type: ignore[arg-type]
        answers=answers,
        status=record.status,
    )


@router.post("/sessions/{session_id}/answers", response_model=QuestionnaireSessionView)
def answer_questionnaire_session(
    session_id: str,
    payload: QuestionnaireSessionAnswerRequest,
    user_id: str = Query(..., min_length=1, description="User ID for ownership verification"),
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    record = get_questionnaire_session(session, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Questionnaire session not found")
    if record.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")
    if record.status == "completed":
        raise HTTPException(status_code=409, detail="Questionnaire session already completed")

    answers = json.loads(record.answers_json or "[]")
    max_answers = len(questionnaire_guide(record.assessment_type).items)  # type: ignore[arg-type]
    if len(answers) > max_answers:
        raise HTTPException(status_code=409, detail="All questions have already been answered")

    max_val = 3 if record.assessment_type in ("phq9", "gad7") else 4
    if not (0 <= payload.value <= max_val):
        raise HTTPException(
            status_code=422,
            detail=f"Answer value must be between 0 and {max_val} for {record.assessment_type}",
        )

    updated = append_questionnaire_answer(session, record, payload.value)
    updated_answers = json.loads(updated.answers_json or "[]")
    return build_questionnaire_session_view(
        session_id=updated.id,
        user_id=updated.user_id,
        assessment_type=updated.assessment_type,  # type: ignore[arg-type]
        answers=updated_answers,
        status=updated.status,
    )


@router.post("/sessions/{session_id}/complete", response_model=QuestionnaireSessionResult)
def complete_questionnaire_session_route(
    session_id: str,
    user_id: str = Query(..., min_length=1, description="User ID for ownership verification"),
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionResult:
    record = get_questionnaire_session(session, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Questionnaire session not found")
    if record.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    answers = json.loads(record.answers_json or "[]")
    assessment_type = record.assessment_type  # type: ignore[assignment]
    score_from_answers(assessment_type, AssessmentAnswerSet(answers=answers))
    completed = complete_questionnaire_session(session, record)
    session_view = build_questionnaire_session_view(
        session_id=completed.id,
        user_id=completed.user_id,
        assessment_type=assessment_type,
        answers=answers,
        status=completed.status,
    )
    result = build_assessment_result(
        assessment_type,
        answers=AssessmentAnswerSet(answers=answers),
    )
    save_assessment(session, completed.user_id, result)
    return QuestionnaireSessionResult(session=session_view, result=result)


class UserAssessmentsResponse(BaseModel):
    user_id: str
    assessments: list[dict]


@router.get("/users/{user_id}/history", response_model=UserAssessmentsResponse)
def get_assessment_history(
    user_id: str,
    session: Session = Depends(get_db_session),
) -> UserAssessmentsResponse:
    records = get_user_assessments(session, user_id)
    return UserAssessmentsResponse(
        user_id=user_id,
        assessments=[
            {
                "assessment_type": r.assessment_type,
                "score": r.score,
                "severity_band": r.severity_band,
                "plain_meaning": r.plain_meaning or None,
                "functional_impact": r.functional_impact or None,
                "care_consideration": r.care_consideration or None,
                "needs_safety_followup": r.needs_safety_followup,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    )
