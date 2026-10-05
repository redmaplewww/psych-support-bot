"""P4/P5 切片-画像联动单元测试。

P4：切片完成 → 摘要生成（确定性降级）+ primary_topic 继承 + 提取溯源；
P5：画像驱动检索（0.5 时间 / 0.3 主题 / 0.2 练习效果）+ 背景块渲染。
全部用例不触网（LLM 生成侧 monkeypatch 为确定性降级）。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from psych_support_bot.ai.profile.extractor import run_turn_extraction
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.me_repositories import delete_user_account
from psych_support_bot.infra.db.models import (
    ConversationSession,
    ConversationSlice,
    Message,
    SliceSummary,
    User,
    UserTimeProfile,
    utcnow,
)
from psych_support_bot.infra.db.profile_repositories import get_belief, record_claim
from psych_support_bot.services import slice_summary as slice_summary_module
from psych_support_bot.services.slice_manager import SliceManager
from psych_support_bot.services.slice_retrieval import (
    calculate_relevance_score_from_days,
    render_slice_history_block,
    retrieve_relevant_slices,
    score_slice_summary,
)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def p4_enabled(monkeypatch):
    """打开 P4 开关并把摘要 LLM 降级为确定性（不触网）。"""
    monkeypatch.setenv("ENABLE_SLICE_BASED_EXTRACTION", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(slice_summary_module, "_generate_summary_text_via_llm", lambda messages: None)
    yield
    get_settings.cache_clear()


def _seed_completed_slice(session, user_id: str, *, day_offset: int = 0) -> ConversationSlice:
    """直造一个带消息的完成态切片（绕开边界判定的时间依赖）。"""
    the_slice = ConversationSlice(
        id=_unique("slice"),
        session_id=_unique("session"),
        user_id=user_id,
        boundary_reason="explicit_switch",
        status="completed",
    )
    session.add(the_slice)
    base = utcnow() - timedelta(days=day_offset)
    for i, (role, content) in enumerate(
        [
            ("user", "我最近失眠很严重，躺下两三个小时睡不着"),
            ("assistant", "听起来入睡这段最难熬"),
            ("user", "对，而且白天注意力也差"),
        ]
    ):
        session.add(
            Message(
                session_id=the_slice.session_id,
                slice_id=the_slice.id,
                role=role,
                content=content,
                created_at=base + timedelta(minutes=i),
            )
        )
    session.commit()
    return the_slice


class TestSliceCompletionHook:
    """P4：切片完成钩子（摘要 + 主题继承 + 边界回填）。"""

    def test_hook_off_by_default_no_summary(self, db_session):
        """开关默认关：关闭切片不产摘要（行为回退 P3）。"""
        user_id = _unique("user")
        session_id = _unique("session")
        manager = SliceManager()
        first = manager.get_or_create_slice(db_session, user_id, session_id, "我失眠很难受")
        db_session.add(Message(session_id=session_id, slice_id=first.id, role="user", content="我失眠很难受"))
        db_session.add(Message(session_id=session_id, slice_id=first.id, role="assistant", content="嗯"))
        db_session.commit()
        manager.get_or_create_slice(db_session, user_id, session_id, "换个话题，聊工作")

        assert first.status == "completed"
        assert db_session.get(SliceSummary, first.id) is None

    def test_summary_generated_on_close(self, db_session, p4_enabled):
        """P4 开：显式切换关闭旧切片时生成摘要并继承主题。"""
        user_id = _unique("user")
        session_id = _unique("session")
        manager = SliceManager()
        first = manager.get_or_create_slice(db_session, user_id, session_id, "我最近失眠很严重")
        db_session.add(Message(session_id=session_id, slice_id=first.id, role="user", content="我最近失眠很严重"))
        db_session.add(Message(session_id=session_id, slice_id=first.id, role="assistant", content="听起来很煎熬"))
        db_session.commit()

        manager.get_or_create_slice(db_session, user_id, session_id, "换个话题，聊聊工作")

        summary = db_session.get(SliceSummary, first.id)
        assert summary is not None
        assert summary.user_id == user_id
        # LLM 已降级为确定性：摘要来自用户原话拼接，主题来自 detect_topics 闭集
        assert "失眠" in summary.summary_text
        assert json.loads(summary.topics)
        db_session.refresh(first)
        assert first.primary_topic in json.loads(summary.topics)

    def test_summary_idempotent(self, db_session, p4_enabled):
        """幂等：重复生成直接返回既有摘要行。"""
        user_id = _unique("user")
        the_slice = _seed_completed_slice(db_session, user_id)

        first_row = slice_summary_module.generate_slice_summary(db_session, the_slice.id)
        db_session.commit()
        second_row = slice_summary_module.generate_slice_summary(db_session, the_slice.id)

        assert first_row is second_row

    def test_crisis_messages_excluded_from_summary(self, db_session, p4_enabled):
        """红线：危机轮消息（safety_flag=True）不进摘要输入。"""
        user_id = _unique("user")
        session_id = _unique("session")
        the_slice = ConversationSlice(id=_unique("slice"), session_id=session_id, user_id=user_id, status="completed")
        db_session.add(the_slice)
        db_session.add(
            Message(
                session_id=session_id,
                slice_id=the_slice.id,
                role="user",
                content="我不想活了",
                safety_flag=True,
            )
        )
        db_session.add(
            Message(
                session_id=session_id,
                slice_id=the_slice.id,
                role="assistant",
                content="我很担心你，请联系专业热线",
                safety_flag=True,
            )
        )
        db_session.commit()

        summary = slice_summary_module.generate_slice_summary(db_session, the_slice.id)
        db_session.commit()

        # 全部消息被红线过滤：摘要正文为空，危机内容零存储
        assert summary.summary_text == ""
        assert json.loads(summary.topics) == []

    def test_boundary_message_ids_backfilled(self, db_session, p4_enabled):
        """切片关闭时 start/end_message_id 回填为切片内首末消息。"""
        user_id = _unique("user")
        session_id = _unique("session")
        manager = SliceManager()
        first = manager.get_or_create_slice(db_session, user_id, session_id, "hello")
        m1 = Message(session_id=session_id, slice_id=first.id, role="user", content="hello")
        m2 = Message(session_id=session_id, slice_id=first.id, role="assistant", content="hi")
        db_session.add_all([m1, m2])
        db_session.commit()

        manager.get_or_create_slice(db_session, user_id, session_id, "换个话题")

        db_session.refresh(first)
        assert first.start_message_id == m1.id
        assert first.end_message_id == m2.id


class TestExtractionProvenance:
    """P4：画像提取溯源 origin_slice_id。"""

    def test_run_turn_extraction_records_slice(self, db_session):
        """K1 提取的 belief 带来源切片。"""
        user_id = _unique("user")
        session_id = _unique("session")
        slice_id = _unique("slice")

        run_turn_extraction(
            db_session,
            user_id=user_id,
            session_id=session_id,
            topics=["anxiety"],
            risk_level="low",
            exercise_tag=None,
            valence_text="",
            slice_id=slice_id,
        )

        belief = get_belief(db_session, user_id, "anxiety")
        assert belief is not None
        assert belief.origin_slice_id == slice_id

    def test_run_turn_extraction_without_slice(self, db_session):
        """未关联切片（功能关闭/旧路径）：origin_slice_id 为 None，不影响提取。"""
        user_id = _unique("user")
        run_turn_extraction(
            db_session,
            user_id=user_id,
            session_id=_unique("session"),
            topics=["anxiety"],
            risk_level="low",
            exercise_tag=None,
            valence_text="",
        )
        belief = get_belief(db_session, user_id, "anxiety")
        assert belief.origin_slice_id is None


class TestTimeDecay:
    """P5：时间衰减分段（设计文档 §2.3.2）。"""

    @pytest.mark.parametrize(
        ("days", "expected"),
        [(0, 1.0), (1, 1.0), (2, 0.8), (3, 0.8), (5, 0.6), (7, 0.6), (20, 0.3), (30, 0.3), (60, 0.1)],
    )
    def test_decay_steps(self, days, expected):
        assert calculate_relevance_score_from_days(days) == expected


class TestProfileDrivenRetrieval:
    """P5：加权检索与背景块渲染。"""

    def test_score_weights(self):
        """纯函数：0.5 时间 + 0.3 主题 + 0.2 练习效果的合成。"""
        now = datetime(2026, 9, 13, tzinfo=UTC)
        summary = SliceSummary(
            slice_id="s",
            user_id="u",
            summary_text="做了 breathing_478 后入睡变快",
            topics=json.dumps(["sleep"]),
            created_at=now - timedelta(hours=6),
        )
        score = score_slice_summary(
            summary,
            match_topics={"sleep", "work_stress"},
            worked_tags={"breathing_478"},
            now=now,
        )
        # 当天=1.0 → 0.5；主题命中 1/2 → 0.15；worked tag 命中 → 0.2
        assert score == pytest.approx(0.85)

    def test_score_no_signals_falls_to_time_only(self):
        now = datetime(2026, 9, 13, tzinfo=UTC)
        summary = SliceSummary(
            slice_id="s",
            user_id="u",
            summary_text="无关内容",
            topics=json.dumps(["grief"]),
            created_at=now - timedelta(days=40),
        )
        assert score_slice_summary(summary, match_topics=set(), worked_tags=set(), now=now) == 0.05

    def test_retrieve_orders_by_score_and_excludes(self, db_session):
        """端到端：D1 画像匹配 + 时间衰减决定排序；空摘要/当前切片被排除。"""
        user_id = _unique("user")
        record_claim(
            db_session,
            user_id,
            dimension="D1",
            key="sleep",
            claim_text="睡眠主题",
            confidence=0.8,
        )
        sleep_slice = _seed_completed_slice(db_session, user_id, day_offset=0)
        slice_summary_module.generate_slice_summary(db_session, sleep_slice.id)

        work_slice = ConversationSlice(id=_unique("slice"), session_id="s", user_id=user_id, status="completed")
        db_session.add(work_slice)
        db_session.add(
            SliceSummary(
                slice_id=work_slice.id,
                user_id=user_id,
                summary_text="上周讨论了工作压力",
                topics=json.dumps(["work_stress"]),
                created_at=utcnow() - timedelta(days=10),
            )
        )
        # 空摘要（全危机段）不应被返回
        empty_slice = ConversationSlice(id=_unique("slice"), session_id="s", user_id=user_id, status="completed")
        db_session.add(empty_slice)
        db_session.add(
            SliceSummary(
                slice_id=empty_slice.id,
                user_id=user_id,
                summary_text="",
                topics=json.dumps([]),
                created_at=utcnow(),
            )
        )
        db_session.commit()

        result = retrieve_relevant_slices(db_session, user_id, "昨晚又睡不着", exclude_slice_id="")
        ids = [s.slice_id for s in result]
        assert sleep_slice.id in ids
        assert empty_slice.id not in ids
        # 睡眠摘要：主题命中 + 更新 → 排在工作压力（30 天档外、主题不匹配）之前
        assert ids.index(sleep_slice.id) < ids.index(work_slice.id)

        excluded = retrieve_relevant_slices(db_session, user_id, "昨晚又睡不着", exclude_slice_id=sleep_slice.id)
        assert sleep_slice.id not in [s.slice_id for s in excluded]

    def test_render_block_language_and_note(self, db_session):
        """渲染：头部时间线提示 + 截断 + 双语。"""
        user_id = _unique("user")
        the_slice = _seed_completed_slice(db_session, user_id)
        summary = slice_summary_module.generate_slice_summary(db_session, the_slice.id)
        db_session.commit()

        block_zh = render_slice_history_block([summary], language="zh")
        assert "【相关历史】" in block_zh
        assert "不要与本次对话混淆时间线" in block_zh
        assert "今天" in block_zh

        block_en = render_slice_history_block([summary], language="en")
        assert "Relevant history" in block_en

        assert render_slice_history_block([], language="zh") == ""
        assert render_slice_history_block([SliceSummary(slice_id="x", user_id=user_id, summary_text="  ")]) == ""

    def test_retrieve_fail_open(self, db_session):
        """异常输入（无摘要用户）返回空表，不抛出。"""
        assert retrieve_relevant_slices(db_session, _unique("user"), "hi") == []


class TestAccountDeletionCascade:
    """隐私红线：注销必须级联删除切片三表（摘要=用户原话派生，不得残留）。"""

    def test_delete_account_removes_slice_tables(self, db_session):
        user_id = _unique("user")
        session_id = _unique("session")
        slice_id = _unique("slice")

        db_session.add(User(id=user_id))
        db_session.add(ConversationSession(id=session_id, user_id=user_id, mode="support", risk_level="low"))
        db_session.add(
            ConversationSlice(
                id=slice_id,
                session_id=session_id,
                user_id=user_id,
                boundary_reason="first_message",
                primary_topic="sleep",
            )
        )
        db_session.add(
            SliceSummary(
                slice_id=slice_id,
                user_id=user_id,
                summary_text="聊了失眠",
                topics=json.dumps(["sleep"]),
            )
        )
        db_session.add(UserTimeProfile(user_id=user_id, frequency_tier="high"))
        db_session.commit()

        counts = delete_user_account(db_session, user_id)
        db_session.commit()

        assert counts["conversation_slices"] == 1
        assert counts["slice_summaries"] == 1
        assert counts["user_time_profiles"] == 1
        assert db_session.get(ConversationSlice, slice_id) is None
        assert db_session.get(SliceSummary, slice_id) is None
        assert db_session.get(UserTimeProfile, user_id) is None
