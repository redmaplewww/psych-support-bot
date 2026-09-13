"""定位「换个方向吧」轮次：模型当时到底看到了什么。"""

import json
import os
import time

import httpx
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _creds():
    creds = {}
    for line in (PROJECT_ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            creds.setdefault(k.strip(), v.strip())
    host = os.environ.get("LANGFUSE_HOST", creds["LANGFUSE_HOST"])
    return (host if host.endswith("/") else host + "/"), creds["LANGFUSE_PUBLIC_KEY"], creds["LANGFUSE_SECRET_KEY"]


def fetch(client, name, since, pages=12):
    out, page = [], 1
    while page <= pages:
        for attempt in range(5):
            resp = client.get(
                f"{client.base_url}api/public/observations",
                params={"name": name, "fromStartTime": since, "limit": 100, "page": page},
            )
            if resp.status_code != 429:
                break
            time.sleep(min(2.0 * (attempt + 1), 8.0))
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        if not data:
            break
        out.extend(data)
        if page >= body.get("meta", {}).get("totalPages", 1):
            break
        page += 1
        time.sleep(0.5)
    return out


base, pub, sec = _creds()
client = httpx.Client(auth=(pub, sec), timeout=60, base_url=base)
roots = fetch(client, "conversation_graph.invoke", "2026-09-06T12:00:00.000Z")
gens = fetch(client, "llm.invoke", "2026-09-06T12:00:00.000Z", pages=15)
client.close()

target = None
for r in roots:
    inp = r.get("input") or {}
    if isinstance(inp, dict) and "换个方向" in str(inp.get("message", "")):
        target = r
        break

if target is None:
    print("未找到「换个方向」轮次；root 消息清单：")
    for r in roots:
        inp = r.get("input") or {}
        if isinstance(inp, dict):
            print(" ", (r.get("startTime") or "")[11:16], repr(str(inp.get("message", ""))[:40]))
    raise SystemExit

print("=== 目标轮 root span ===")
print("ts:", (target.get("startTime") or "")[:19])
meta = target.get("metadata") or {}
print("input.message:", repr((target.get("input") or {}).get("message", "")))
print("output:", json.dumps(target.get("output"), ensure_ascii=False)[:300])
print("\n=== root metadata.memory_summary（模型当时持有的记忆）===")
print(str(meta.get("memory_summary", "<none>"))[:800])

print("\n=== 同 trace 的 generations（模型实际收到的输入）===")
tid = target.get("traceId")
for g in gens:
    if g.get("traceId") != tid:
        continue
    inp = g.get("input") or {}
    sp = str(inp.get("system_prompt", ""))
    print(f"\n-- generation {g.get('id','')[:10]} ts={g.get('startTime','')[11:19]} --")
    print("user_message:", repr(str(inp.get("user_message", ""))[:80]))
    print("system_prompt 尾部 600 字：")
    print(" ", sp[-600:].replace("\n", "\n  "))
