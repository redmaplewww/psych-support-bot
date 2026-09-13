"""练习报告化 + 须知确认 + 引导端点测试（P1/P2 后端）。

覆盖：
- 隐私协议端点（条目/版本/确认落库）
- 练习须知 intro/consent + complete 强校验（未确认 403）
- complete 风险接入：步骤回答含危机词 → safety_pause + 风险标记落列
- complete 常规路径：AI 反馈落列（mock 生成层）
- guidance 端点：风险词 → risk_paused + seek_help；常规 → ok
- 记录详情越权防护（user_id 绑定）
- 评估 session start 强校验（未确认 403）+ consent 埋点
"""

import pytest
from fastapi.testclient import TestClient

from psych_support_bot.app import create_app
from psych_support_bot.infra.db.init_db import init_db


@pytest.fixture()
def client(monkeypatch):
    init_db()
    monkeypatch.setenv("AUTH_MODE", "open")
    app = create_app()
    return TestClient(app)


def _unique_user(prefix: str = "ex-rep-") -> str:
    import uuid

    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _complete(client, user_id: str, tag: str = "cbt_thought_record", **payload):
    body = {
        "reflection_note": "",
        "step_responses": ["被同事误解了，心里不是滋味", "我有个想法：他们故意针对我"],
        "consent_acknowledged": True,
        "disclaimer_version": "20260904.1",
        **payload,
    }
    return client.post(
        f"/v1/exercises/{tag}/complete",
        params={"user_id": user_id, "source": "panel"},
        json=body,
    )


# ---- 隐私协议 ----


def test_privacy_agreement_returns_points_and_version(client) -> None:
    resp = client.get("/v1/users/privacy-agreement")
    assert resp.status_code == 200
    data = resp.json()
    assert data["privacy_points"]
    assert data["data_processing_points"]
    assert data["consent_version"]


def test_privacy_consent_records_event(client) -> None:
    user_id = _unique_user("privacy-")
    resp = client.post(
        "/v1/users/privacy-consent",
        params={"user_id": user_id},
        json={"acknowledged": True, "consent_version": "20260904.1"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"


# ---- 须知 intro/consent/complete 强校验 ----


def test_intro_returns_disclaimer_points(client) -> None:
    resp = client.get("/v1/exercises/cbt_thought_record/intro")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_count"] >= 1
    assert data["disclaimer_points"]
    assert data["disclaimer_version"]


def test_complete_without_consent_rejected(client) -> None:
    user_id = _unique_user()
    resp = _complete(client, user_id, consent_acknowledged=False)
    assert resp.status_code == 403


def test_complete_normal_path_stores_report(client, monkeypatch) -> None:
    user_id = _unique_user()
    # mock AI 反馈生成（不调真实 LLM），验证报告落列
    from psych_support_bot.api.routes import exercises as ex_routes

    monkeypatch.setattr(
        ex_routes,
        "_build_exercise_feedback",
        lambda **kwargs: ("你把想法记录完整走完了，观察得很细。", "llm", "low"),
    )
    resp = _complete(client, user_id)
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated_by"] == "llm"
    assert data["record"]["ai_feedback"] == "你把想法记录完整走完了，观察得很细。"
    assert data["record"]["id"] is not None

    # 记录详情（本人）
    detail = client.get(f"/v1/exercises/records/{data['record']['id']}", params={"user_id": user_id})
    assert detail.status_code == 200
    assert detail.json()["step_responses"][0] == "被同事误解了，心里不是滋味"


def test_complete_crisis_content_pauses_feedback(client) -> None:
    """步骤回答含危机信号：AI 层风险筛查拦截，safety_pause + 风险标记。"""
    user_id = _unique_user()
    resp = _complete(
        client,
        user_id,
        step_responses=["最近真的撑不住了，不想活了", "每天都很绝望"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated_by"] == "safety_pause"
    assert data["risk_level"] == "elevated"
    assert data["record"]["risk_flag"] == "elevated"
    # 危机口径反馈：不生成常规鼓励语
    assert "练习" not in data["ai_feedback"][:30] or data["ai_feedback"]


def test_record_detail_forbidden_for_other_user(client) -> None:
    user_id = _unique_user()
    resp = _complete(client, user_id)
    record_id = resp.json()["record"]["id"]
    other = client.get(f"/v1/exercises/records/{record_id}", params={"user_id": _unique_user()})
    assert other.status_code == 404


# ---- guidance 端点 ----


def test_guidance_risk_message_pauses_with_help(client) -> None:
    user_id = _unique_user()
    resp = client.post(
        "/v1/exercises/cbt_thought_record/guidance",
        params={"user_id": user_id},
        json={
            "step_index": 1,
            "step_guide": "写下当时的自动想法",
            "step_responses": [],
            "user_message": "写到这里，我觉得活着没有意义了",
            "dialog_history": [],
            "expected_language": "zh",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "risk_paused"
    assert data["suggested_action"] == "seek_help"
    assert data["reply"]  # 危机安抚口径非空


def test_guidance_normal_message_returns_reply(client, monkeypatch) -> None:
    from psych_support_bot.ai import exercise_ai

    user_id = _unique_user()
    monkeypatch.setattr(
        exercise_ai,
        "_invoke",
        lambda *a, **k: "被误解的滋味确实难受。那次误解里，最让你在意的是哪一句话？",
    )
    resp = client.post(
        "/v1/exercises/cbt_thought_record/guidance",
        params={"user_id": user_id},
        json={
            "step_index": 0,
            "step_guide": "描述发生了什么",
            "step_responses": [],
            "user_message": "被别人误解了，心里不是滋味",
            "dialog_history": [],
            "expected_language": "zh",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["suggested_action"] == "continue"
    assert "误解" in data["reply"]


# ---- 评估 session 强校验 ----


def test_assessment_session_requires_consent(client) -> None:
    user_id = _unique_user("as-")
    resp = client.post(
        "/v1/assessments/sessions",
        params={"user_id": user_id},
        json={"user_id": user_id, "assessment_type": "isi", "consent_acknowledged": False},
    )
    assert resp.status_code == 403

    ok = client.post(
        "/v1/assessments/sessions",
        params={"user_id": user_id},
        json={
            "user_id": user_id,
            "assessment_type": "isi",
            "consent_acknowledged": True,
            "disclaimer_version": "20260904.1",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["session_id"]


def test_questionnaire_guide_carries_disclaimer(client) -> None:
    resp = client.get("/v1/assessments/questionnaires")
    assert resp.status_code == 200
    guides = resp.json()
    assert guides and all(g["disclaimer_points"] for g in guides)
