"""Deep-dive: raw fields for recent traces + observation trees of suspects."""
import base64
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
HOST = os.environ["LANGFUSE_HOST"].rstrip("/")
AUTH = base64.b64encode(
    f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
).decode()
client = httpx.Client(
    headers={"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}, timeout=30
)


def jget(path):
    r = client.get(f"{HOST}{path}")
    r.raise_for_status()
    return r.json()


def clip(t, n=70):
    if not t:
        return ""
    t = str(t).replace("\n", "\\n")
    return t[:n] + ("…" if len(t) > n else "")


data = jget("/api/public/traces?limit=20")
traces = sorted(data.get("data", []), key=lambda x: x.get("timestamp", ""))
print("== RAW LIST ==")
for t in traces[-14:]:
    print(
        f"id={t['id'][:10]} ts={t['timestamp'][:19]} latency={t.get('latency')} "
        f"sessionId={str(t.get('sessionId'))[:12]} userId={t.get('userId')} name={t.get('name')}"
    )

print("\n== TREES OF SUSPECTS ==")
for tid in [t["id"] for t in traces[-4:]]:
    full = jget(f"/api/public/traces/{tid}")
    print(f"\n--- TRACE {tid[:10]} ts={full['timestamp'][:19]} ---")
    inp = full.get("input")
    print(f"trace.input     : {clip(json.dumps(inp, ensure_ascii=False), 110)}")
    out = full.get("output")
    print(f"trace.output    : {clip(json.dumps(out, ensure_ascii=False), 110)}")
    for o in sorted(full.get("observations") or [], key=lambda x: str(x.get("startTime"))):
        if o.get("name") != "node.response_generator":
            continue
        i = o.get("input") or {}
        oo = o.get("output") or {}
        print(
            f"  gen span [{o['startTime'][11:19]}] lvl={o.get('level')} "
            f"user={clip(i.get('user_message'), 40)} risk={i.get('risk_level')} "
            f"fb={oo.get('fallback_used')}"
        )
        rep = oo.get("reply_text")
        if rep:
            print(f"    reply: {clip(rep, 130)}")

client.close()
