"""P4/P5 切片-画像联动端到端演示（离线：LLM 降级为确定性路径）。

剧情：
  第 1 段对话（切片 A）：用户聊失眠 → 每轮提取带 origin_slice_id 溯源；
  边界轮：用户"换个话题" → 切片 A 关闭 → P4 钩子生成摘要 + primary_topic；
  第 2 段对话（切片 B）：新话题里再提睡眠 → P5 检索命中切片 A 摘要，
  渲染【相关历史】背景块注入 memory_summary。

运行：uv run python scripts/demo_slice_profile_linkage.py
结束后自动清理演示数据（独立 user/session 前缀）。
"""

import os
import uuid

os.environ["ENABLE_CONTEXT_SLICING"] = "true"
os.environ["ENABLE_SLICE_BASED_EXTRACTION"] = "true"
os.environ["ENABLE_PROFILE_SLICE_RETRIEVAL"] = "true"

from psych_support_bot.ai.profile.extractor import run_turn_extraction  # noqa: E402
from psych_support_bot.infra.config.settings import get_settings  # noqa: E402
from psych_support_bot.infra.db.models import (  # noqa: E402
    ConversationSession,
    Message,
    ProfileBelief,
    SliceSummary,
)
from psych_support_bot.infra.db.session import SessionLocal  # noqa: E402
from psych_support_bot.services import slice_summary as slice_summary_module  # noqa: E402
from psych_support_bot.services.slice_manager import SliceManager  # noqa: E402
from psych_support_bot.services.slice_retrieval import (  # noqa: E402
    render_slice_history_block,
    retrieve_relevant_slices,
)

# 离线演示：摘要 LLM 降级为确定性拼接（生产开启开关时走真 LLM）。
slice_summary_module._generate_summary_text_via_llm = lambda messages: None  # noqa: E731


def main() -> None:
    settings = get_settings()
    assert settings.enable_context_slicing and settings.enable_slice_based_extraction
    assert settings.enable_profile_slice_retrieval

    user_id = f"demo-p45-{uuid.uuid4().hex[:8]}"
    session_id = f"demo-sess-{uuid.uuid4().hex[:8]}"
    manager = SliceManager()

    with SessionLocal() as session:
        session.add(ConversationSession(id=session_id, user_id=user_id, mode="support", risk_level="low"))
        session.commit()

        print("=" * 70)
        print("第 1 段对话：失眠话题（切片 A，每轮提取带 origin_slice_id）")
        print("=" * 70)
        slice_a = manager.get_or_create_slice(session, user_id, session_id, "我最近失眠很严重，躺下两三个小时睡不着")
        session.add(
            Message(
                session_id=session_id,
                slice_id=slice_a.id,
                role="user",
                content="我最近失眠很严重，躺下两三个小时睡不着",
            )
        )
        session.add(
            Message(
                session_id=session_id,
                slice_id=slice_a.id,
                role="assistant",
                content="听起来入睡这段最难熬。",
            )
        )
        session.commit()
        run_turn_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            topics=["sleep"],
            risk_level="low",
            exercise_tag=None,
            valence_text="",
            slice_id=slice_a.id,
        )
        belief = session.query(ProfileBelief).filter_by(user_id=user_id, key="sleep").first()
        print(f"  切片 A: {slice_a.id}")
        print(f"  D1 belief 'sleep' origin_slice_id = {belief.origin_slice_id if belief else None}")
        assert belief is not None and belief.origin_slice_id == slice_a.id, "P4 溯源未写入"

        print()
        print("=" * 70)
        print("边界轮：换个话题 → 切片 A 关闭 → P4 钩子（摘要 + 主题继承）")
        print("=" * 70)
        manager.get_or_create_slice(session, user_id, session_id, "换个话题，我想聊聊工作压力")
        session.commit()
        summary = session.get(SliceSummary, slice_a.id)
        session.refresh(slice_a)
        print(f"  切片 A status = {slice_a.status}, primary_topic = {slice_a.primary_topic!r}")
        print(f"  边界消息回填: start={slice_a.start_message_id} end={slice_a.end_message_id}")
        print(f"  摘要: {summary.summary_text}")
        print(f"  主题: {summary.topics}")
        assert summary is not None and slice_a.primary_topic == "sleep"

        print()
        print("=" * 70)
        print("第 2 段对话：工作话题中再提睡眠 → P5 画像驱动检索注入")
        print("=" * 70)
        relevant = retrieve_relevant_slices(session, user_id, "昨晚又睡不着，工作压力大", exclude_slice_id="")
        block = render_slice_history_block(relevant, language="zh")
        print(block)
        assert block, "P5 未命中历史摘要"

        print()
        print("=" * 70)
        print("清理演示数据")
        print("=" * 70)
        n_belief = session.query(ProfileBelief).filter_by(user_id=user_id).delete()
        n_summary = session.query(SliceSummary).filter_by(user_id=user_id).delete()
        from psych_support_bot.infra.db.models import (
            ConversationSlice,
            ProfileBeliefEvent,
            ProfileExtractionStats,
        )

        session.query(ProfileBeliefEvent).filter_by(user_id=user_id).delete()
        session.query(ProfileExtractionStats).filter_by(user_id=user_id).delete()
        n_slice = session.query(ConversationSlice).filter_by(user_id=user_id).delete()
        n_msg = session.query(Message).filter_by(session_id=session_id).delete()
        session.query(ConversationSession).filter_by(id=session_id).delete()
        session.commit()
        print(f"  beliefs={n_belief} summaries={n_summary} slices={n_slice} messages={n_msg}")
        print("✓ P4/P5 全链路演示通过")


if __name__ == "__main__":
    main()
