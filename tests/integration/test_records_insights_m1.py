"""M1 记录与洞察：问卷历史 + AI 解读。

覆盖：
1. GET /v1/assessments 历史列表（来源标记、倒序、band）。
2. GET /v1/assessments/analysis —— LLM 正常（mock 生成）与 LLM 故障
   （_invoke 全链路必抛）两条路径；功能永不 500。
3. 商业化埋点：assessment_submitted / ai_analysis_requested / ai_analysis_served
   落 usage_events，且 metadata 不含情绪内容（伦理边界）。
4. 对话图内完成问卷 → source="chat"；页面提交 → source="panel"。
"""

from types import SimpleNamespace
from uuid import uuid4

import openai
from fastapi.testclient import TestClient

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.api.routes import assessments as assessments_routes
from psych_support_bot.app import app
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.models import UsageEvent
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.infra.llm import generation as llm_generation
from psych_support_bot.services.conversation import conversation_service

client = TestClient(app)

init_db()


def _seed_assessments(user_id: str) -> None:
    first = client.post(
        "/v1/assessments",
        json={"user_id": user_id, "assessment_type": "gad7", "score": 12},
    )
    assert first.status_code == 200
    second = client.post(
        "/v1/assessments",
        json={"user_id": user_id, "assessment_type": "gad7", "score": 6},
    )
    assert second.status_code == 200


def test_assessment_history_route_lists_records_with_source() -> None:
    user_id = f"m1-history-{uuid4().hex[:8]}"
    _seed_assessments(user_id)

    resp = client.get("/v1/assessments", params={"user_id": user_id})
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) >= 2
    # 页面直接提交 → source=panel
    assert all(r["source"] == "panel" for r in records)
    # 倒序：最新在前
    assert records[0]["created_at"] >= records[-1]["created_at"]
    assert records[0]["score"] == 6
    assert records[0]["severity_band"]
    assert records[0]["assessment_type"] == "gad7"
    assert isinstance(records[0]["needs_safety_followup"], bool)


def test_assessment_history_requires_user_id() -> None:
    resp = client.get("/v1/assessments")
    assert resp.status_code == 422


def test_assessment_analysis_llm_path(monkeypatch) -> None:
    user_id = f"m1-analysis-llm-{uuid4().hex[:8]}"
    _seed_assessments(user_id)

    def _fake_analysis(*, history_text: str, expected_language: str, fallback) -> str:
        assert "GAD7" in history_text
        return "过去两次测评分数从 12 分降到 6 分，整体在缓解。继续保持记录。"

    # 路由模块是直接 from-import 的名字，必须补丁到引用处
    monkeypatch.setattr(assessments_routes, "generate_assessment_history_analysis", _fake_analysis)

    resp = client.get(
        "/v1/assessments/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "llm"
    assert "12" in body["analysis"] and "6" in body["analysis"]
    assert body["history_count"] >= 2


def test_assessment_analysis_falls_back_when_llm_down(monkeypatch) -> None:
    """LLM 全链路故障（_invoke 必抛）→ 确定性统计文本，HTTP 200。"""
    user_id = f"m1-analysis-fb-{uuid4().hex[:8]}"
    _seed_assessments(user_id)

    class _AlwaysFailingModel:
        def invoke(self, _messages: object) -> object:
            raise openai.PermissionDeniedError(
                "Request rejected by content safety review",
                response=SimpleNamespace(status_code=403),
                body=None,
            )

    monkeypatch.setattr(llm_generation, "build_chat_model", lambda **_: _AlwaysFailingModel())

    resp = client.get(
        "/v1/assessments/analysis",
        params={"user_id": user_id, "expected_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_by"] == "fallback"
    # 确定性兜底：次数 + 首末分数 + 趋势方向
    assert "2 次测评" in body["analysis"]
    assert "12" in body["analysis"] and "6" in body["analysis"]
    assert "下降" in body["analysis"]


def test_assessment_analysis_empty_history_is_404() -> None:
    resp = client.get(
        "/v1/assessments/analysis",
        params={"user_id": f"m1-empty-{uuid4().hex[:8]}"},
    )
    assert resp.status_code == 404


def test_usage_events_recorded_without_mood_content() -> None:
    user_id = f"m1-usage-{uuid4().hex[:8]}"
    _seed_assessments(user_id)
    client.get("/v1/assessments/analysis", params={"user_id": user_id})

    with SessionLocal() as session:
        events = session.query(UsageEvent).filter(UsageEvent.user_id == user_id).order_by(UsageEvent.id).all()
    types = [e.event_type for e in events]
    assert "assessment_submitted" in types
    assert "ai_analysis_requested" in types
    assert "ai_analysis_served" in types
    # 伦理边界：埋点只含动作元数据（target/generated_by），绝无分数、band、note 等内容。
    for event in events:
        assert "score" not in event.metadata_json
        assert "band" not in event.metadata_json
        assert "note" not in event.metadata_json


def test_chat_questionnaire_completion_marks_source_chat() -> None:
    """对话图内完成的问卷落 source=chat，与页面提交共享同一历史。"""
    user_id = f"m1-chat-src-{uuid4().hex[:8]}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 GAD-7"),
            session=session,
        )
        assert start.mode == "assessment"
        # 依次答完 7 题
        for value in ["2", "2", "2", "2", "2", "2", "2"]:
            conversation_service.respond(
                ConversationRequest(user_id=user_id, message=value),
                session=session,
            )

    resp = client.get("/v1/assessments", params={"user_id": user_id})
    assert resp.status_code == 200
    records = resp.json()
    assert records, "chat 侧完成的问卷应出现在历史列表"
    assert all(r["source"] == "chat" for r in records)


def test_assessment_analysis_filters_by_assessment_type() -> None:
    """assessment_type 过滤：只统计指定量表的记录（结果详情页按量表出解读）。"""
    user_id = f"m1-analysis-filter-{uuid4().hex[:8]}"
    _seed_assessments(user_id)  # 2 条 gad7
    other = client.post(
        "/v1/assessments",
        json={"user_id": user_id, "assessment_type": "phq9", "score": 3},
    )
    assert other.status_code == 200

    # 无过滤：3 条
    resp = client.get("/v1/assessments/analysis", params={"user_id": user_id})
    assert resp.status_code == 200
    assert resp.json()["history_count"] == 3

    # gad7 过滤：2 条
    resp = client.get(
        "/v1/assessments/analysis",
        params={"user_id": user_id, "assessment_type": "gad7"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["history_count"] == 2

    # 过滤后无记录的量表 → 404 空态
    resp = client.get(
        "/v1/assessments/analysis",
        params={"user_id": user_id, "assessment_type": "isi"},
    )
    assert resp.status_code == 404
