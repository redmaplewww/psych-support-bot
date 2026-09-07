import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import request_user_id
from psych_support_bot.domain import consents
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
    record_usage_event,
    save_assessment,
)
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.infra.llm.generation import LLMUnavailableError, generate_assessment_history_analysis

router = APIRouter(prefix="/v1/assessments", tags=["assessments"])


class AssessmentRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    assessment_type: AssessmentType
    score: int | None = Field(default=None, ge=0)
    answers: list[int] | None = None


class AssessmentHistoryItem(BaseModel):
    assessment_type: str
    score: int
    severity_band: str
    source: str
    created_at: str
    needs_safety_followup: bool = False


class AssessmentAnalysisResponse(BaseModel):
    analysis: str
    history_count: int
    generated_by: str  # "llm" | "fallback"


@router.get("", response_model=list[AssessmentHistoryItem])
def list_assessment_history(
    request: Request,
    user_id: str = "",
    limit: int = Query(20, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> list[AssessmentHistoryItem]:
    """问卷历史（对话内与页面提交合并存储，source 仅作来源标记）。"""
    user_id = request_user_id(request, user_id)
    records = get_user_assessments(session, user_id, limit=limit)
    return [
        AssessmentHistoryItem(
            assessment_type=record.assessment_type,
            score=record.score,
            severity_band=record.severity_band,
            source=record.source,
            created_at=record.created_at.isoformat(),
            needs_safety_followup=record.needs_safety_followup,
        )
        for record in records
    ]


@router.get("/analysis", response_model=AssessmentAnalysisResponse)
def get_assessment_analysis(
    request: Request,
    user_id: str = "",
    expected_language: str = Query("zh", pattern="^(zh|en)$"),
    limit: int = Query(10, ge=1, le=50),
    assessment_type: str | None = Query(None, description="按量表过滤（phq9/gad7/isi）；缺省=全部量表"),
    session: Session = Depends(get_db_session),
) -> AssessmentAnalysisResponse:
    """AI 趋势解读（独立端点 = 将来的付费墙锚点）。

    LLM 不可用时确定性统计文本兜底——功能永不 500。
    """
    user_id = request_user_id(request, user_id)
    records = get_user_assessments(session, user_id, limit=limit)
    if assessment_type:
        records = [r for r in records if r.assessment_type == assessment_type]
    if not records:
        raise HTTPException(status_code=404, detail="No assessment history yet.")
    record_usage_event(session, user_id, "ai_analysis_requested", target="assessments")
    # 埋点单独提交：路由会话结束不 commit（get_db_session 只读路径），不提交会随连接关闭回滚。
    session.commit()

    # 喂给 LLM 的只有动作元数据（日期/量表/分数/band），不含情绪叙述——伦理边界。
    chronological = list(reversed(records))
    history_lines = [
        f"- {record.created_at.date().isoformat()} {record.assessment_type.upper()} "
        f"{record.score}分 {record.severity_band} (source={record.source})"
        for record in chronological
    ]
    history_text = "\n".join(history_lines)

    first, last = chronological[0], chronological[-1]
    band_counts: dict[str, int] = {}
    for record in chronological:
        band_counts[record.severity_band] = band_counts.get(record.severity_band, 0) + 1
    bands_text = "、".join(f"{band}×{count}" for band, count in band_counts.items())

    def deterministic_fallback() -> str:
        zh = expected_language == "zh"
        delta = last.score - first.score
        direction = (
            (("下降" if delta < 0 else "上升") if delta else "持平")
            if zh
            else ("improved" if delta < 0 else ("worsened" if delta > 0 else "stable"))
        )
        if zh:
            return (
                f"你共完成 {len(chronological)} 次测评（{bands_text}）。"
                f"从 {first.created_at.date()} 的 {first.score} 分到 "
                f"{last.created_at.date()} 的 {last.score} 分，整体{direction} {abs(delta)} 分。"
                + (
                    "最近一次提示需要关注安全信号，建议聊聊。"
                    if any(r.needs_safety_followup for r in chronological)
                    else ""
                )
            )
        return (
            f"You completed {len(chronological)} screenings ({bands_text}). "
            f"From {first.score} on {first.created_at.date()} to {last.score} on "
            f"{last.created_at.date()}, your score {direction} by {abs(delta)} points."
        )

    fallback_text = deterministic_fallback()
    try:
        # _invoke 降级时返回 fallback 闭包的返回值 —— 与 fallback_text 精确比对即可判定来源
        analysis = generate_assessment_history_analysis(
            history_text=history_text,
            expected_language=expected_language,
            fallback=lambda: fallback_text,
        )
        generated_by = "fallback" if analysis == fallback_text else "llm"
    except LLMUnavailableError:
        analysis = fallback_text
        generated_by = "fallback"

    record_usage_event(session, user_id, "ai_analysis_served", target="assessments", generated_by=generated_by)
    session.commit()
    return AssessmentAnalysisResponse(analysis=analysis, history_count=len(records), generated_by=generated_by)


@router.get("/questionnaires", response_model=list[QuestionnaireGuide])
def get_questionnaires() -> list[QuestionnaireGuide]:
    guides = list_questionnaire_guides()
    for guide in guides:
        guide.disclaimer_points = consents.ASSESSMENT_DISCLAIMER_ZH
        guide.disclaimer_version = consents.DISCLAIMER_VERSION
    return guides


@router.get("/questionnaires/{assessment_type}", response_model=QuestionnaireGuide)
def get_questionnaire(assessment_type: AssessmentType) -> QuestionnaireGuide:
    guide = questionnaire_guide(assessment_type)
    guide.disclaimer_points = consents.ASSESSMENT_DISCLAIMER_ZH
    guide.disclaimer_version = consents.DISCLAIMER_VERSION
    return guide


@router.post("", response_model=AssessmentResult)
def create_assessment(
    payload: AssessmentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AssessmentResult:
    payload.user_id = request_user_id(request, payload.user_id)
    answer_set = AssessmentAnswerSet(answers=payload.answers) if payload.answers is not None else None
    # 面板提交为中文 UI 出口：解读文案固定 zh（与对话路径 expected_language 同语义）
    assessment = build_assessment_result(
        payload.assessment_type,
        score=payload.score,
        answers=answer_set,
        language="zh",
    )
    save_assessment(session, payload.user_id, assessment, source="panel")
    return assessment


@router.post("/sessions", response_model=QuestionnaireSessionView)
def start_questionnaire_session(
    payload: QuestionnaireSessionStartRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    payload.user_id = request_user_id(request, payload.user_id)
    # 须知确认强校验（20260904，每次评估必须）：评估有临床语义，确认必须
    # 落后端而非仅前端 gate。
    if not payload.consent_acknowledged:
        raise HTTPException(status_code=403, detail="Assessment disclaimer must be acknowledged")
    record_usage_event(
        session,
        payload.user_id,
        "assessment_consent",
        assessment_type=str(payload.assessment_type),
        disclaimer_version=payload.disclaimer_version or consents.DISCLAIMER_VERSION,
    )
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
    request: Request,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    user_id = request_user_id(request, user_id)
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
    request: Request,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionView:
    user_id = request_user_id(request, user_id)
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
    request: Request,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> QuestionnaireSessionResult:
    user_id = request_user_id(request, user_id)
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
        language="zh",
    )
    save_assessment(session, completed.user_id, result, source="panel")
    return QuestionnaireSessionResult(session=session_view, result=result)


class UserAssessmentsResponse(BaseModel):
    user_id: str
    assessments: list[dict]


@router.get("/users/{user_id}/history", response_model=UserAssessmentsResponse)
def get_assessment_history(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> UserAssessmentsResponse:
    user_id = request_user_id(request, user_id)
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
