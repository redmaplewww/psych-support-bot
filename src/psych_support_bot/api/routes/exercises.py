from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from psych_support_bot.ai.tools.exercises import get_exercise_by_tag, list_all_exercises
from psych_support_bot.infra.db.exercise_repositories import (
    get_user_exercise_records,
    save_exercise_record,
)
from psych_support_bot.infra.db.repositories import record_usage_event
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.infra.llm.generation import (
    LLMUnavailableError,
    generate_exercise_history_analysis,
)

router = APIRouter(prefix="/v1/exercises", tags=["exercises"])


@router.get("")
def list_exercises() -> dict[str, list[str]]:
    return list_all_exercises()


class ExerciseCompleteRequest(BaseModel):
    reflection_note: str | None = None


class ExerciseRecordItem(BaseModel):
    exercise_tag: str
    source: str
    reflection_note: str
    completed_at: str


class ExerciseAnalysisResponse(BaseModel):
    analysis: str
    count: int
    generated_by: str  # "llm" | "fallback"


# 注意路由顺序：/records* 必须声明在 /{exercise_tag} 之前，否则 "records"
# 会被当作练习 tag 匹配走 404。
@router.get("/records", response_model=list[ExerciseRecordItem])
def list_exercise_records(
    user_id: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[ExerciseRecordItem]:
    """练习历史（对话内与页面完成共用一张表，source 仅作来源标记）。"""
    records = get_user_exercise_records(session, user_id, limit=limit)
    return [
        ExerciseRecordItem(
            exercise_tag=record.exercise_tag,
            source=record.source,
            reflection_note=record.reflection_note,
            completed_at=record.completed_at.isoformat(),
        )
        for record in records
    ]


@router.get("/records/analysis", response_model=ExerciseAnalysisResponse)
def get_exercise_records_analysis(
    user_id: str = Query(..., min_length=1),
    expected_language: str = Query("zh", pattern="^(zh|en)$"),
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> ExerciseAnalysisResponse:
    """AI 练习分析（独立端点 = 将来的付费墙锚点）。

    LLM 不可用时确定性统计文本兜底——功能永不 500。
    """
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

    record_usage_event(
        session, user_id, "ai_analysis_served", target="exercises", generated_by=generated_by
    )
    session.commit()
    return ExerciseAnalysisResponse(
        analysis=analysis, count=len(records), generated_by=generated_by
    )


@router.post("/{exercise_tag}/complete", response_model=ExerciseRecordItem)
def complete_exercise(
    exercise_tag: str,
    payload: ExerciseCompleteRequest | None = None,
    user_id: str = Query(..., min_length=1),
    source: str = Query("panel", pattern="^(chat|panel)$"),
    session: Session = Depends(get_db_session),
) -> ExerciseRecordItem:
    """练习完成上报（页面练习用，source=panel；对话内完成走图内联动落库）。"""
    exercise = get_exercise_by_tag(exercise_tag)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    note = (payload.reflection_note or "").strip() if payload else ""
    record = save_exercise_record(
        session, user_id, exercise_tag, source=source, reflection_note=note
    )
    return ExerciseRecordItem(
        exercise_tag=record.exercise_tag,
        source=record.source,
        reflection_note=record.reflection_note,
        completed_at=record.completed_at.isoformat(),
    )


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
