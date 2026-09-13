from datetime import date

from pydantic import BaseModel, Field


class DailyCheckin(BaseModel):
    mood_score: int = Field(..., ge=0, le=10)
    anxiety_score: int = Field(..., ge=0, le=10)
    sleep_hours: float = Field(..., ge=0, le=24)
    energy_score: int = Field(..., ge=0, le=10)
    note: str | None = Field(default=None, max_length=500)
    # 可选打卡日期：缺省=今天（旧行为）；携带时用于本地历史补传（幂等 upsert）。
    checkin_date: date | None = None
