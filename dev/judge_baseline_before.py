"""Before-side baseline: generate replies with pre-change code + judge scoring.

Runs on a detached checkout of 7508e09 (before batches 1-3). The judge
module is identical on both sides, so dimension deltas measure only the
generation-side changes. Judge 429s get bounded backoff retries.
"""

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, "src")

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.evals.judge import score_reply
from psych_support_bot.evals.runner import _cleanup_eval_data, _has_chinese
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service


def score_with_retry(*args, **kwargs):
    for attempt in range(5):
        try:
            return score_reply(*args, **kwargs)
        except Exception as exc:
            if attempt == 4:
                raise
            wait = 8 * (attempt + 1)
            print(f"  judge 429/错误，{wait}s 后重试 ({attempt + 1}/4): {str(exc)[:80]}", flush=True)
            time.sleep(wait)


def main() -> None:
    init_db()
    cases = json.loads(Path("tests/evals/cases.json").read_text())
    selected = [c for c in cases if c.get("expected_mode", "support") == "support" and c.get("expected_risk") == "low"]
    dims = ["attribution_safety", "boundary", "empathy", "action_appropriateness", "expression_naturalness"]
    totals: dict[str, list[int]] = {d: [] for d in dims}

    with SessionLocal() as session:
        _cleanup_eval_data(session)
        for idx, case in enumerate(selected, start=1):
            user_id = f"eval-empathy-base-{idx}-{uuid4().hex[:8]}"
            try:
                response = conversation_service.respond(
                    ConversationRequest(user_id=user_id, message=case["message"]), session=session
                )
            except Exception as exc:  # noqa: BLE001 — 评测脚本需在任意端点错误下给出指引
                print(f"生成失败: {exc}")
                break
            reply = response.reply.text
            lang = case.get("expected_language") or ("zh" if _has_chinese(case["message"]) else "en")
            scores = score_with_retry(case["message"], reply, response.mode, response.risk.risk_level, lang)
            print(f"[{case['name']}] {case['message'][:36]}", flush=True)
            print(f"  回复: {reply[:110]!r}", flush=True)
            for d in dims:
                s = scores.get(d, {}).get("score", -1)
                totals[d].append(int(s))
                print(f"  {d}: {s}", flush=True)

    print("\n=== 基线（改动前 7508e09）各维度均值 ===", flush=True)
    for d in dims:
        vals = [v for v in totals[d] if v >= 0]
        if vals:
            print(f"  {d}: {sum(vals) / len(vals):.2f} (n={len(vals)})", flush=True)


if __name__ == "__main__":
    main()
