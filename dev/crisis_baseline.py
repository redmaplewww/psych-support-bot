"""危机误路由基线巡检（P1，月度重跑）。

数据源：Langfuse `conversation_graph.invoke` root spans（含完整用户消息
与最终 mode/risk 裁决）。判定逻辑：

- 真实用户（u_*）与自动化流量（eval/e2e/process/test）分离统计——
  2026-09-06 基线显示夹具占 trace 总量 ~89%，不分离则统计无意义。
- 误报嫌疑人 = 生产路由进 crisis 但当前规则层判 low/elevated 的轮次。
  注意跨轮升级（连续 elevated → high）合法地会把良性单轮送进危机路径，
  所以嫌疑 ≠ 误报，需要人工复核。
- 漏报嫌疑人 = 按当前规则层应判 high/critical 但生产未走危机路径的轮次
  （安全敏感方向，优先复核）。
- 词表直接 import rules.py——单一真相源，避免巡检词表与线上规则漂移
  （上一版手抄词表曾把 "I want to die and hurt myself" 误标为嫌疑人）。

用法：.venv/bin/python dev/crisis_baseline.py [--days 35]
报告落盘 dev/reports/crisis_baseline_<date>.md
"""

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from psych_support_bot.ai.safety.rules import classify_message_risk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "dev" / "reports"

GENUINE_NOTE = "（生产语境下的真危机信号，词表外表述由人工复核判断）"


def _langfuse_creds() -> tuple[str, str, str]:
    creds = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                creds.setdefault(key.strip(), value.strip())
    host = os.environ.get("LANGFUSE_HOST", creds.get("LANGFUSE_HOST", ""))
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", creds.get("LANGFUSE_PUBLIC_KEY", ""))
    sec = os.environ.get("LANGFUSE_SECRET_KEY", creds.get("LANGFUSE_SECRET_KEY", ""))
    if not (host and pub and sec):
        raise SystemExit("Langfuse credentials missing (.env or environment)")
    return host, pub, sec


def fetch_turns(days: int) -> list[dict]:
    host, pub, sec = _langfuse_creds()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    turns: list[dict] = []
    page = 1
    client = httpx.Client(auth=(pub, sec), timeout=60)
    try:
        while True:
            for attempt in range(5):
                resp = client.get(
                    f"{host}/api/public/observations",
                    params={
                        "name": "conversation_graph.invoke",
                        "fromStartTime": since,
                        "limit": 100,
                        "page": page,
                    },
                )
                if resp.status_code != 429:
                    break
                # 云端限流：退避后重试同一页
                time.sleep(min(2.0 * (attempt + 1), 8.0))
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for obs in data:
                inp, out = obs.get("input") or {}, obs.get("output") or {}
                if not (isinstance(inp, dict) and isinstance(out, dict)):
                    continue
                message, mode, risk = inp.get("message"), out.get("mode"), out.get("risk_level")
                if not (message and mode):
                    continue
                turns.append({
                    "ts": (obs.get("startTime") or "")[:16],
                    "user": inp.get("user_id") or "unknown",
                    "message": message,
                    "mode": mode,
                    "risk": risk,
                })
            meta = resp.json().get("meta", {})
            if page >= meta.get("totalPages", 1):
                break
            page += 1
            time.sleep(0.6)
    finally:
        client.close()
    return turns


def user_class(user: str) -> str:
    if user.startswith("u_"):
        return "real"
    if user.startswith(("eval-user", "e2e-", "process-user", "test", "traffic-sim", "repro", "p0-")):
        return "automated"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35, help="回看窗口（天）")
    args = parser.parse_args()

    turns = fetch_turns(args.days)
    if not turns:
        raise SystemExit("no conversation turns found in window")
    real = [t for t in turns if user_class(t["user"]) == "real"]
    automated = [t for t in turns if user_class(t["user"]) == "automated"]
    unknown = [t for t in turns if user_class(t["user"]) == "unknown"]

    lines: list[str] = []
    lines.append(f"# 危机误路由基线巡检 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"窗口：近 {args.days} 天，共 {len(turns)} 轮（real {len(real)} / automated {len(automated)} / unknown {len(unknown)}）")
    lines.append(f"时间跨度：{min(t['ts'] for t in turns)} → {max(t['ts'] for t in turns)}")

    real_crisis = [t for t in real if t["mode"] == "crisis" or t["risk"] in {"high", "critical"}]
    lines.append(f"\n## 真实用户危机率：{len(real_crisis)}/{len(real)} ({len(real_crisis)/max(len(real),1):.1%})")

    # 误报嫌疑人：路由进危机但当前规则层不同意（含跨轮升级合法情形，需人工复核）
    suspects = [
        t for t in real_crisis
        if classify_message_risk(t["message"]).risk_level not in {"high", "critical"}
    ]
    lines.append(f"\n## 误报嫌疑人（crisis 路由 vs 当前规则层不同意）：{len(suspects)}")
    lines.append(f"注意：跨轮升级会把良性单轮合法送入危机路径，嫌疑需人工复核。{GENUINE_NOTE}")
    for t in sorted(suspects, key=lambda x: x["ts"]):
        rule = classify_message_risk(t["message"])
        lines.append(f"- {t['ts']} [{t['risk']}] rule={rule.risk_level}/{rule.reason[:40]} | {t['user']} {t['message'][:60]!r}")

    # 漏报嫌疑人：当前规则层判 high/critical 但生产未走危机路径
    false_neg = [
        t for t in real
        if t["mode"] != "crisis" and t["risk"] not in {"high", "critical"}
        and classify_message_risk(t["message"]).risk_level in {"high", "critical"}
    ]
    lines.append(f"\n## 漏报嫌疑人（规则层 high/critical 但生产未走危机路径）：{len(false_neg)}")
    for t in sorted(false_neg, key=lambda x: x["ts"]):
        lines.append(f"- {t['ts']} [{t['risk']}/{t['mode']}] {t['user']} {t['message'][:60]!r}")

    # 自动化流量占比（环境分流效果观测）
    lines.append(f"\n## 流量构成：real {len(real)/len(turns):.0%} / automated {len(automated)/len(turns):.0%} / unknown {len(unknown)/len(turns):.0%}")
    lines.append("（unknown 偏高说明仍有脚本未打 LANGFUSE_ENVIRONMENT 标记或 user_id 不规范）")

    report = "\n".join(lines)
    print(report)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"crisis_baseline_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nreport saved → {out_path}")


if __name__ == "__main__":
    main()
