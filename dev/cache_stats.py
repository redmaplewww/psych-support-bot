"""前缀缓存命中率统计（P1，Phase 3 静态区补厚决策依据）。

采样最近的 llm.invoke generations，按天聚合 input/cached token。
预期读数（分层装配上线后）：
- 主回复调用（~1100-1200 input）：cached ≥512（静态区 zh 728 tok，单块）
- 风险分类器调用（~760 input）：cached ≥512（自有稳定前缀）
- cached=0 的比例反映多副本冷启动损失，随真实流量摊平

用法：.venv/bin/python dev/cache_stats.py [--days 7] [--pages 5]
"""

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--pages", type=int, default=5, help="采样页数（每页 100 条）")
    args = parser.parse_args()

    host, pub, sec = _langfuse_creds()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    client = httpx.Client(auth=(pub, sec), timeout=60)

    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "input": 0, "cached": 0, "hits": 0})
    total_pages = 0
    try:
        for page in range(1, args.pages + 1):
            for attempt in range(5):
                resp = client.get(
                    f"{host}/api/public/observations",
                    params={"name": "llm.invoke", "fromStartTime": since, "limit": 100, "page": page},
                )
                if resp.status_code != 429:
                    break
                time.sleep(min(2.0 * (attempt + 1), 8.0))
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", [])
            if not data:
                break
            total_pages += 1
            for obs in data:
                usage = obs.get("usageDetails") or {}
                inp, cached = usage.get("input", 0), usage.get("input_cached", 0)
                if not inp:
                    continue
                day = (obs.get("startTime") or "")[:10]
                bucket = by_day[day]
                bucket["calls"] += 1
                bucket["input"] += inp
                bucket["cached"] += cached
                if cached > 0:
                    bucket["hits"] += 1
            if page >= body.get("meta", {}).get("totalPages", 1):
                break
            time.sleep(0.6)
    finally:
        client.close()

    print(f"采样窗口：近 {args.days} 天，{total_pages} 页（每页 100 条，最新优先）")
    print(f"{'日期':<12}{'调用':>6}{'命中':>6}{'命中率':>8}{'input':>9}{'cached':>9}{'缓存率':>8}")
    totals = {"calls": 0, "input": 0, "cached": 0, "hits": 0}
    for day in sorted(by_day):
        b = by_day[day]
        for k in totals:
            totals[k] += b[k]
        print(
            f"{day:<12}{b['calls']:>6}{b['hits']:>6}{b['hits']/b['calls']:>7.0%}"
            f"{b['input']:>9}{b['cached']:>9}{b['cached']/b['input']:>7.0%}"
        )
    if totals["calls"]:
        print("-" * 58)
        print(
            f"{'合计':<12}{totals['calls']:>6}{totals['hits']:>6}"
            f"{totals['hits']/totals['calls']:>7.0%}{totals['input']:>9}{totals['cached']:>9}"
            f"{totals['cached']/totals['input']:>7.0%}"
        )
        print("\n判读：分层装配上线后主回复调用 cached 应 ≥512；全为 0 说明新代码未部署"
              "或前缀失稳（检查 Langfuse span 中 system_prompt 是否逐字稳定）。")


if __name__ == "__main__":
    main()
