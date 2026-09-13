"""Known-gap live validation: real LLM semantic topics on the 3 gap messages.

Bounded retries around transient empty responses from the dots gateway
(风险层对空输出 fail-safe 到规则判定，不影响安全).
"""

import sys
import time

sys.path.insert(0, "src")

from psych_support_bot.ai.knowledge.index import retrieve_knowledge_entries
from psych_support_bot.ai.safety.llm_classifier import classify_risk_llm

GAP_CASES = [
    ("我心情一直很低落，什么都不想做", "support", "depression"),
    ("I lost my father three months ago and I still cry every day", "support", "grief"),
    ("我想给自己定个小计划，早点睡", "planning", "sleep"),
]


def classify_with_retry(msg: str, lang: str):
    for attempt in range(4):
        try:
            return classify_risk_llm(msg, lang)
        except ValueError as exc:
            if "unparseable output: ''" in str(exc) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def main() -> None:
    hits = 0
    for msg, mode, want in GAP_CASES:
        lang = "zh" if any("\u4e00" <= c <= "\u9fff" for c in msg) else "en"
        risk, read = classify_with_retry(msg, lang)
        merged = retrieve_knowledge_entries(
            msg, mode=mode, risk_level=risk.risk_level, limit=5, extra_topics=read.topics
        )
        ok = want in read.topics
        hits += ok
        print(
            ("✓" if ok else "✗"),
            repr(msg[:24]),
            "→",
            risk.risk_level,
            "| topics =",
            read.topics,
            "| emo =",
            read.emotional_state[:30],
        )
        print("   合并检索:", [e.entry_id for e in merged][:4], flush=True)
    print(f"\n{hits}/3 主题命中")


if __name__ == "__main__":
    main()
