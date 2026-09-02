"""Focus: real conversation_graph traces only, latest N, full reply text."""
import base64
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
HOST = os.environ["LANGFUSE_HOST"].rstrip("/")
AUTH = base64.b64encode(
    f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
).decode()
client = httpx.Client(headers={"Authorization": f"Basic {AUTH}"}, timeout=30)


def jget(path):
    r = client.get(f"{HOST}{path}")
    if r.status_code == 429:
        time.sleep(2)
        r = client.get(f"{HOST}{path}")
    r.raise_for_status()
    return r.json()


def clip(t, n=200):
    if not t:
        return ""
    t = str(t).replace("\n", "\\n")
    return t[:n] + ("…" if len(t) > n else "")


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
data = jget(f"/api/public/traces?name=conversation_graph.invoke&limit={limit}")
traces = sorted(data.get("data", []), key=lambda x: x.get("timestamp", ""))
for t in traces:
    full = jget(f"/api/public/traces/{t['id']}")
    inp = full.get("input") or {}
    out = full.get("output") or {}
    gen = [o for o in full.get("observations", []) if o.get("name") == "node.response_generator"]
    stage = ""
    consult = ""
    if gen:
        o = gen[0]
        notes = (o.get("output") or {}).get("consultation_notes")
        stage = notes.split("stage=")[-1].split(";")[0] if notes else ""
        agents = notes.split("opinions=")[0] if notes else ""
        consult = f" agents={agents}" if "agents consulted" in (notes or "") else ""
    print(
        f"[{t['timestamp'][11:19]}] u={inp.get('user_id')} sess={str(inp.get('session_id'))[:8]} "
        f"mode={out.get('mode') or inp.get('mode')} risk={out.get('risk_level')} stage={stage}{consult}"
    )
    print(f"  U : {clip(inp.get('message'), 120)}")
    print(f"  B : {clip((out.get('reply_text') or '').replace(chr(92)+'n', ' / '), 260)}")
client.close()
