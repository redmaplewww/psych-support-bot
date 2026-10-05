"""今晚 20:00 后对话行为分析（P3-B 后回复丰满度专项）。

数据：Langfuse root spans（消息/模式/风险）+ llm.invoke generations（回复全文），
按 traceId 关联。分析维度：
- 回复形态：气泡数（\n\n 切分）、字数、三段式完整性（镜像/印象/提问）
- 丰满度信号：短回复（<60 字）占比、无提问占比、纯镜像轮占比
- 用户输入 vs 回复长度比：用户说很多、bot 答很少的失衡轮
"""

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _creds():
    creds = {}
    env_path = PROJECT_ROOT / ".env"
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            creds.setdefault(k.strip(), v.strip())
    host = os.environ.get("LANGFUSE_HOST", creds["LANGFUSE_HOST"])
    return host, creds["LANGFUSE_PUBLIC_KEY"], creds["LANGFUSE_SECRET_KEY"]


def fetch(client, name, since, pages=10):
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
        time.sleep(0.6)
    return out


def main() -> None:
    host, pub, sec = _creds()
    since = "2026-09-06T12:00:00.000Z"  # 今晚 20:00（UTC+8）
    client = httpx.Client(auth=(pub, sec), timeout=60, base_url=host if host.endswith("/") else host + "/")
    roots = fetch(client, "conversation_graph.invoke", since)
    gens = fetch(client, "llm.invoke", since, pages=15)
    client.close()

    # traceId → 回复全文（取主回复 generation：input 的 mode/温度区分不可靠，
    # 改用「该 trace 下 output 最长的 generation」为主回复）
    replies_by_trace: dict[str, str] = {}
    for g in gens:
        tid = g.get("traceId")
        text = g.get("output") or ""
        if isinstance(text, str) and len(text) > len(replies_by_trace.get(tid, "")):
            replies_by_trace[tid] = text

    turns = []
    for r in roots:
        inp, out = r.get("input") or {}, r.get("output") or {}
        if not (isinstance(inp, dict) and isinstance(out, dict)):
            continue
        user = inp.get("user_id") or ""
        if not user.startswith("u_"):
            continue
        turns.append({
            "ts": (r.get("startTime") or "")[:16],
            "trace": r.get("traceId"),
            "user": user[:12],
            "message": inp.get("message", ""),
            "mode": out.get("mode"),
            "risk": out.get("risk_level"),
            "reply": replies_by_trace.get(r.get("traceId"), ""),
        })

    print(f"窗口 {since} 之后：root={len(roots)} generations={len(gens)} 真实用户轮次={len(turns)}")
    if not turns:
        print("窗口内无真实用户对话。")
        return

    print("\n=== 逐轮明细（时间 | 模式 | 气泡数 | 回复字数 | 用户字数）===")
    stats = Counter()
    for t in sorted(turns, key=lambda x: x["ts"]):
        reply = t["reply"]
        bubbles = [b for b in reply.split("\n\n") if b.strip()]
        rlen = len(reply)
        ulen = len(t["message"])
        has_q = "？" in reply or "?" in reply
        # 三段式识别（zh/en 都算）：粗略按气泡数与问句分布
        if t["mode"] == "support" and rlen:
            stats["support_turns"] += 1
            if rlen < 60:
                stats["support_under60"] += 1
            if len(bubbles) == 1:
                stats["support_single_bubble"] += 1
            if not has_q:
                stats["support_no_question"] += 1
        stats["total_reply_chars"] += rlen
        stats["total_user_chars"] += ulen
        print(f"  {t['ts']} [{t['mode']}/{t['risk']}] 气泡={len(bubbles)} 回复={rlen}字 用户={ulen}字")
        print(f"    用户: {t['message'][:60]!r}")
        print(f"    回复: {reply[:150]!r}")
        if len(bubbles) > 1:
            print(f"    气泡2: {(bubbles[1] if len(bubbles) > 1 else '')[:80]!r}")

    print("\n=== 汇总 ===")
    st = stats["support_turns"]
    if st:
        print(f"support 轮次: {st} | <60字: {stats['support_under60']} | 单气泡: {stats['support_single_bubble']} | 无提问: {stats['support_no_question']}")
    if stats["total_user_chars"]:
        print(f"用户总字数: {stats['total_user_chars']} | 回复总字数: {stats['total_reply_chars']} | 比例 1:{stats['total_reply_chars']/max(stats['total_user_chars'],1):.2f}")


if __name__ == "__main__":
    main()
