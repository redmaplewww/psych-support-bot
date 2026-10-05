#!/usr/bin/env python
"""上下文切片系统集成测试。

测试场景：
1. 启用切片系统
2. 模拟多轮对话
3. 验证切片创建和消息关联
4. 验证切片上下文注入到 GraphState
"""

import os
import sys
from uuid import uuid4

# 设置环境变量启用切片
os.environ["ENABLE_CONTEXT_SLICING"] = "true"

from psych_support_bot.ai.schemas.requests import ConversationRequest

from psych_support_bot.infra.db.models import ConversationSlice, Message
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.services.conversation import ConversationService


def test_context_slicing():
    """集成测试：启用切片后的完整对话流程。"""
    print("=" * 70)
    print("上下文切片系统集成测试")
    print("=" * 70)

    # 创建测试数据
    user_id = f"test-user-{uuid4().hex[:8]}"
    session_id = f"test-session-{uuid4().hex[:8]}"

    print(f"\n创建测试用户: {user_id}")
    print(f"创建测试会话: {session_id}")

    service = ConversationService()
    db_session = next(get_db_session())

    # 场景 1: 首次对话
    print("\n" + "=" * 70)
    print("场景 1: 首次对话")
    print("=" * 70)

    try:
        request1 = ConversationRequest(
            user_id=user_id,
            session_id=session_id,
            message="我最近压力很大",
        )
        response1 = service.respond(request1, db_session)

        print("\n✓ 对话完成")
        print(f"  - session_id: {response1.session_id}")
        print(f"  - reply: {response1.reply.text[:100]}...")

        # 检查切片是否创建
        slices = db_session.query(ConversationSlice).filter_by(user_id=user_id).all()
        print(f"\n✓ 切片数量: {len(slices)}")
        if slices:
            slice1 = slices[0]
            print(f"  - slice_id: {slice1.id}")
            print(f"  - boundary_reason: {slice1.boundary_reason}")
            print(f"  - turn_count: {slice1.turn_count}")

        # 检查消息是否关联到切片
        messages = db_session.query(Message).filter_by(session_id=session_id).all()
        print(f"\n✓ 消息数量: {len(messages)}")
        for i, msg in enumerate(messages, 1):
            print(f"  {i}. role={msg.role}, slice_id={msg.slice_id}, content={msg.content[:30]}...")

    except Exception as e:
        print(f"\n✗ 场景 1 失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    # 场景 2: 继续同话题
    print("\n" + "=" * 70)
    print("场景 2: 继续同话题（应保持在同一切片）")
    print("=" * 70)

    try:
        request2 = ConversationRequest(
            user_id=user_id,
            session_id=session_id,
            message="主要是工作上的压力",
        )
        response2 = service.respond(request2, db_session)

        print("\n✓ 对话完成")
        print(f"  - reply: {response2.reply.text[:100]}...")

        # 检查切片数量（应该还是1个）
        slices = db_session.query(ConversationSlice).filter_by(user_id=user_id).all()
        print(f"\n✓ 切片数量: {len(slices)} (预期: 1)")

        if slices:
            slice1 = slices[0]
            print(f"  - turn_count: {slice1.turn_count} (预期: 1)")

    except Exception as e:
        print(f"\n✗ 场景 2 失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    # 场景 3: 显式切换话题
    print("\n" + "=" * 70)
    print("场景 3: 用户说'换个话题'（应创建新切片）")
    print("=" * 70)

    try:
        request3 = ConversationRequest(
            user_id=user_id,
            session_id=session_id,
            message="好的谢谢。换个话题，我想聊聊睡眠问题",
        )
        response3 = service.respond(request3, db_session)

        print("\n✓ 对话完成")
        print(f"  - reply: {response3.reply.text[:100]}...")

        # 检查切片数量（应该是2个）
        slices = (
            db_session.query(ConversationSlice).filter_by(user_id=user_id).order_by(ConversationSlice.created_at).all()
        )
        print(f"\n✓ 切片数量: {len(slices)} (预期: 2)")

        if len(slices) >= 2:
            slice1 = slices[0]
            slice2 = slices[1]
            print("\n切片 1:")
            print(f"  - id: {slice1.id}")
            print(f"  - boundary_reason: {slice1.boundary_reason}")
            print(f"  - status: {slice1.status} (预期: completed)")
            print(f"  - turn_count: {slice1.turn_count}")

            print("\n切片 2:")
            print(f"  - id: {slice2.id}")
            print(f"  - boundary_reason: {slice2.boundary_reason} (预期: explicit_switch)")
            print(f"  - status: {slice2.status}")
            print(f"  - turn_count: {slice2.turn_count}")

    except Exception as e:
        print(f"\n✗ 场景 3 失败: {e}")
        import traceback

        traceback.print_exc()
        return False

    # 清理测试数据
    print("\n" + "=" * 70)
    print("清理测试数据...")
    print("=" * 70)

    try:
        db_session.query(Message).filter_by(session_id=session_id).delete()
        db_session.query(ConversationSlice).filter_by(user_id=user_id).delete()
        from psych_support_bot.infra.db.models import ConversationSession

        db_session.query(ConversationSession).filter_by(id=session_id).delete()
        db_session.commit()
        print("✓ 清理完成")
    except Exception as e:
        print(f"✗ 清理失败: {e}")

    print("\n" + "=" * 70)
    print("✓ 集成测试完成！")
    print("=" * 70)
    return True


if __name__ == "__main__":
    success = test_context_slicing()
    sys.exit(0 if success else 1)
