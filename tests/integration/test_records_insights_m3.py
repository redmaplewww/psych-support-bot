"""M3 记录与洞察：练习记录 + AI 分析 + 练习中文化。

覆盖：
1. 迁移/repository：练习落库（此前练习完全不落库）。
2. API：POST /{tag}/complete（页面上报，source=panel）、GET /records、
   GET /records/analysis（LLM 路径 + 确定性降级 + 404）。
3. 对话图联动：对话中说"做完练习"自动落库（source=chat）。
4. 中文化：lang=zh 返回中文版内容，不做中英对照。
5. 埋点：exercise_completed / ai_analysis_requested / ai_analysis_served，
   反思笔记不进埋点（伦理边界）。
"""

from types import SimpleNamespace
from uuid import uuid4

import openai
from fastapi.testclient import TestClient

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.ai.tools.exercises import detect_completed_exercise, get_exercise_by_tag
from psych_support_bot.api.routes import exercises as exercises_routes
from psych_support_bot.app import app
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.models import UsageEvent
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service

client = TestClient(app)

init_db()


def test_detect_completed_exercise() -> None:
    assert detect_completed_exercise("我跟着做完了TIPP练习，感觉平静了一些") == "dbt_tipp"
    assert detect_completed_exercise("I finished the thought record exercise") == "cbt_thought_record"
    assert detect_completed_exercise("今天做完了") is None  # 没提练习 → 不记
    assert detect_completed_exercise("想做个呼吸练习") is None  # 想做 ≠ 做完
    assert detect_completed_exercise("") is None


def test_complete_and_list_exercise_records() -> None:
    user_id = f"m3-records-{uuid4().hex[:8]}"
    first = client.post(
        "/v1/exercises/dbt_tipp/complete",
        params={"user_id": user_id},
        json={"reflection_note": "做完之后手是凉的，但心跳慢下来了"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["exercise_tag"] == "dbt_tipp"
    assert body["source"] == "panel"
    assert "手是凉的" in body["reflection_note"]

    second = client.post(
        "/v1/exercises/cbt_thought_record/complete",
        params={"user_id": user_id},
        json=None,
    )
    assert second.status_code == 200
    assert second.json()["reflection_note"] == ""

    records = client.get("/v1/exercises/records", params={"user_id": user_id}).json()
    assert len(records) == 2
    # 倒序：最新在前
    assert records[0]["exercise_tag"] == "cbt_thought_record"
    assert {r["source"] for r in records} == {"panel"}


def test_complete_unknown_tag_is_404() -> None:
    resp = client.post(
        "/v1/exercises/not_a_real_exercise/complete",
        params={"user_id": f"m3-404-{uuid4().hex[:8]}"},
        json=None,
    )
    assert resp.status_code == 404


def test_exercise_analysis_llm_path(monkeypatch) -> None:
    user_id = f"m3-analysis-llm-{uuid4().hex[:8]}"
    for tag in ("dbt_tipp", "dbt_tipp", "act_defusion"):
        assert client.post(
            f"/v1/exercises/{tag}/complete", params={"user_id": user_id}, json=None
        ).status_code == 200

    def _fake_analysis(*, records_text: str, expected_language: str, fallback) -> str:
        assert "dbt_tipp" in records_text
        return "你最近偏向使用 DBT 类的平复技能，保持每周两次的节奏很好。"

    monkeypatch.setattr(exercises_routes, "generate_exercise_history_analysis", _fake_analysis)

    resp = client.get(
        "/v1/exercises/records/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "llm"
    assert "DBT" in body["analysis"]


def test_exercise_analysis_falls_back_when_llm_down(monkeypatch) -> None:
    user_id = f"m3-analysis-fb-{uuid4().hex[:8]}"
    assert client.post(
        "/v1/exercises/sleep_wind_down/complete", params={"user_id": user_id}, json=None
    ).status_code == 200

    class _AlwaysFailingModel:
        def invoke(self, _messages: object) -> object:
            raise openai.PermissionDeniedError(
                "Request rejected by content safety review",
                response=SimpleNamespace(status_code=403),
                body=None,
            )

    from psych_support_bot.infra.llm import generation as llm_generation

    monkeypatch.setattr(llm_generation, "build_chat_model", lambda **_: _AlwaysFailingModel())

    resp = client.get(
        "/v1/exercises/records/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "fallback"
    assert "1 次练习" in body["analysis"]
    assert "sleep_wind_down" in body["analysis"]


def test_exercise_analysis_empty_history_is_404() -> None:
    resp = client.get(
        "/v1/exercises/records/analysis",
        params={"user_id": f"m3-empty-{uuid4().hex[:8]}"},
    )
    assert resp.status_code == 404


def test_chat_exercise_completion_persists_with_source_chat() -> None:
    """对话图联动：用户说"做完练习"→ 自动落库 source=chat。"""
    user_id = f"m3-chat-ex-{uuid4().hex[:8]}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我刚才把想法记录练习做完了"),
            session=session,
        )

    records = client.get("/v1/exercises/records", params={"user_id": user_id}).json()
    assert len(records) == 1
    assert records[0]["exercise_tag"] == "cbt_thought_record"
    assert records[0]["source"] == "chat"


def test_exercise_zh_content_and_fallback() -> None:
    """中文化：有 overlay 的 tag 出中文，没有的回落英文，不做中英对照。"""
    zh = get_exercise_by_tag("dbt_tipp", language="zh")
    assert zh is not None
    assert zh["name"] == "DBT TIPP 危机技能"
    assert any("节奏呼吸" in step for step in zh["steps"])

    # 无中文版的知识库练习回落英文原版
    en = get_exercise_by_tag("act_defusion_tunnel", language="zh")
    assert en is not None
    assert en["name"].startswith("Defusion")

    assert get_exercise_by_tag("no_such_tag") is None

    api_zh = client.get("/v1/exercises/dbt_tipp", params={"lang": "zh"}).json()
    assert api_zh["name"] == "DBT TIPP 危机技能"
    api_en = client.get("/v1/exercises/dbt_tipp").json()
    assert api_en["name"].startswith("DBT TIPP Skills")


def test_exercise_usage_events_recorded_without_reflection_note() -> None:
    user_id = f"m3-usage-{uuid4().hex[:8]}"
    client.post(
        "/v1/exercises/dbt_wise_mind/complete",
        params={"user_id": user_id},
        json={"reflection_note": "私密反思内容"},
    )
    client.get("/v1/exercises/records/analysis", params={"user_id": user_id})

    with SessionLocal() as session:
        events = (
            session.query(UsageEvent)
            .filter(UsageEvent.user_id == user_id)
            .order_by(UsageEvent.id)
            .all()
        )
    types = [e.event_type for e in events]
    assert "exercise_completed" in types
    assert "ai_analysis_requested" in types
    assert "ai_analysis_served" in types
    # 伦理边界：埋点只含动作元数据，反思笔记内容不进埋点
    for event in events:
        assert "私密反思" not in event.metadata_json
        assert "reflection" not in event.metadata_json
