"""K3 综合引擎：从碎片信念中生长对这个人的结构性理解。

触发时机：切片完成时（低频，与切片摘要同步）。
输入：全部活跃 beliefs + 最近切片摘要 + 打卡趋势。
输出：结构化的用户理解（不是标签，是关系和模式）。

设计原则：
- 不预设标签体系——理解从用户数据中涌现
- 带着问号接受用户说的一切——user_stated 不等于真相
- 理解是可修正的——新证据可以推翻旧理解
- 输出必须是结构化的 JSON，可以被渲染和验证
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from psych_support_bot.ai.profile.constants import K3_MAX_BELIEFS, K3_MAX_SUMMARIES

logger = logging.getLogger(__name__)

_K3_SYSTEM_PROMPT = """\
You are building a psychological understanding of a specific person through \
ongoing conversation. This is NOT a diagnostic exercise — you are developing \
a working model of how this person experiences and navigates their world.

Input: a set of observations (beliefs with confidence scores), recent \
conversation summaries, and checkin trends.

Output STRICT JSON only, no prose:
{
  "patterns": [
    {
      "description": "<one sentence describing an observed pattern>",
      "situations": ["<when this pattern appears>"],
      "what_happens": "<what the person does>",
      "why_possibly": "<your best guess at what drives this — mark as hypothesis>",
      "confidence": 0.0-1.0,
      "evidence_count": 0,
      "needs_verification": true/false
    }
  ],
  "open_questions": [
    "<something you've noticed but can't explain yet>"
  ],
  "what_works": [
    "<interventions or approaches that seem to help this person>"
  ],
  "how_to_be_with_them": "<one sentence on how to interact with this person effectively>"
}

Rules:
- At most 3 patterns. Output empty arrays when nothing is solid.
- Every pattern MUST be grounded in the provided observations — never invent.
- Mark uncertain patterns with needs_verification=true.
- "how_to_be_with_them" is about interaction style, not diagnosis.
- Never use clinical labels (personality disorder, attachment style, etc.).
- Speak about this person with respect — they are a human being, not a case.
- If the observations are too sparse, output {"patterns":[], "open_questions":["Not enough data yet to form a clear picture."], "what_works":[], "how_to_be_with_them":"Be present and listen. This is still early."}
"""


def _build_k3_prompt(
    beliefs_text: str,
    slice_summaries: str,
    checkin_trend: str,
    current_understanding: str,
) -> str:
    parts = [
        "[Observations — beliefs with confidence and evidence]",
        beliefs_text or "(no beliefs yet)",
        "",
        "[Recent conversation summaries]",
        slice_summaries or "(no summaries yet)",
        "",
        "[Checkin trends]",
        checkin_trend or "(no checkin data)",
        "",
        "[Current understanding — update or replace as needed]",
        current_understanding or "(no prior understanding)",
    ]
    return "\n".join(parts)


def _format_beliefs_for_k3(beliefs: list) -> str:
    lines: list[str] = []
    for b in beliefs:
        if b.confidence < 0.3:
            continue
        lines.append(f"- [{b.dimension}] {b.key} (confidence={b.confidence:.2f}, layer={b.layer}): {b.claim_text}")
    return "\n".join(lines)


def _format_slice_summaries(summaries: list) -> str:
    lines: list[str] = []
    for s in summaries[:5]:
        lines.append(f"- {s.created_at.strftime('%Y-%m-%d') if s.created_at else '?'}: {s.summary_text}")
    return "\n".join(lines)


def _format_checkin_trend(checkins: list) -> str:
    if not checkins:
        return ""
    lines: list[str] = []
    for c in checkins[:7]:
        lines.append(f"- {c.checkin_date}: mood={c.mood_score} anxiety={c.anxiety_score} sleep={c.sleep_hours}h")
    return "\n".join(lines)


def run_k3_synthesis(
    session: Session,
    user_id: str,
) -> dict | None:
    """K3 综合引擎：切片完成时调用，生成/更新用户的结构性理解。

    返回理解 dict 或 None（数据不足/LLM 不可用）。
    fail-open：任何异常只记日志，绝不阻断对话。
    """
    try:
        from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled
        from psych_support_bot.infra.db.repositories import get_recent_checkins, get_user_profile
        from psych_support_bot.infra.llm.generation import generate_profile_extraction

        if not is_profile_memory_enabled(session, user_id):
            return None

        # 收集输入。
        from psych_support_bot.infra.db.profile_repositories import list_active_beliefs

        beliefs = list_active_beliefs(session, user_id, limit=K3_MAX_BELIEFS)
        if len(beliefs) < 2:
            return None  # 数据太少，不做综合

        from psych_support_bot.infra.db.models import SliceSummary

        summaries = (
            session.query(SliceSummary)
            .filter(SliceSummary.user_id == user_id)
            .order_by(SliceSummary.created_at.desc())
            .limit(K3_MAX_SUMMARIES)
            .all()
        )
        checkins = get_recent_checkins(session, user_id, limit=7)

        profile = get_user_profile(session, user_id)
        current = "{}"
        if profile and profile.understanding_json and profile.understanding_json != "{}":
            current = profile.understanding_json

        beliefs_text = _format_beliefs_for_k3(beliefs)
        summaries_text = _format_slice_summaries(summaries)
        checkin_text = _format_checkin_trend(checkins)

        if not beliefs_text:
            return None

        user_content = _build_k3_prompt(beliefs_text, summaries_text, checkin_text, current)

        # LLM 调用。
        raw = generate_profile_extraction(
            system_prompt=_K3_SYSTEM_PROMPT,
            payload_text=user_content,
        )

        # 解析。
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        understanding = json.loads(text[start : end + 1])

        # 写入。
        if profile is None:
            from psych_support_bot.infra.db.repositories import upsert_user_profile

            upsert_user_profile(
                session,
                user_id,
                display_name="",
                primary_concerns="",
                goals="",
                support_preferences="",
                risk_notes="",
                understanding_json=json.dumps(understanding, ensure_ascii=False),
            )
        else:
            profile.understanding_json = json.dumps(understanding, ensure_ascii=False)
            session.commit()

        return understanding

    except Exception:  # noqa: BLE001 — K3 must never block conversation
        logger.warning("K3 synthesis failed for user %s; skipping", user_id)
        return None


def render_understanding(session: Session, user_id: str, language: str = "") -> str | None:
    """将用户的结构性理解渲染为可注入 memory snapshot 的文本。"""
    try:
        from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled
        from psych_support_bot.infra.db.repositories import get_user_profile

        if not is_profile_memory_enabled(session, user_id):
            return None
        profile = get_user_profile(session, user_id)
        if not profile or not profile.understanding_json or profile.understanding_json == "{}":
            return None

        understanding = json.loads(profile.understanding_json)
        patterns = understanding.get("patterns", [])
        how_to = understanding.get("how_to_be_with_them", "")
        questions = understanding.get("open_questions", [])

        if not patterns and not how_to:
            return None

        is_en = language == "en"
        parts: list[str] = []

        if how_to:
            parts.append(f"Interaction note: {how_to}" if is_en else f"相处方式：{how_to}")

        for p in patterns[:3]:
            desc = p.get("description", "")
            if desc:
                parts.append(desc)

        if questions and questions[0] != "Not enough data yet to form a clear picture.":
            q = questions[0]
            parts.append(f"Open question: {q}" if is_en else f"待验证：{q}")

        return " | ".join(parts) if parts else None

    except Exception:  # noqa: BLE001
        return None
