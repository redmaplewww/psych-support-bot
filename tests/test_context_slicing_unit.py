"""上下文切片系统单元测试。"""

import uuid
from datetime import UTC, datetime

from psych_support_bot.infra.db.models import (
    ConversationSession,
    ConversationSlice,
    Message,
)
from psych_support_bot.services.slice_manager import (
    SliceManager,
    build_slice_context,
)
from psych_support_bot.services.time_profile import (
    calculate_time_profile,
    is_sleep_boundary,
)


def _unique(prefix: str) -> str:
    """每个测试生成唯一 id，避免跨测试数据残留碰撞（SliceManager 内部 commit）。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestSliceManager:
    """切片管理器测试。"""

    def test_first_message_creates_slice(self, db_session):
        """首次对话应创建切片。"""
        manager = SliceManager()
        user_id = _unique("user")
        session_id = _unique("session")

        slice = manager.get_or_create_slice(
            db_session,
            user_id=user_id,
            session_id=session_id,
            user_message="Hello",
        )

        assert slice is not None
        assert slice.boundary_reason == "first_message"
        assert slice.status == "active"
        assert slice.turn_count == 0

    def test_explicit_switch_creates_new_slice(self, db_session):
        """显式切换话题应创建新切片。"""
        manager = SliceManager()
        user_id = _unique("user")
        session_id = _unique("session")

        # 第一个切片
        slice1 = manager.get_or_create_slice(
            db_session,
            user_id=user_id,
            session_id=session_id,
            user_message="I'm feeling anxious",
        )

        # 显式切换
        slice2 = manager.get_or_create_slice(
            db_session,
            user_id=user_id,
            session_id=session_id,
            user_message="换个话题，我想聊聊睡眠问题",
        )

        assert slice2.id != slice1.id
        assert slice2.boundary_reason == "explicit_switch"
        assert slice1.status == "completed"
        assert slice2.status == "active"

    def test_continue_topic_same_slice(self, db_session):
        """继续同话题应保持在同一切片。"""
        manager = SliceManager()
        user_id = _unique("user")
        session_id = _unique("session")

        slice1 = manager.get_or_create_slice(
            db_session,
            user_id=user_id,
            session_id=session_id,
            user_message="I'm anxious",
        )

        slice2 = manager.get_or_create_slice(
            db_session,
            user_id=user_id,
            session_id=session_id,
            user_message="It's mainly work stress",
        )

        assert slice2.id == slice1.id
        assert slice2.turn_count == 1


class TestTimeProfile:
    """时间画像测试。"""

    def test_new_user_default_profile(self, db_session):
        """新用户应使用保守默认值。"""
        user_id = _unique("user")
        session_id = _unique("session")
        # 创建用户但没有消息
        session_obj = ConversationSession(
            id=session_id,
            user_id=user_id,
            mode="support",
            risk_level="low",  # 修复：必填字段
        )
        db_session.add(session_obj)
        db_session.commit()

        profile = calculate_time_profile(db_session, user_id)

        assert profile["frequency_tier"] == "unknown"
        assert profile["short_gap_threshold"] == 1.0
        assert profile["long_gap_threshold"] == 12.0
        assert profile["confidence"] < 0.5

    def test_sleep_boundary_detection(self):
        """睡眠边界检测。"""
        # 23:30 -> 08:00，跨睡眠边界
        last_time = datetime(2026, 9, 13, 23, 30, tzinfo=UTC)
        current_time = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
        sleep_window = (23, 7)

        assert is_sleep_boundary(last_time, current_time, sleep_window)

        # 14:00 -> 15:00，未跨睡眠边界
        last_time = datetime(2026, 9, 13, 14, 0, tzinfo=UTC)
        current_time = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)

        assert not is_sleep_boundary(last_time, current_time, sleep_window)


class TestSliceContext:
    """切片上下文构建测试。"""

    def test_build_slice_context(self, db_session):
        """构建切片上下文。"""
        user_id = _unique("user")
        session_id = _unique("session")
        # 创建切片
        slice_id = _unique("slice")
        slice = ConversationSlice(
            id=slice_id,
            session_id=session_id,
            user_id=user_id,
            boundary_reason="first_message",
        )
        db_session.add(slice)

        # 添加消息
        messages = [
            Message(session_id=session_id, slice_id=slice_id, role="user", content="Hello"),
            Message(session_id=session_id, slice_id=slice_id, role="assistant", content="Hi there"),
            Message(session_id=session_id, slice_id=slice_id, role="user", content="How are you?"),
        ]
        for msg in messages:
            db_session.add(msg)
        db_session.commit()

        # 构建上下文
        context = build_slice_context(db_session, slice_id, max_turns=10)

        assert len(context) == 3
        assert context[0]["role"] == "user"
        assert context[0]["content"] == "Hello"
        assert context[1]["role"] == "assistant"
        assert context[2]["role"] == "user"
