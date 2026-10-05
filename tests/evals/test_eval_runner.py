from psych_support_bot.evals.runner import run_eval_cases


def test_eval_runner_cases_pass() -> None:
    results = run_eval_cases()
    assert results
    failed = [r for r in results if not r["passed"]]
    for item in failed:
        reasons: list[str] = []
        if not item.get("routing_pass", True):
            reasons.append(f"routing(mode={item['mode']}, risk={item['risk']})")
        if not item.get("redline_pass", True):
            reasons.append("redline")
        if not item.get("structure_pass", True):
            reasons.append("structure")
        if not item.get("lang_pass", True):
            reasons.append(f"language(expected={item.get('expected_language', '?')})")
        print(f"FAILED: {item['name']} — {', '.join(reasons)}")
    assert all(bool(item["passed"]) for item in results), (
        f"{len(failed)} eval case(s) failed: {[r['name'] for r in failed]}"
    )
