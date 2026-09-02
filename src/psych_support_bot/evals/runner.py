from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.ai.nodes.safety_reviewer import (
    DIAGNOSIS_PATTERNS,
    LEAK_MARKERS,
    OVERREACH_PATTERNS,
)
from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.models import ConversationSession, Message, RiskEvent, User
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service

# Evaluation dimension helpers (deterministic, no LLM needed)
# ---------------------------------------------------------------------------

_ALL_REDLINED_REGEX = [re.compile(p, re.IGNORECASE) for p in DIAGNOSIS_PATTERNS + OVERREACH_PATTERNS]


def _detect_redline(text: str) -> bool:
    """Return True if the text contains any redline-violating pattern."""
    # Check diagnosis/overreach patterns
    for pattern in _ALL_REDLINED_REGEX:
        if pattern.search(text):
            return True
    # Check prompt leak markers
    return any(marker in text for marker in LEAK_MARKERS)


def _check_structure(text: str, expected_language: str, mode: str = "") -> bool:
    """Check the reply follows the current visible-reply contract.

    Contract (post 三气泡 redesign): 2–4 short unlabeled conversational
    messages separated by blank lines (intervention/planning may use four
    because guided teaching needs steps); clinical labels must NOT appear;
    at most one question mark after collapsing "A？是 B 还是 C"-style choice
    appositives back into a single question.
    Crisis mode is exempt — the safety template intentionally does not follow
    this structure; safety guidance takes priority over formatting there.
    """
    if mode == "crisis":
        return True
    forbidden = ["回应", "工作性假设", "下一问"]
    if any(label in text for label in forbidden):
        return False

    # Collapse choice-appositive duplicates ("哪些方面？是 A、B 还是 C？" carries
    # ONE question semantically) before counting.
    collapsed = re.sub(r"？\s*(?:是|比如|例如)[^？\n]{0,24}?(?:还是|或者)", "，或者", text)
    parts = [p.strip() for p in re.split(r"\n\s*\n", collapsed) if p.strip()]
    max_parts = 4 if mode in {"intervention", "planning"} else 3
    if not 1 <= len(parts) <= max_parts:
        return False
    question_marks = sum(part.count("？") + part.count("?") for part in parts)
    return question_marks <= 1


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _contains_ascii_words(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]{3,}", text))


def _check_language(text: str, expected_language: str) -> bool:
    """Check that the reply language is consistent with expected_language.

    Rules:
    - zh: output must contain Chinese characters; pure English is a violation.
    - en: output must contain ASCII words; pure Chinese is a violation.
    - Mixed (Chinese + English) is acceptable for either language, as the
      LLM may include scale names like PHQ-9 in Chinese replies.
    - Pure numbers/punctuation are language-neutral and pass.
    """
    has_chinese = _has_chinese(text)
    has_english = _contains_ascii_words(text)

    # Language-neutral (pure numbers/punctuation) — pass
    if not has_chinese and not has_english:
        return True

    if expected_language == "zh":
        # Chinese output is expected; pure English without any Chinese is a violation
        return not (has_english and not has_chinese)
    else:  # en
        # English output is expected; pure Chinese without any English is a violation
        return not (has_chinese and not has_english)


# ---------------------------------------------------------------------------
# Main evaluation runner
# ---------------------------------------------------------------------------


def _cleanup_eval_data(session: Session) -> None:
    """Delete all prior eval-user-* data to prevent cross-turn risk upgrade.

    Without this, build_memory_snapshot() picks up messages from previous
    eval runs, and the B2.2 cross-turn risk upgrade (elevated → high)
    fires incorrectly because _has_previous_elevated() matches keywords
    like 'hopeless' in the residual message text.
    """
    from sqlalchemy import delete, select

    # Get eval-user IDs
    eval_user_ids = list(session.execute(select(User.id).where(User.id.like("eval-user-%"))).scalars())
    if not eval_user_ids:
        return

    # Delete in dependency order: messages → risk_events → sessions → users
    eval_session_ids = list(
        session.execute(select(ConversationSession.id).where(ConversationSession.user_id.in_(eval_user_ids))).scalars()
    )
    if eval_session_ids:
        session.execute(delete(Message).where(Message.session_id.in_(eval_session_ids)))
        session.execute(delete(RiskEvent).where(RiskEvent.session_id.in_(eval_session_ids)))
    session.execute(delete(ConversationSession).where(ConversationSession.user_id.in_(eval_user_ids)))
    session.execute(delete(User).where(User.id.like("eval-user-%")))
    session.commit()


def run_eval_cases() -> list[dict[str, str | bool]]:
    init_db()
    cases_path = Path("tests/evals/cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results: list[dict[str, str | bool]] = []

    with SessionLocal() as session:
        # Clean up residual data from previous eval runs to prevent
        # cross-turn risk upgrade from firing on stale memory.
        _cleanup_eval_data(session)

        for idx, case in enumerate(cases, start=1):
            # Use a unique run suffix so even if cleanup fails, the
            # memory_snapshot will be empty for this user_id.
            run_id = uuid4().hex[:8]
            user_id = f"eval-user-{idx}-{run_id}"
            response = conversation_service.respond(
                ConversationRequest(
                    user_id=user_id,
                    message=case["message"],
                ),
                session=session,
            )

            # Determine expected language from the case or infer from message
            expected_language = case.get("expected_language", "")
            if not expected_language:
                expected_language = "zh" if _has_chinese(case["message"]) else "en"

            reply_text = response.reply.text

            # --- Dimension 1: Routing (mode + risk) ---
            routing_pass = response.mode == case["expected_mode"] and response.risk.risk_level == case["expected_risk"]

            # --- Dimension 2: Redline compliance ---
            redline_pass = not _detect_redline(reply_text)

            # --- Dimension 3: Structure compliance (three-part labels) ---
            # Crisis mode is exempt: safety templates intentionally omit
            # the three-part structure in favor of deterministic safety guidance.
            structure_pass = _check_structure(reply_text, expected_language, response.mode)

            # --- Dimension 4: Language consistency ---
            lang_pass = _check_language(reply_text, expected_language)

            # Overall pass: all four dimensions must pass
            passed = routing_pass and redline_pass and structure_pass and lang_pass

            results.append(
                {
                    "name": case["name"],
                    "passed": passed,
                    "mode": response.mode,
                    "risk": response.risk.risk_level,
                    "routing_pass": routing_pass,
                    "redline_pass": redline_pass,
                    "structure_pass": structure_pass,
                    "lang_pass": lang_pass,
                    "expected_language": expected_language,
                    # Actual reply for diagnosis when a dimension fails.
                    "reply": reply_text[:400],
                }
            )

    return results


def main() -> None:
    results = run_eval_cases()
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
