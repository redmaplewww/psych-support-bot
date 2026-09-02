"""M2 记录与洞察：打卡历史 + 趋势 + AI 解读。

覆盖：
1. GET /v1/checkins 近 N 天历史（含笔记，只返回给本人）。
2. GET /v1/checkins/trend 结构化序列（升序 + 均值）。
3. GET /v1/checkins/analysis —— LLM 正常与故障两条路径，功能永不 500。
4. 埋点：checkin_created / checkin_backfilled / ai_analysis_requested / ai_analysis_served。
5. 幂等 upsert：同日重复提交合并为一条；checkin_date 补传本地历史；拒绝未来日期。
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import openai
from fastapi.testclient import TestClient

from psych_support_bot.api.routes import checkins as checkins_routes
from psych_support_bot.app import app
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.models import UsageEvent
from psych_support_bot.infra.db.session import SessionLocal

client = TestClient(app)

init_db()


def _post_checkin(
    user_id: str,
    mood: int,
    anxiety: int,
    note: str | None = None,
    checkin_date: str | None = None,
) -> None:
    payload: dict = {
        "mood_score": mood,
        "anxiety_score": anxiety,
        "sleep_hours": 7.0,
        "energy_score": 6,
        "note": note,
    }
    if checkin_date is not None:
        payload["checkin_date"] = checkin_date
    resp = client.post("/v1/checkins", params={"user_id": user_id}, json=payload)
    assert resp.status_code == 200


def test_checkin_history_route_returns_records() -> None:
    user_id = f"m2-history-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=6, anxiety=4, note="今天还行")

    resp = client.get("/v1/checkins", params={"user_id": user_id, "days": 30})
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    rec = records[0]
    assert rec["mood_score"] == 6
    assert rec["anxiety_score"] == 4
    assert rec["sleep_hours"] == 7.0
    assert rec["note"] == "今天还行"
    assert rec["date"]


def test_checkin_trend_route_structured_series() -> None:
    user_id = f"m2-trend-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=4, anxiety=7)
    # 同日第二条 POST 走 upsert 覆盖（旧行为是新增一行）：趋势序列仍只有今天一个点
    _post_checkin(user_id, mood=7, anxiety=3)

    resp = client.get("/v1/checkins/trend", params={"user_id": user_id, "days": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert len(body["points"]) == 1
    assert body["points"][-1]["mood_score"] == 7
    assert body["averages"]["mood_score"] == 7.0
    assert body["days"] == 30


def test_checkin_trend_empty_user_returns_empty_series() -> None:
    resp = client.get("/v1/checkins/trend", params={"user_id": f"m2-empty-{uuid4().hex[:8]}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["points"] == []
    assert body["averages"] == {}


def test_checkin_analysis_llm_path(monkeypatch) -> None:
    user_id = f"m2-analysis-llm-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=4, anxiety=7)
    # upsert 语义：同日再打一次是覆盖而非新增
    _post_checkin(user_id, mood=7, anxiety=3)

    captured: dict = {}

    def _fake_analysis(*, trend_text: str, expected_language: str, fallback) -> str:
        captured["trend_text"] = trend_text
        return "近两天你的焦虑从 7 降到 3，睡眠稳定在 7 小时，心情随之回升。"

    monkeypatch.setattr(checkins_routes, "generate_checkin_trend_analysis", _fake_analysis)

    resp = client.get(
        "/v1/checkins/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "llm"
    assert "焦虑" in body["analysis"]
    # 伦理边界：喂给 LLM 的只有数值序列，不含 note 文字
    _post_checkin(user_id, mood=6, anxiety=4, note="私密心事不该进 prompt")
    monkeypatch.setattr(checkins_routes, "generate_checkin_trend_analysis", _fake_analysis)
    client.get("/v1/checkins/analysis", params={"user_id": user_id})
    assert "私密心事" not in captured["trend_text"]


def test_checkin_analysis_falls_back_when_llm_down(monkeypatch) -> None:
    user_id = f"m2-analysis-fb-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=3, anxiety=8)
    _post_checkin(user_id, mood=5, anxiety=6)

    class _AlwaysFailingModel:
        def invoke(self, _messages: object) -> object:
            raise openai.PermissionDeniedError(
                "Request rejected by content safety review",
                response=SimpleNamespace(status_code=403),
                body=None,
            )

    # 直接替换 build_chat_model，打穿 _invoke 全链路（重试/降级 → fallback 闭包）
    from psych_support_bot.infra.llm import generation as llm_generation

    monkeypatch.setattr(llm_generation, "build_chat_model", lambda **_: _AlwaysFailingModel())

    resp = client.get(
        "/v1/checkins/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "fallback"
    assert "平均心情" in body["analysis"]
    assert "1 天打卡" in body["analysis"]


def test_checkin_analysis_empty_history_is_404() -> None:
    resp = client.get(
        "/v1/checkins/analysis",
        params={"user_id": f"m2-empty-{uuid4().hex[:8]}"},
    )
    assert resp.status_code == 404


def test_checkin_usage_events_recorded() -> None:
    user_id = f"m2-usage-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=6, anxiety=4)
    client.get("/v1/checkins/analysis", params={"user_id": user_id})

    with SessionLocal() as session:
        events = session.query(UsageEvent).filter(UsageEvent.user_id == user_id).order_by(UsageEvent.id).all()
    types = [e.event_type for e in events]
    assert "checkin_created" in types
    assert "ai_analysis_requested" in types
    assert "ai_analysis_served" in types
    # 伦理边界：埋点不含情绪数值与备注内容
    for event in events:
        assert "mood" not in event.metadata_json
        assert "note" not in event.metadata_json


def _usage_event_types(user_id: str) -> list[str]:
    with SessionLocal() as session:
        events = (
            session.query(UsageEvent)  # type: ignore[attr-defined]
            .filter(UsageEvent.user_id == user_id)  # 绑定参数，非字符串拼接
            .order_by(UsageEvent.id)
            .all()
        )
    return [e.event_type for e in events]


def test_checkin_upsert_same_day_overwrites() -> None:
    user_id = f"m2-upsert-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=4, anxiety=7, note="第一次")
    _post_checkin(user_id, mood=8, anxiety=2, note="更新后")

    resp = client.get("/v1/checkins", params={"user_id": user_id, "days": 30})
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["mood_score"] == 8
    assert records[0]["anxiety_score"] == 2
    assert records[0]["note"] == "更新后"
    # 同日二次提交走更新路径：只记一次 checkin_created
    assert _usage_event_types(user_id) == ["checkin_created"]


def _today() -> date:
    return datetime.now(tz=UTC).date()


def test_checkin_backfill_with_explicit_date() -> None:
    user_id = f"m2-backfill-{uuid4().hex[:8]}"
    yesterday = str(_today() - timedelta(days=1))
    _post_checkin(user_id, mood=5, anxiety=5, checkin_date=yesterday)

    resp = client.get("/v1/checkins", params={"user_id": user_id, "days": 30})
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["date"] == yesterday
    # 补传埋点用 checkin_backfilled，与当日打卡计量分开
    assert _usage_event_types(user_id) == ["checkin_backfilled"]

    # 同日重复补传幂等：不新增记录、不重复埋点
    _post_checkin(user_id, mood=6, anxiety=4, checkin_date=yesterday)
    resp = client.get("/v1/checkins", params={"user_id": user_id, "days": 30})
    assert len(resp.json()) == 1
    assert _usage_event_types(user_id) == ["checkin_backfilled"]
    assert resp.json()[0]["mood_score"] == 6


def test_checkin_future_date_rejected() -> None:
    user_id = f"m2-future-{uuid4().hex[:8]}"
    resp = client.post(
        "/v1/checkins",
        params={"user_id": user_id},
        json={
            "mood_score": 5,
            "anxiety_score": 5,
            "sleep_hours": 7.0,
            "energy_score": 5,
            "note": None,
            "checkin_date": str(_today() + timedelta(days=1)),
        },
    )
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"]

    # 未落库
    resp = client.get("/v1/checkins", params={"user_id": user_id, "days": 30})
    assert resp.json() == []


def test_checkin_trend_multi_day_via_backfill_dates() -> None:
    user_id = f"m2-trend-bf-{uuid4().hex[:8]}"
    _post_checkin(user_id, mood=4, anxiety=7, checkin_date=str(_today() - timedelta(days=2)))
    _post_checkin(user_id, mood=7, anxiety=3, checkin_date=str(_today() - timedelta(days=1)))

    resp = client.get("/v1/checkins/trend", params={"user_id": user_id, "days": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    # 升序：最后一条是最新打卡
    assert body["points"][-1]["mood_score"] == 7
    assert body["averages"]["mood_score"] == 5.5
