"""记录层热插拔记忆模块（ai/memory_modules.py）单测。

覆盖：
1. 评估模块：趋势渲染（分差方向）、中英文口径、空数据返回 None。
2. 打卡模块：箭头趋势 + 连续 3 天低位/高位/睡眠不足标注，单日波动不标。
3. 练习模块：中文练习名映射、来源标注、空数据。
4. render_record_layers：MEMORY_MODULE_* 开关逐个热插拔、预算截断、
   单模块渲染失败 fail-open。
5. build_user_history_text：用户原话 + 会话摘要双源；不含 Bot 侧文本。
6. build_memory_snapshot(language=...)：模块渲染进入快照、开关关闭时不进。
7. 情绪扫描隔离：记录层文本不进 user_history_text。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psych_support_bot.ai.memory_modules import (
    DEFAULT_MODULE_BUDGET,
    render_record_layers,
)
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.models import (
    AssessmentRecord,
    CheckinRecord,
    ConversationSession,
    ExerciseRecord,
    Message,
)
from psych_support_bot.infra.db.repositories import (
    build_memory_snapshot,
    build_user_history_text,
    get_recent_user_messages,
)
from psych_support_bot.infra.db.session import SessionLocal

init_db()


def _add_records(session, user_id: str) -> None:
    now = datetime.now(UTC)
    # 评估：ISI 16 -> 20（上升 4），8月口径带中文 band。
    session.add_all(
        [
            AssessmentRecord(
                user_id=user_id,
                assessment_type="isi",
                score=20,
                severity_band="moderate",
                source="chat",
                created_at=now - timedelta(days=1),
            ),
            AssessmentRecord(
                user_id=user_id,
                assessment_type="isi",
                score=16,
                severity_band="subthreshold",
                source="chat",
                created_at=now - timedelta(days=8),
            ),
        ]
    )
    # 打卡：心情 4→4→4（连续 3 天 ≤4 触发标注），睡眠正常。
    for offset, mood in enumerate([4, 4, 4]):
        session.add(
            CheckinRecord(
                user_id=user_id,
                checkin_date=(now - timedelta(days=offset)).date(),
                mood_score=mood,
                anxiety_score=3,
                sleep_hours=7.0,
                energy_score=5,
            )
        )
    # 练习：中文 tag 映射 + 面板来源。
    session.add(
        ExerciseRecord(
            user_id=user_id,
            exercise_tag="dbt_tipp",
            source="panel",
            completed_at=now - timedelta(days=1),
        )
    )
    session.flush()


def _fresh_session():
    return SessionLocal()


def test_assessment_module_renders_trend_zh() -> None:
    user_id = f"mm-as-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()
        rendered = render_record_layers(db, user_id, "zh")
    assert "评估记录：" in rendered
    assert "失眠严重程度量表" in rendered
    assert "上升4分" in rendered
    assert "打卡趋势：" in rendered
    assert "4→4→4" in rendered
    assert "连续3天低位" in rendered
    assert "最近完成：" in rendered
    assert "DBT TIPP 危机技能" in rendered
    assert "（面板）" in rendered


def test_assessment_module_renders_en() -> None:
    user_id = f"mm-en-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()
        rendered = render_record_layers(db, user_id, "en")
    assert "Assessments: " in rendered
    assert "Insomnia Severity Index 20 (moderate)" in rendered
    assert "up 4 from last" in rendered
    assert "评估记录" not in rendered


def test_modules_return_none_when_no_records() -> None:
    user_id = f"mm-empty-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        db.commit()
        assert render_record_layers(db, user_id, "zh") == ""


def test_checkin_single_day_dip_not_flagged() -> None:
    """单日低分不标注"需关注"——连续 3 天才触发，避免噪音。"""
    user_id = f"mm-dip-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    with _fresh_session() as db:
        db.add_all(
            [
                CheckinRecord(
                    user_id=user_id,
                    checkin_date=(now - timedelta(days=offset)).date(),
                    mood_score=mood,
                    anxiety_score=3,
                    sleep_hours=7.0,
                    energy_score=5,
                )
                for offset, mood in [(0, 3), (1, 7), (2, 6)]
            ]
        )
        db.commit()
        rendered = render_record_layers(db, user_id, "zh")
    assert "打卡趋势：" in rendered
    assert "6→7→3" in rendered
    assert "需关注" not in rendered


def test_module_switch_off_disables_layer(monkeypatch) -> None:
    user_id = f"mm-sw-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()
        monkeypatch.setenv("MEMORY_MODULE_ASSESSMENTS", "false")
        monkeypatch.setenv("MEMORY_MODULE_EXERCISES", "false")
        from psych_support_bot.infra.config.settings import get_settings

        get_settings.cache_clear()
        rendered = render_record_layers(db, user_id, "zh")
        get_settings.cache_clear()
    assert "评估记录" not in rendered
    assert "最近完成" not in rendered
    assert "打卡趋势" in rendered  # 未关闭的模块仍在


def test_budget_truncates(monkeypatch) -> None:
    from psych_support_bot.ai.memory_modules import AssessmentMemoryModule

    user_id = f"mm-clip-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()
        rendered = AssessmentMemoryModule().render(db, user_id, language="zh", char_budget=DEFAULT_MODULE_BUDGET)
        assert len(rendered) <= DEFAULT_MODULE_BUDGET
        clipped = AssessmentMemoryModule().render(db, user_id, language="zh", char_budget=30)
        assert clipped.endswith("…")
        assert len(clipped) <= 30


def test_module_failure_fails_open(monkeypatch) -> None:
    """单模块渲染抛异常只跳过该层，其余层照常渲染。"""
    user_id = f"mm-fail-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()

        def _boom(self, session, user_id, *, language, char_budget):
            raise RuntimeError("boom")

        monkeypatch.setattr("psych_support_bot.ai.memory_modules.AssessmentMemoryModule.render", _boom)
        rendered = render_record_layers(db, user_id, "zh")
    assert "评估记录" not in rendered
    assert "打卡趋势" in rendered


def test_get_recent_user_messages_user_role_only() -> None:
    user_id = f"mm-msg-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        conv = ConversationSession(
            id=str(uuid4()),
            user_id=user_id,
            mode="support",
            risk_level="low",
            summary="User (mode=support, risk=elevated): 很绝望",
        )
        db.add(conv)
        db.flush()
        db.add_all(
            [
                Message(session_id=conv.id, role="user", content="我觉得很绝望"),
                Message(session_id=conv.id, role="assistant", content="我听到你正在经历这样的难事睡不着很难受"),
                Message(session_id=conv.id, role="user", content="今天撑不住了"),
            ]
        )
        db.commit()
        messages = get_recent_user_messages(db, user_id)
    assert messages == ["今天撑不住了", "我觉得很绝望"]  # 倒序，无 Bot 侧文本


def test_build_user_history_text_excludes_record_layers() -> None:
    """情绪扫描通道 = 会话摘要 + 用户原话；记录层/其他用户不含临床词汇。"""
    user_id = f"mm-hist-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        conv = ConversationSession(
            id=str(uuid4()),
            user_id=user_id,
            mode="support",
            risk_level="low",
            summary="User (mode=support, risk=elevated): 很绝望",
        )
        db.add(conv)
        db.flush()
        db.add(Message(session_id=conv.id, role="user", content="撑不住了"))
        db.commit()
        history = build_user_history_text(db, user_id)
    assert "评估记录" not in history
    assert "打卡趋势" not in history
    assert "最近完成" not in history
    assert "很绝望" in history
    assert "撑不住了" in history


def test_build_memory_snapshot_includes_layers_with_language() -> None:
    user_id = f"mm-snap-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        _add_records(db, user_id)
        db.commit()
        snapshot = build_memory_snapshot(db, user_id, language="zh")
    assert "评估记录：" in snapshot
    assert "打卡趋势：" in snapshot
    assert "最近完成：" in snapshot
    # 旧裸字段格式不再出现。
    assert "recent check-ins mood=" not in snapshot


def test_build_memory_snapshot_without_records_has_no_layers() -> None:
    user_id = f"mm-snap0-{uuid4().hex[:8]}"
    with _fresh_session() as db:
        db.commit()
        snapshot = build_memory_snapshot(db, user_id, language="zh")
    assert snapshot == ""
