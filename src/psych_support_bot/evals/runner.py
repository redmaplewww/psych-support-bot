from __future__ import annotations

import json
from pathlib import Path

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service


def run_eval_cases() -> list[dict[str, str | bool]]:
    init_db()
    cases_path = Path("tests/evals/cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results: list[dict[str, str | bool]] = []

    with SessionLocal() as session:
        for idx, case in enumerate(cases, start=1):
            response = conversation_service.respond(
                ConversationRequest(
                    user_id=f"eval-user-{idx}",
                    message=case["message"],
                ),
                session=session,
            )
            passed = response.mode == case["expected_mode"] and response.risk.risk_level == case["expected_risk"]
            results.append(
                {
                    "name": case["name"],
                    "passed": passed,
                    "mode": response.mode,
                    "risk": response.risk.risk_level,
                }
            )

    return results


def main() -> None:
    results = run_eval_cases()
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
