#!/usr/bin/env python
"""上下文切片系统演示脚本。

演示场景:
1. 用户首次对话 -> 创建第一个切片
2. 短时间内继续对话 -> 保持在同一切片
3. 用户说"换个话题" -> 创建新切片
4. 长时间间隔后回来 -> 创建新切片(基于时间阈值)
"""

from uuid import uuid4

from psych_support_bot.infra.db.models import (
    ConversationSession,
    ConversationSlice,
    Message,
    UserTimeProfile,
)
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.services.slice_manager import SliceManager
from psych_support_bot.services.time_profile import get_user_time_profile


def simulate_message(
    session,
    user_id: str,
    session_id: str,
    content: str,
    time_offset_minutes: int = 0,
):
    """模拟用户发送消息。"""
    slice_manager = SliceManager()

    # 模拟时间偏移(用于演示时间间隔检测)
    if time_offset_minutes > 0:
        # 注意: 这里只是演示,实际使用时需要修改 utcnow() 或使用测试 mock
        print(f"\n  [模拟时间流逝: {time_offset_minutes} 分钟]")

    # 获取或创建切片
    slice = slice_manager.get_or_create_slice(session, user_id, session_id, content)

    # 保存消息
    msg = Message(
        session_id=session_id,
        slice_id=slice.id,
        role="user",
        content=content,
    )
    session.add(msg)
    session.commit()

    return slice


def main():
    """运行演示。"""
    print("=" * 70)
    print("上下文切片系统演示")
    print("=" * 70)

    # 获取数据库会话
    db_session = next(get_db_session())

    # 创建测试用户和会话
    user_id = f"demo-user-{uuid4().hex[:8]}"
    session_id = f"session-{uuid4().hex[:8]}"

    print(f"\n创建测试用户: {user_id}")
    print(f"创建测试会话: {session_id}")

    # 创建会话记录
    conv_session = ConversationSession(
        id=session_id,
        user_id=user_id,
        mode="support",
        risk_level="low",
    )
    db_session.add(conv_session)
    db_session.commit()

    # === 场景 1: 首次对话 ===
    print("\n" + "=" * 70)
    print("场景 1: 首次对话 (应该创建第一个切片)")
    print("=" * 70)

    slice1 = simulate_message(db_session, user_id, session_id, "我最近有点焦虑")
    print("\n✓ 切片已创建:")
    print(f"  - slice_id: {slice1.id}")
    print(f"  - boundary_reason: {slice1.boundary_reason}")
    print(f"  - confidence: {slice1.boundary_confidence}")
    print(f"  - turn_count: {slice1.turn_count}")

    # === 场景 2: 短时间内继续对话 ===
    print("\n" + "=" * 70)
    print("场景 2: 5分钟后继续聊焦虑话题 (应该保持在同一切片)")
    print("=" * 70)

    slice2 = simulate_message(db_session, user_id, session_id, "主要是工作压力太大了")
    print("\n✓ 切片状态:")
    print(f"  - slice_id: {slice2.id}")
    print(f"  - 是否为同一切片: {'是' if slice2.id == slice1.id else '否'}")
    print(f"  - turn_count: {slice2.turn_count}")

    # === 场景 3: 显式切换话题 ===
    print("\n" + "=" * 70)
    print("场景 3: 用户说'换个话题' (应该创建新切片)")
    print("=" * 70)

    slice3 = simulate_message(db_session, user_id, session_id, "好的，谢谢。换个话题，我想聊聊睡眠问题")
    print("\n✓ 新切片已创建:")
    print(f"  - slice_id: {slice3.id}")
    print(f"  - boundary_reason: {slice3.boundary_reason}")
    print(f"  - confidence: {slice3.boundary_confidence}")
    print(f"  - 旧切片 status: {db_session.get(ConversationSlice, slice1.id).status}")

    # === 场景 4: 继续新话题 ===
    print("\n" + "=" * 70)
    print("场景 4: 继续睡眠话题 (应该保持在新切片)")
    print("=" * 70)

    slice4 = simulate_message(db_session, user_id, session_id, "我经常失眠")
    print("\n✓ 切片状态:")
    print(f"  - slice_id: {slice4.id}")
    print(f"  - 是否为同一切片: {'是' if slice4.id == slice3.id else '否'}")
    print(f"  - turn_count: {slice4.turn_count}")

    # === 场景 5: 查看用户时间画像 ===
    print("\n" + "=" * 70)
    print("场景 5: 用户时间画像")
    print("=" * 70)

    time_profile = get_user_time_profile(db_session, user_id)
    print("\n✓ 时间画像:")
    print(f"  - frequency_tier: {time_profile['frequency_tier']}")
    print(f"  - short_gap_threshold: {time_profile['short_gap_threshold']:.1f} 小时")
    print(f"  - long_gap_threshold: {time_profile['long_gap_threshold']:.1f} 小时")
    print(f"  - sleep_window: {time_profile['sleep_window']}")
    print(f"  - confidence: {time_profile['confidence']:.2f}")

    # === 查看所有切片 ===
    print("\n" + "=" * 70)
    print("所有切片汇总")
    print("=" * 70)

    slices = db_session.query(ConversationSlice).filter_by(user_id=user_id).order_by(ConversationSlice.created_at).all()

    for i, s in enumerate(slices, 1):
        messages = db_session.query(Message).filter_by(slice_id=s.id).all()
        print(f"\n切片 {i}:")
        print(f"  - ID: {s.id}")
        print(f"  - 原因: {s.boundary_reason}")
        print(f"  - 状态: {s.status}")
        print(f"  - 轮数: {s.turn_count}")
        print(f"  - 消息数: {len(messages)}")
        if messages:
            print(f"  - 首条消息: {messages[0].content[:30]}...")

    # 清理测试数据
    print("\n" + "=" * 70)
    print("清理测试数据...")
    print("=" * 70)

    for s in slices:
        db_session.query(Message).filter_by(slice_id=s.id).delete()
        db_session.delete(s)

    db_session.query(ConversationSession).filter_by(id=session_id).delete()
    db_session.query(UserTimeProfile).filter_by(user_id=user_id).delete()
    db_session.commit()

    print("\n✓ 演示完成!")


if __name__ == "__main__":
    main()
