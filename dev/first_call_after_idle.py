"""冷启动「激活」探测：LLM 调用距上一次的间隔 × 本次耗时的相关性。

回答的问题：流量空闲一段时间后，第一发 llm.invoke 是否更慢（网关副本冷启动 /
前缀 KV 缓存被逐出 = 需要「激活」）。判读方法：把每笔调用按「与上一笔的间隔」
分桶，对比各桶的耗时中位数/p90 与 cached 命中。

口径注意（如实声明）：
- 耗时是整笔调用（流式含全部生成），不是纯 TTFT——冷启动主要加在首 token 前，
  会整体抬高该桶耗时，趋势可信，绝对值偏大；
- 间隔按全局相邻调用算（混了风险分类/主回复/摘要等不同调用类型），另切
  「耗时>2s 长调用」子集（基本只可能是主回复/会诊生成）降低混杂；
- 样本按 Langfuse 分页拉取（最新优先），窗口内流量不均时以趋势为准。

用法：.venv/bin/python dev/first_call_after_idle.py [--days 3] [--pages 5]
"""

import argparse
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 间隔分桶（秒）：秒级连续 / 十秒级 / 分钟级 / 半小时级 / 小时级
_BUCKETS: list[tuple[str, float]] = [
    ("<5s", 5.0),
    ("5-30s", 30.0),
    ("30s-5m", 300.0),
    ("5-30m", 1800.0),
    ("30m-6h", 21600.0),
    ("≥6h", float("inf")),
]


def _langfuse_creds() -> tuple[str, str, str]:
    creds: dict[str, str] = {}
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


def _ts(obs: dict, key: str) -> datetime | None:
    raw = obs.get(key)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _p(data: list[float], q: float) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    idx = min(len(s) - 1, int(q / 100.0 * len(s)))
    return s[idx]


def _report(label: str, rows: list[tuple[float, float, int]]) -> None:
    """rows: (间隔秒, 耗时秒, cached token)。分桶输出 n/median/p90/命中。"""
    print(f"\n== {label}：n={len(rows)} ==")
    print(f"{'间隔':<9}{'n':>5}{'耗时中位':>9}{'p90':>8}{'cached命中':>9}")
    lo = 0.0
    for name, hi in _BUCKETS:
        b = [r for r in rows if lo <= r[0] < hi]
        lo = hi
        if not b:
            continue
        durs = [d for _, d, _ in b]
        hits = sum(1 for _, _, c in b if c > 0)
        print(
            f"{name:<9}{len(b):>5}{statistics.median(durs):>9.2f}{_p(durs, 90):>8.2f}"
            f"{hits / len(b):>9.0%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--pages", type=int, default=5, help="采样页数（每页 100 条，最新优先）")
    args = parser.parse_args()

    host, pub, sec = _langfuse_creds()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    client = httpx.Client(auth=(pub, sec), timeout=60)
    calls: dict[str, tuple[datetime, float, int]] = {}  # id -> (start, dur_s, cached)
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
            for obs in data:
                start, end = _ts(obs, "startTime"), _ts(obs, "endTime")
                if not (start and end):
                    continue
                usage = obs.get("usageDetails") or {}
                dur = (end - start).total_seconds()
                if 0 < dur < 300:
                    calls[obs.get("id", start.isoformat())] = (start, dur, int(usage.get("input_cached") or 0))
            if page >= body.get("meta", {}).get("totalPages", 1):
                break
            time.sleep(0.6)
    finally:
        client.close()

    seq = sorted(calls.values(), key=lambda r: r[0])
    if len(seq) < 10:
        raise SystemExit(f"样本不足（{len(seq)}），扩大 --days/--pages")
    rows = []
    for (start, dur, cached), (p_start, _, _) in zip(seq[1:], seq):
        gap = (start - p_start).total_seconds()
        rows.append((gap, dur, cached))
    _report("全部 llm.invoke（按与上一笔调用的间隔分桶）", rows)
    # 主回复子集：耗时 >2s 的调用基本只可能是主回复/会诊生成（风险分类/摘要远短）
    slow = [r for r in rows if r[1] > 2.0]
    _report("长调用子集（耗时>2s ≈ 主回复/会诊）", slow)
    print(
        "\n判读：若长间隔桶的耗时中位数/p90 显著高于短间隔桶（>1.5×），说明空闲后"
        "首字需要「激活」（副本冷启动或前缀 KV 逐出）；若持平，网关常驻，无需处理。"
    )


if __name__ == "__main__":
    main()
