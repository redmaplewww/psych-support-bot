"""Debug: print actual replies for the two failing zh eval cases."""
from psych_support_bot.evals.runner import run_eval_cases

results = run_eval_cases()
for r in results:
    if r["name"] in {"zh_assessment_case", "zh_intervention_case"}:
        print(f"=== {r['name']} structure={r['structure_pass']} ===")
        print(r.get("reply_text") or "(reply not stored in result)")
        print()

# run_eval_cases 不回传 reply 文本的话，直接在这里打印所有键
print("keys:", list(results[0].keys()))
