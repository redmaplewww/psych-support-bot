import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.ai.exercise_ai import generate_exercise_guidance
from psych_support_bot.ai.tools.exercises import get_exercise_by_tag, list_all_exercises
from psych_support_bot.api.auth import request_user_id
from psych_support_bot.domain import consents
from psych_support_bot.infra.db.exercise_repositories import (
    get_exercise_record_by_id,
    get_user_exercise_records,
    save_exercise_record,
)
from psych_support_bot.infra.db.repositories import record_usage_event
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.infra.llm.generation import (
    LLMUnavailableError,
    generate_exercise_history_analysis,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/exercises", tags=["exercises"])


@router.get("")
def list_exercises() -> dict[str, list[str]]:
    return list_all_exercises()


class ExerciseCompleteRequest(BaseModel):
    reflection_note: str | None = None
    # 面板分步回答（20260904 报告化）：与须知确认绑定，用于 AI 反馈与报告。
    step_responses: list[str] = Field(default_factory=list, max_length=32)
    disclaimer_version: str = Field("", max_length=32)
    consent_acknowledged: bool = False


class ExerciseRecordItem(BaseModel):
    exercise_tag: str
    source: str
    reflection_note: str
    completed_at: str
    id: int | None = None
    step_responses: list[str] = Field(default_factory=list)
    ai_feedback: str = ""
    risk_flag: str = ""


class ExerciseCompleteResponse(BaseModel):
    record: ExerciseRecordItem
    ai_feedback: str
    generated_by: str  # "llm" | "safety_pause" | "fallback"
    risk_level: str


class ExerciseIntroResponse(BaseModel):
    tag: str
    name: str
    description: str
    step_count: int
    disclaimer_points: list[str]
    disclaimer_version: str


class ExerciseGuidanceRequest(BaseModel):
    step_index: int = Field(0, ge=0)
    step_guide: str = Field("", max_length=2000)
    step_responses: list[str] = Field(default_factory=list, max_length=32)
    user_message: str = Field(..., min_length=1, max_length=2000)
    dialog_history: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    expected_language: str = Field("zh", pattern="^(zh|en)$")


class ExerciseGuidanceResponse(BaseModel):
    reply: str
    messages: list[str]
    status: str  # "ok" | "risk_paused"
    suggested_action: str  # "continue" | "finish" | "seek_help"


class ExerciseAnalysisResponse(BaseModel):
    analysis: str
    count: int
    generated_by: str  # "llm" | "fallback"


# 注意路由顺序：/records* 必须声明在 /{exercise_tag} 之前，否则 "records"
# 会被当作练习 tag 匹配走 404。
@router.get("/records", response_model=list[ExerciseRecordItem])
def list_exercise_records(
    request: Request,
    user_id: str = "",
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[ExerciseRecordItem]:
    """练习历史（对话内与页面完成共用一张表，source 仅作来源标记）。

    复用 _record_item：列表必须携带 id（前端点击进详情报告）、
    ai_feedback（行内摘要）与 risk_flag（需要关照标记）。
    """
    user_id = request_user_id(request, user_id)
    records = get_user_exercise_records(session, user_id, limit=limit)
    return [_record_item(record) for record in records]


@router.get("/records/analysis", response_model=ExerciseAnalysisResponse)
def get_exercise_records_analysis(
    request: Request,
    user_id: str = "",
    expected_language: str = Query("zh", pattern="^(zh|en)$"),
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> ExerciseAnalysisResponse:
    """AI 练习分析（独立端点 = 将来的付费墙锚点）。

    LLM 不可用时确定性统计文本兜底——功能永不 500。
    """
    user_id = request_user_id(request, user_id)
    records = get_user_exercise_records(session, user_id, limit=limit)
    if not records:
        raise HTTPException(status_code=404, detail="No exercise history yet.")
    record_usage_event(session, user_id, "ai_analysis_requested", target="exercises")
    session.commit()

    # 只喂动作元数据（日期/练习/来源），反思笔记不进 LLM——伦理边界同 M1/M2。
    chronological = list(reversed(records))
    records_text = "\n".join(
        f"- {record.completed_at.date().isoformat()} {record.exercise_tag} (source={record.source})"
        for record in chronological
    )

    counts: dict[str, int] = {}
    for record in chronological:
        counts[record.exercise_tag] = counts.get(record.exercise_tag, 0) + 1
    top_tag = max(counts, key=lambda k: counts[k])
    zh = expected_language == "zh"

    def deterministic_fallback() -> str:
        if zh:
            return (
                f"最近你完成了 {len(chronological)} 次练习，"
                f"做得最多的是「{top_tag}」（{counts[top_tag]} 次）。"
                "保持节奏，下次可以试试同一类里的另一个练习，或换一种新类型。"
            )
        return (
            f"You completed {len(chronological)} exercises recently, most often "
            f"'{top_tag}' ({counts[top_tag]} times). Keep the rhythm and try a new "
            "type or a sibling exercise next."
        )

    fallback_text = deterministic_fallback()
    try:
        analysis = generate_exercise_history_analysis(
            records_text=records_text,
            expected_language=expected_language,
            fallback=lambda: fallback_text,
        )
        generated_by = "fallback" if analysis == fallback_text else "llm"
    except LLMUnavailableError:
        analysis = fallback_text
        generated_by = "fallback"

    record_usage_event(session, user_id, "ai_analysis_served", target="exercises", generated_by=generated_by)
    session.commit()
    return ExerciseAnalysisResponse(analysis=analysis, count=len(records), generated_by=generated_by)


@router.post("/{exercise_tag}/complete", response_model=ExerciseCompleteResponse)
def complete_exercise(
    exercise_tag: str,
    request: Request,
    payload: ExerciseCompleteRequest | None = None,
    user_id: str = "",
    source: str = Query("panel", pattern="^(chat|panel)$"),
    session: Session = Depends(get_db_session),
) -> ExerciseCompleteResponse:
    """练习完成上报（页面练习用，source=panel；对话内完成走图内联动落库）。

    20260904 报告化：面板完成携带 step_responses + 须知确认 → 服务端
    风险筛查（与 chat 同源规则层，crisis 词命中即拦截）→ AI 个人化反馈
    → 报告落库。对话内完成（chat source，无 step_responses）保持原语义。
    """
    user_id = request_user_id(request, user_id)
    # 面板反馈/引导均为 zh 出口——练习元数据取中文版（M3 单版本约定）
    exercise = get_exercise_by_tag(exercise_tag, language="zh")
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    payload = payload or ExerciseCompleteRequest()
    note = (payload.reflection_note or "").strip()
    step_responses = [r.strip() for r in (payload.step_responses or [])]

    # 每次练习确认（用户决策 2026-09-04）：面板路径必须带确认；chat 路径
    # 无须知页（对话上文即上下文），不校验。确认事件由 /consent 端点在
    # 勾选瞬间落库（用户可能确认后中途放弃，确认记录不依赖完成）。
    if source == "panel" and not payload.consent_acknowledged:
        raise HTTPException(status_code=403, detail="Exercise disclaimer must be acknowledged")

    ai_feedback = ""
    generated_by = "fallback"
    risk_level = "low"
    if source == "panel":
        feedback, generated_by, risk_level = _build_exercise_feedback(
            exercise=exercise, step_responses=step_responses, user_id=user_id
        )
        ai_feedback = feedback

    record = save_exercise_record(
        session,
        user_id,
        exercise_tag,
        source=source,
        reflection_note=note,
        step_responses=step_responses,
        ai_feedback=ai_feedback,
        risk_flag=risk_level,
    )
    if source == "panel" and generated_by in {"llm", "safety_pause"}:
        record_usage_event(
            session, user_id, "exercise_feedback_served", exercise_tag=exercise_tag, generated_by=generated_by
        )
        session.commit()
    return ExerciseCompleteResponse(
        record=_record_item(record),
        ai_feedback=ai_feedback,
        generated_by=generated_by,
        risk_level=risk_level,
    )


def _build_exercise_feedback(*, exercise: dict, step_responses: list[str], user_id: str) -> tuple[str, str, str]:
    """风险筛查 + AI 反馈。返回 (feedback, generated_by, risk_level)。

    exercise 已按语言解析（中文请求传中文元数据——生成 prompt 的练习
    名称/描述与用户语言一致）。
    """
    from psych_support_bot.ai.exercise_ai import generate_exercise_feedback

    try:
        feedback, generated_by = generate_exercise_feedback(
            exercise_name=str(exercise.get("name", "")),
            exercise_description=str(exercise.get("description", "")),
            step_guides=[str(s) for s in exercise.get("steps", [])],
            step_responses=step_responses,
            expected_language="zh",
        )
    except Exception:
        logger.exception("Exercise feedback pipeline failed for user %s", user_id)
        feedback, generated_by = "", "fallback"
    risk_level = "elevated" if generated_by == "safety_pause" else "low"
    return feedback, generated_by, risk_level


def _record_item(record, *, risk_flag: str = "") -> ExerciseRecordItem:
    try:
        steps = json.loads(record.step_responses_json or "[]")
    except json.JSONDecodeError:
        steps = []
    flag = risk_flag or getattr(record, "risk_flag", "") or ""
    return ExerciseRecordItem(
        id=record.id,
        exercise_tag=record.exercise_tag,
        source=record.source,
        reflection_note=record.reflection_note,
        completed_at=record.completed_at.isoformat(),
        step_responses=steps,
        ai_feedback=record.ai_feedback or "",
        risk_flag=flag if flag in {"elevated", "high", "critical"} else "",
    )


@router.get("/records/{record_id}", response_model=ExerciseRecordItem)
def get_exercise_record_detail(
    record_id: int,
    request: Request,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> ExerciseRecordItem:
    """单条练习报告详情（本人可见；user_id 绑定校验防越权）。"""
    user_id = request_user_id(request, user_id)
    record = get_exercise_record_by_id(session, user_id, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Exercise record not found")
    return _record_item(record)


@router.get("/{exercise_tag}/intro", response_model=ExerciseIntroResponse)
def get_exercise_intro(
    exercise_tag: str,
    lang: str = Query("zh", pattern="^(zh|en|)$"),
) -> ExerciseIntroResponse:
    """练习须知页数据（条款文本单一事实源在后端，法务调整不改前端）。

    练习元数据（名称/描述）按 lang 出单版本——M3 中文化约定，无中文版
    的 tag 原样回落英文。
    """
    exercise = get_exercise_by_tag(exercise_tag, language=lang)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    steps = exercise.get("steps", []) or []
    return ExerciseIntroResponse(
        tag=exercise_tag,
        name=str(exercise.get("name", exercise_tag)),
        description=str(exercise.get("description", "")),
        step_count=len(steps),
        disclaimer_points=consents.EXERCISE_DISCLAIMER_ZH,
        disclaimer_version=consents.DISCLAIMER_VERSION,
    )


class ExerciseConsentRequest(BaseModel):
    disclaimer_version: str = Field("", max_length=32)


@router.post("/{exercise_tag}/consent")
def acknowledge_exercise_intro(
    exercise_tag: str,
    request: Request,
    payload: ExerciseConsentRequest,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """须知确认（每次练习确认，勾选瞬间落库；完成上报再强校验 ack 位）。"""
    user_id = request_user_id(request, user_id)
    exercise = get_exercise_by_tag(exercise_tag)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    record_usage_event(
        session,
        user_id,
        "exercise_consent",
        exercise_tag=exercise_tag,
        disclaimer_version=payload.disclaimer_version or consents.DISCLAIMER_VERSION,
    )
    session.commit()
    return {"status": "acknowledged", "disclaimer_version": consents.DISCLAIMER_VERSION}


@router.post("/{exercise_tag}/guidance", response_model=ExerciseGuidanceResponse)
def exercise_guidance(
    exercise_tag: str,
    request: Request,
    payload: ExerciseGuidanceRequest,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> ExerciseGuidanceResponse:
    """练习对话引导（单步内多轮，P2）：风险筛查与 chat 同步 + 红线接入。

    风险命中（elevated/high/critical）时不生成常规引导——返回危机安抚
    口径（build_crisis_reply），status=risk_paused，前端停止引导并展示
    求助资源（"即时引导用户寻求心理帮助"）。
    """
    user_id = request_user_id(request, user_id)
    # 练习元数据按引导语言出单版本（zh 引导喂中文练习名/描述）
    exercise = get_exercise_by_tag(exercise_tag, language=payload.expected_language)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    reply, status = generate_exercise_guidance(
        exercise_name=str(exercise.get("name", "")),
        exercise_description=str(exercise.get("description", "")),
        current_step_index=payload.step_index,
        step_guide=payload.step_guide,
        step_responses=payload.step_responses,
        user_message=payload.user_message,
        dialog_history=payload.dialog_history,
        expected_language=payload.expected_language,
    )
    record_usage_event(session, user_id, "exercise_guidance_used", exercise_tag=exercise_tag, status=status)
    session.commit()
    return ExerciseGuidanceResponse(
        reply=reply,
        messages=_split_messages(reply),
        status=status,
        suggested_action="seek_help" if status == "risk_paused" else "continue",
    )


def _split_messages(text: str) -> list[str]:
    import re

    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return [] if len(parts) <= 1 else parts


@router.get("/{exercise_tag}")
def get_exercise(
    exercise_tag: str,
    lang: str = Query("", pattern="^(zh|en|)$"),
) -> dict:
    """练习内容；lang=zh 且该练习有中文版时返回中文版本（不做中英对照）。"""
    exercise = get_exercise_by_tag(exercise_tag, language=lang)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return exercise
