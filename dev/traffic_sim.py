"""One-off traffic simulation (Phase 1 verification): drive the real
ConversationService.respond pipeline with the new layered prompts to
produce Langfuse llm.invoke generations carrying input_cached data.

Two sessions (zh/en), 11 turns total, covering support / assessment /
intervention / quiet / elevated / diagnosis-refusal shapes. Turn spacing
lets gateway replicas warm their prefix caches.
"""

import time
import uuid

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.infra.telemetry.tracing import flush_langfuse
from psych_support_bot.services.conversation import ConversationService

init_db()
service = ConversationService()

SESSION_ZH = f"sim-zh-{uuid.uuid4().hex[:8]}"
SESSION_EN = f"sim-en-{uuid.uuid4().hex[:8]}"
USER = "traffic-sim-user"

TURNS_ZH = [
    "我最近晚上总是睡不着，躺在床上翻来覆去一两个小时都睡不着",
    "就是工作上的事，项目一直加班，感觉停不下来",
    "嗯，白天上班也没精神，注意力很难集中",
    "有什么办法能让自己睡前放松一点吗？",
    "……让我一个人静静，先别问了",
    "好一点了，谢谢你听我说这些",
]

TURNS_EN = [
    "I feel anxious all the time lately and I can't figure out why",
    "Mostly work stress, my manager keeps piling things on me",
    "I keep overthinking everything at night, it's exhausting",
    "Are there any techniques that could help me calm down quickly?",
    "Be honest with me, am I depressed? I check all the boxes I think",
    "Thanks, this actually helped a bit",
]


def run_turn(user_id: str, session_id: str, message: str) -> None:
    with SessionLocal() as session:
        response = service.respond(
            ConversationRequest(user_id=user_id, message=message, session_id=session_id),
            session,
        )
    reply_text = response.text if hasattr(response, "text") else str(response)[:80]
    print(f"[{session_id[-8:]}] turn: {message[:24]}… → {str(reply_text)[:60]}…")


for i, message in enumerate(TURNS_ZH):
    run_turn(USER, SESSION_ZH, message)
    time.sleep(4)

for i, message in enumerate(TURNS_EN):
    run_turn(USER, SESSION_EN, message)
    time.sleep(4)

flush_langfuse()
print("done; sessions:", SESSION_ZH, SESSION_EN)
