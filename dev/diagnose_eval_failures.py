"""Diagnose eval failures: print per-dimension pass/fail + reply excerpts."""

import sys

sys.path.insert(0, "src")

from psych_support_bot.evals.runner import run_eval_cases


def main() -> None:
    results = run_eval_cases()
    failed = [r for r in results if not r["passed"]]
    print(f"总计 {len(results)} 条，失败 {len(failed)} 条\n")
    for item in failed:
        reasons = []
        if not item.get("routing_pass", True):
            reasons.append(f"routing(mode={item['mode']}, risk={item['risk']})")
        if not item.get("redline_pass", True):
            reasons.append("redline")
        if not item.get("structure_pass", True):
            reasons.append("structure")
        if not item.get("lang_pass", True):
            reasons.append(f"language(expected={item.get('expected_language', '?')})")
        print(f"FAILED {item['name']}: {', '.join(reasons)}")
        print(f"  回复: {item['reply'][:180]!r}\n")


if __name__ == "__main__":
    main()
