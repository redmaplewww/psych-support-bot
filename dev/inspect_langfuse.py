"""One-off: pull recent conversation traces from Langfuse cloud for review."""
import base64
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

HOST = os.environ["LANGFUSE_HOST"].rstrip("/")
AUTH = (
    base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()
)

client = httpx.Client(
    headers={"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"},
    timeout=30,
)


def jget(path: str, **params):
    r = client.get(f"{HOST}{path}", params=params)
    r.raise_for_status()
    return r.json()


def clip(text, n=90):
    if text is None:
        return ""
    text = str(text).replace("\n", "\\n")
    return text[:n] + ("…" if len(text) > n else "")


# List the most recent root conversation traces
data = jget("/api/public/traces", limit=int(sys.argv[1]) if len(sys.argv) > 1 else 12)
traces = data.get("data", data if isinstance(data, list) else [])
print(f"fetched {len(traces)} traces\n")
rows = []
for t in traces:
    ts = t.get("timestamp", "")
    latency = t.get("latency")
    rows.append((ts, t))
for ts, t in sorted(rows):
    tid = t.get("id")
    name = t.get("name")
    # Pull a couple of interesting observations instead of guessing shape
    obs = None
    try:
        full = jget(f"/api/public/traces/{tid}")
        obs = full
    except Exception as e:  # noqa: BLE001
        print(f"[{ts}] id={tid} name={name} latency={latency} detail_error={e}")
        continue

    tree = full.get("observations") or []
    gen_spans = [o for o in tree if o.get("name") == "node.response_generator"]
    gen_in = (gen_spans[0].get("input") or {}) if gen_spans else {}
    gen_out = (gen_spans[0].get("output") or {}) if gen_spans else {}

    user_msg = ""
    if isinstance(full.get("input"), dict):
        user_msg = full["input"].get("message") or full["input"].get("user_message") or ""
    mode = full.get("metadata", {}).get("mode") or gen_in.get("mode") or "?"
    risk = gen_in.get("risk_level", "?")
    reply = (full.get("output") or {}).get("reply_text") if isinstance(full.get("output"), dict) else None
    fb = gen_out.get("fallback_used")
    consult_note = gen_out.get("consultation_notes")

    when = ts.replace("T", " ")[:19]
    print(f"[{when}] mode={mode} risk={risk} fallback={fb} latency={latency}")
    print(f"  USER : {clip(user_msg)}")
    if reply:
        print(f"  REPLY: {clip(reply, 160)}")
    elif gen_out:
        print(f"  GEN_OUT: {clip(json.dumps(gen_out, ensure_ascii=False), 140)}")
    if consult_note:
        print(f"  CONSULT: {clip(consult_note, 100)}")
    print()

client.close()
