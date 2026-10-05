"""结构化风险通道（RiskEvent 接入跨轮升级）单测。

覆盖：
1. get_recent_risk_level：窗口命中/过期/无记录。
2. recent_risk_level=high 时 elevated 消息跨轮升级（结构化主来源）。
3. 结构化通道缺失时摘要/原话文本兜底仍生效（原 _has_previous_elevated 行为不变）。
4. 记录层文本仍不会触发升级（隔离红线回归）。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psych_support_bot.ai.nodes.risk_classifier import classify_risk
from psych_support_bot.infra.db.models import RiskEvent
from psych_support_bot.infra.db.repositories import get_recent_risk_level
from psych_support_bot.infra.db.session import SessionLocal


def _state(user_message: str, recent_risk_level: str = "", user_history_text: str = ""):
    return {
        "user_message": user_message,
        "memory_summary": "",
        "user_history_text": user_history_text,
        "recent_risk_level": recent_risk_level,
        "safety_floor_risk_level": "",
        "mode": "support",
        "turn_count": 2,
        "expected_language": "zh",
    }


def _add_risk_event(user_id: str, *, level: str, age_days: float) -> None:
    with SessionLocal() as session:
        session.add(
            RiskEvent(
                user_id=user_id,
                session_id=f"s-{uuid4().hex[:8]}",
                risk_level=level,
                risk_reason="test",
                created_at=datetime.now(UTC) - timedelta(days=age_days),
            )
        )
        session.commit()


# --- get_recent_risk_level ---


def test_recent_risk_level_hit_within_window() -> None:
    user_id = f"re-hit-{uuid4().hex[:8]}"
    _add_risk_event(user_id, level="high", age_days=2)
    with SessionLocal() as session:
        assert get_recent_risk_level(session, user_id) == "high"


def test_recent_risk_level_ignores_expired() -> None:
    user_id = f"re-old-{uuid4().hex[:8]}"
    _add_risk_event(user_id, level="critical", age_days=10)
    with SessionLocal() as session:
        assert get_recent_risk_level(session, user_id) == ""


def test_recent_risk_level_ignores_elevated_events() -> None:
    """RiskEvent 只记 high/critical；elevated 行即使手工插入也不参与结构化通道。"""
    user_id = f"re-elv-{uuid4().hex[:8]}"
    _add_risk_event(user_id, level="elevated", age_days=1)
    with SessionLocal() as session:
        assert get_recent_risk_level(session, user_id) == ""


# --- 跨轮升级（结构化主来源） ---


def test_structured_high_triggers_upgrade() -> None:
    """elevated 消息 + 窗口内 high RiskEvent → 升级 high（无需摘要标记）。"""
    result = classify_risk(_state("我还是觉得没意义，撑不住了", recent_risk_level="high"))
    assert result["risk_result"].risk_level == "high"
    assert result["risk_result"].needs_crisis_mode is True
    assert "cumulative_elevated" in result["risk_result"].risk_types


def test_structured_critical_triggers_upgrade() -> None:
    result = classify_risk(_state("我还是觉得没意义，撑不住了", recent_risk_level="critical"))
    assert result["risk_result"].risk_level == "high"


def test_structured_empty_falls_back_to_text_channel() -> None:
    """结构化通道为空时，摘要/原话文本兜底判定仍生效（原行为回归）。"""
    result = classify_risk(
        _state(
            "我还是觉得没意义，撑不住了",
            user_history_text="User (mode=support, risk=elevated): 我觉得很绝望",
        )
    )
    assert result["risk_result"].risk_level == "high"


def test_no_structured_no_text_stays_elevated() -> None:
    result = classify_risk(_state("我觉得很绝望", recent_risk_level=""))
    assert result["risk_result"].risk_level == "elevated"
    assert result["risk_result"].needs_crisis_mode is False


def test_record_layer_text_still_does_not_trigger_upgrade() -> None:
    """隔离红线回归：记录层文本只在 memory_summary 时不得触发升级。"""
    result = classify_risk(
        _state(
            "我还是觉得没意义，撑不住了",
            recent_risk_level="",
            user_history_text="User (mode=support, risk=low): 今天去散步了",
        )
    )
    assert result["risk_result"].risk_level == "elevated"
