"""共情化 A/B 评测：端到端生成回复 + judge 五维评分。

用法（需要主 LLM 端点可用——当前 .env 端点返回 403 governance 错误，
恢复后直接运行）：

    uv run python dev/judge_empathy_compare.py

流程：对 tests/evals/cases.json 的 support/低风险用例走完整对话管线生成
回复，judge（独立端点）按 attribution_safety / boundary / empathy /
action_appropriateness / expression_naturalness 五维打分，输出各维度
均值。对比基线：git checkout <改动前 commit> 后再跑一次本脚本。

批次3 验证记录（judge 鉴别力，2026-09-04）：同一消息的机械式回复
（播报/罗列/多问/临床词）empathy=3、expression_naturalness=3；共情式
（镜像用词/单问/口语）两维均为 5——量表对目标差异敏感。
"""

import json
from pathlib import Path
from uuid import uuid4

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.evals.judge import score_reply
from psych_support_bot.evals.runner import _cleanup_eval_data, _has_chinese
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service


def main() -> None:
    init_db()
    cases_path = Path("tests/evals/cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    # 只取 support/low 用例——共情化改动的主作用面
    selected = [c for c in cases if c.get("expected_mode", "support") == "support" and c.get("expected_risk") == "low"]

    dims = ["attribution_safety", "boundary", "empathy", "action_appropriateness", "expression_naturalness"]
    totals: dict[str, list[int]] = {d: [] for d in dims}

    with SessionLocal() as session:
        _cleanup_eval_data(session)
        for idx, case in enumerate(selected, start=1):
            user_id = f"eval-empathy-{idx}-{uuid4().hex[:8]}"
            try:
                response = conversation_service.respond(
                    ConversationRequest(user_id=user_id, message=case["message"]),
                    session=session,
                )
            except Exception as exc:  # noqa: BLE001 — 端点不可用时给出明确指引
                print(f"生成失败（主 LLM 端点不可用？）: {exc}")
                print("提示：主端点恢复后重跑本脚本；judge 端点独立配置不受影响。")
                return
            reply_text = response.reply.text
            expected_language = case.get("expected_language") or ("zh" if _has_chinese(case["message"]) else "en")
            scores = score_reply(
                case["message"], reply_text, response.mode, response.risk.risk_level, expected_language
            )
            print(f"\n[{case['name']}] {case['message'][:40]}")
            print(f"  回复: {reply_text[:120]!r}")
            for d in dims:
                s = scores.get(d, {}).get("score", -1)
                totals[d].append(int(s))
                print(f"  {d}: {s}")

    print("\n=== 各维度均值（共情化后） ===")
    for d in dims:
        vals = [v for v in totals[d] if v >= 0]
        if vals:
            print(f"  {d}: {sum(vals) / len(vals):.2f} (n={len(vals)})")


if __name__ == "__main__":
    main()
