"""One-off: latency baseline from Langfuse — per-stage P50/P95 before optimization.

Usage: uv run python dev/latency_baseline.py [limit]
Stage breakdown (startTime/endTime based, works on historical traces):
- total        : conversation_graph.invoke trace latency
- risk_llm     : llm.invoke under node.risk_classifier (semantic risk backstop)
- reply_llm    : llm.invoke under node.response_generator
- risk_node    : node.risk_classifier wall time
- gen_node     : node.response_generator wall time
"""

import base64
import os
import statistics
import sys
from collections import defaultdict

import httpx
from dotenv import load_dotenv

load_dotenv()

HOST = os.environ["LANGFUSE_HOST"].rstrip("/")
AUTH = base64.b64encode(
    f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
).decode()

client = httpx.Client(
    headers={"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"},
    timeout=60,
)


def jget(path: str, **params):
    r = client.get(f"{HOST}{path}", params=params)
    r.raise_for_status()
    return r.json()


def parse_ts(ts: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def elapsed_ms(obs: dict) -> float:
    try:
        return (parse_ts(obs["endTime"]) - parse_ts(obs["startTime"])) * 1000
    except (KeyError, TypeError, ValueError):
        return 0.0


def pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def fmt(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values)} P50={statistics.median(values):.0f}ms "
        f"P95={pctl(values, 95):.0f}ms max={max(values):.0f}ms"
    )


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
data = jget("/api/public/traces", limit=limit, name="conversation_graph.invoke")
traces = data.get("data", [])
print(f"fetched {len(traces)} conversation traces\n")

stages: dict[str, list[float]] = defaultdict(list)
by_mode: dict[str, list[float]] = defaultdict(list)

for t in traces:
    tid = t.get("id")
    try:
        full = jget(f"/api/public/traces/{tid}")
    except Exception:  # noqa: BLE001
        continue
    tree = full.get("observations") or []
    if not tree:
        continue
    by_id = {o.get("id"): o for o in tree}

    # 分型：按 response_generator 输入的 mode（crisis/support/…）
    gen_nodes = [o for o in tree if o.get("name") == "node.response_generator"]
    mode = "?"
    if gen_nodes:
        g_in = gen_nodes[0].get("input") or {}
        mode = str(g_in.get("mode") or "?")

    total = t.get("latency")  # seconds per API docs
    if total:
        stages["total"].append(float(total) * 1000)
        by_mode[mode].append(float(total) * 1000)

    for o in tree:
        name = o.get("name", "")
        if name in {"node.risk_classifier", "node.response_generator"}:
            stages[name].append(elapsed_ms(o))
        elif name == "llm.invoke":
            parent = by_id.get(o.get("parentObservationId") or "")
            pname = (parent or {}).get("name", "?")
            key = {"node.risk_classifier": "risk_llm", "node.response_generator": "reply_llm"}.get(
                pname, "llm_other"
            )
            stages[key].append(elapsed_ms(o))

print("== 全量分阶段 ==")
for key in ["total", "risk_node", "risk_llm", "gen_node", "reply_llm", "llm_other"]:
    print(f"  {key:12s} {fmt(stages.get(key, []))}")

print("\n== 按 mode 分型的 trace 总延迟 ==")
for mode, values in sorted(by_mode.items()):
    print(f"  {mode:12s} {fmt(values)}")

client.close()
