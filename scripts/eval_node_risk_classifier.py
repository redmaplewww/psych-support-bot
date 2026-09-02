"""节点级回归：classify_risk（规则 + LLM 兜底）在 71 条标注语料上的表现。

与 scripts/eval_llm_risk_classifier.py 同口径，但走真实节点全链路
（规则判定 → low 触发 LLM 语义兜底 → max 单向合并）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from eval_llm_risk_classifier import CORPUS

from psych_support_bot.ai.nodes.risk_classifier import classify_risk
from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult


def _build_state(user_message: str):
    return {
        "user_id": "eval",
        "session_id": "eval",
        "user_message": user_message,
        "memory_summary": "",
        "knowledge_context": "",
        "mode": "support",
        "risk_result": RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
        "generated_reply": GeneratedReply(text="", style="support", includes_action_step=True),
        "session_summary": "",
        "topics": [],
        "fallback_used": False,
        "consultation_required": False,
        "consultation_agents": [],
        "consultation_notes": "",
        "consultation_opinions": [],
        "interview_stage": "engagement",
        "question_strategy": "open",
        "challenge_allowed": False,
        "loop_hint": "",
        "exercise_history": [],
        "refusal_history": [],
        "expected_language": "zh",
        "turn_count": 0,
        "safety_floor_risk_level": "",
        "no_question_mode": False,
        "last_bot_reply": "",
    }


def main() -> None:
    crisis = [(t, e) for t, e in CORPUS if e == "crisis"]
    elevated = [(t, e) for t, e in CORPUS if e == "elevated"]
    low = [(t, e) for t, e in CORPUS if e == "low"]

    node_cr_tp = rule_cr_tp = 0
    node_el_ok = rule_el_ok = 0
    node_hard_fp = rule_hard_fp = 0
    node_misses: list[tuple[str, str]] = []
    llm_calls = 0
    t0 = time.time()

    for text, expected in CORPUS:
        # 规则基线
        rr = classify_message_risk(text)
        if expected == "crisis" and rr.needs_crisis_mode and rr.risk_level in {"high", "critical"}:
            rule_cr_tp += 1
        elif expected == "elevated" and rr.risk_level in {"elevated", "high", "critical"}:
            rule_el_ok += 1
        elif expected == "low" and (rr.risk_level in {"high", "critical"} or rr.needs_crisis_mode):
            rule_hard_fp += 1

        # 节点全链路（规则 + LLM 兜底）
        state = classify_risk(_build_state(text))
        if state["risk_result"].reason.startswith("[llm]"):
            llm_calls += 1
        level = state["risk_result"].risk_level
        crisis_mode = state["risk_result"].needs_crisis_mode
        if expected == "crisis":
            if crisis_mode and level in {"high", "critical"}:
                node_cr_tp += 1
            else:
                node_misses.append((text, f"{level} crisis={crisis_mode}"))
        elif expected == "elevated":
            if level in {"elevated", "high", "critical"}:
                node_el_ok += 1
        elif expected == "low" and (level in {"high", "critical"} or crisis_mode):
            node_hard_fp += 1

    elapsed = time.time() - t0
    n_cr, n_el, n_low = len(crisis), len(elevated), len(low)
    print(f"节点级回归完成：{len(CORPUS)} 条，耗时 {elapsed:.0f}s（LLM 兜底触发 {llm_calls} 次）\n")
    print(f"{'指标':<26}{'纯规则':>12}{'节点(规则+LLM)':>16}")
    print(f"{'-'*56}")
    print(f"{'危机召回率':<28}{f'{rule_cr_tp}/{n_cr} = {rule_cr_tp/n_cr:.0%}':>14}{f'{node_cr_tp}/{n_cr} = {node_cr_tp/n_cr:.0%}':>18}")
    print(f"{'elevated 召回率':<26}{f'{rule_el_ok}/{n_el} = {rule_el_ok/n_el:.0%}':>14}{f'{node_el_ok}/{n_el} = {node_el_ok/n_el:.0%}':>18}")
    print(f"{'危机误报率(阴性→crisis)':<22}{f'{rule_hard_fp}/{n_low} = {rule_hard_fp/n_low:.0%}':>14}{f'{node_hard_fp}/{n_low} = {node_hard_fp/n_low:.0%}':>18}")
    if node_misses:
        print("\n节点漏报明细:")
        for text, got in node_misses:
            print(f"  {text!r:44} -> {got}")


if __name__ == "__main__":
    main()
