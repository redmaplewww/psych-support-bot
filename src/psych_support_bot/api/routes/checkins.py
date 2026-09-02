from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from psych_support_bot.domain.checkins.schemas import DailyCheckin
from psych_support_bot.infra.db.repositories import (
    get_checkins_since,
    record_usage_event,
    save_checkin,
)
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.infra.llm.generation import (
    LLMUnavailableError,
    generate_checkin_trend_analysis,
)

router = APIRouter(prefix="/v1/checkins", tags=["checkins"])


@router.post("", response_model=DailyCheckin)
def create_checkin(
    payload: DailyCheckin,
    user_id: str,
    session: Session = Depends(get_db_session),
) -> DailyCheckin:
    """打卡落库（幂等 upsert）。

    携带 checkin_date 时为历史补传（本地未同步记录回传），不接受未来日期。
    """
    if payload.checkin_date and payload.checkin_date > date.today():  # noqa: DTZ011
        raise HTTPException(status_code=400, detail="checkin_date cannot be in the future.")
    save_checkin(session, user_id, payload)
    return payload


class CheckinRecordItem(BaseModel):
    date: str
    mood_score: int
    anxiety_score: int
    sleep_hours: float
    energy_score: int
    note: str


class CheckinTrendPoint(BaseModel):
    date: str
    mood_score: int
    anxiety_score: int
    sleep_hours: float
    energy_score: int


class CheckinTrendResponse(BaseModel):
    days: int
    count: int
    points: list[CheckinTrendPoint]  # 按日期升序，供前端直接连线
    averages: dict[str, float]


class CheckinAnalysisResponse(BaseModel):
    analysis: str
    count: int
    generated_by: str  # "llm" | "fallback"


@router.get("", response_model=list[CheckinRecordItem])
def list_checkin_records(
    user_id: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_db_session),
) -> list[CheckinRecordItem]:
    """打卡历史（记录查看免费；笔记只返回给用户本人）。"""
    records = get_checkins_since(session, user_id, days=days)
    return [
        CheckinRecordItem(
            date=record.checkin_date.isoformat(),
            mood_score=record.mood_score,
            anxiety_score=record.anxiety_score,
            sleep_hours=record.sleep_hours,
            energy_score=record.energy_score,
            note=record.note,
        )
        for record in records
    ]


@router.get("/trend", response_model=CheckinTrendResponse)
def get_checkin_trend(
    user_id: str = Query(..., min_length=1),
    days: int = Query(30, ge=7, le=365),
    session: Session = Depends(get_db_session),
) -> CheckinTrendResponse:
    """结构化趋势序列（供前端画 SVG 折线）。"""
    records = get_checkins_since(session, user_id, days=days)
    chronological = list(reversed(records))
    points = [
        CheckinTrendPoint(
            date=record.checkin_date.isoformat(),
            mood_score=record.mood_score,
            anxiety_score=record.anxiety_score,
            sleep_hours=record.sleep_hours,
            energy_score=record.energy_score,
        )
        for record in chronological
    ]
    count = len(points)
    averages = (
        {
            "mood_score": sum(p.mood_score for p in points) / count,
            "anxiety_score": sum(p.anxiety_score for p in points) / count,
            "sleep_hours": sum(p.sleep_hours for p in points) / count,
            "energy_score": sum(p.energy_score for p in points) / count,
        }
        if count
        else {}
    )
    return CheckinTrendResponse(days=days, count=count, points=points, averages=averages)


@router.get("/analysis", response_model=CheckinAnalysisResponse)
def get_checkin_analysis(
    user_id: str = Query(..., min_length=1),
    expected_language: str = Query("zh", pattern="^(zh|en)$"),
    days: int = Query(30, ge=7, le=365),
    session: Session = Depends(get_db_session),
) -> CheckinAnalysisResponse:
    """AI 趋势解读（独立端点 = 将来的付费墙锚点）。

    LLM 不可用时确定性统计文本兜底——功能永不 500。
    """
    records = get_checkins_since(session, user_id, days=days)
    if not records:
        raise HTTPException(status_code=404, detail="No check-in history yet.")
    record_usage_event(session, user_id, "ai_analysis_requested", target="checkins")
    session.commit()

    # 只喂数值序列，不带 note 文字——伦理边界：AI 分析基于数值规律，
    # 不做情绪叙述内容画像（备注只属于用户自己的记录）。
    chronological = list(reversed(records))
    trend_lines = [
        f"- {record.checkin_date.isoformat()} mood={record.mood_score} "
        f"anxiety={record.anxiety_score} sleep={record.sleep_hours}h energy={record.energy_score}"
        for record in chronological
    ]
    trend_text = "\n".join(trend_lines)

    def deterministic_fallback() -> str:
        n = len(chronological)
        avg = lambda key: sum(getattr(r, key) for r in chronological) / n
        half = max(1, n // 2)
        first_half = sum(r.mood_score for r in chronological[:half]) / half
        second_half = sum(r.mood_score for r in chronological[-half:]) / half
        delta = second_half - first_half
        if expected_language == "zh":
            direction = "有所回升" if delta > 0.5 else ("有所回落" if delta < -0.5 else "基本平稳")
            return (
                f"近 {n} 天打卡：平均心情 {avg('mood_score'):.1f}/10，焦虑 {avg('anxiety_score'):.1f}/10，"
                f"睡眠 {avg('sleep_hours'):.1f} 小时，精力 {avg('energy_score'):.1f}/10。"
                f"与前一阶段相比心情{direction}（{delta:+.1f}）。"
            )
        direction = "improved" if delta > 0.5 else ("declined" if delta < -0.5 else "stayed stable")
        return (
            f"{n} check-ins: average mood {avg('mood_score'):.1f}/10, anxiety "
            f"{avg('anxiety_score'):.1f}/10, sleep {avg('sleep_hours'):.1f}h, energy "
            f"{avg('energy_score'):.1f}/10. Mood {direction} ({delta:+.1f}) versus the earlier half."
        )

    fallback_text = deterministic_fallback()
    try:
        # _invoke 降级时返回 fallback 闭包的返回值 —— 精确比对判定来源
        analysis = generate_checkin_trend_analysis(
            trend_text=trend_text,
            expected_language=expected_language,
            fallback=lambda: fallback_text,
        )
        generated_by = "fallback" if analysis == fallback_text else "llm"
    except LLMUnavailableError:
        analysis = fallback_text
        generated_by = "fallback"

    record_usage_event(session, user_id, "ai_analysis_served", target="checkins", generated_by=generated_by)
    session.commit()
    return CheckinAnalysisResponse(analysis=analysis, count=len(records), generated_by=generated_by)
