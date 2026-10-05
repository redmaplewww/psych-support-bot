#!/usr/bin/env python3
"""回半环闭环验证：u_v1 确认路径（D3 面板应出现），u_v2 否认路径（D8 边界应出现）。"""
import json, socket, time, urllib.request, urllib.error
from urllib.parse import urlparse

BASE = "https://psych-support-bot.ericdocmic.top"
assert urlparse(BASE).scheme == "https" and urlparse(BASE).hostname == "psych-support-bot.ericdocmic.top"
for info in socket.getaddrinfo(urlparse(BASE).hostname, 443):
    assert not info[4][0].startswith(("127.", "10.", "192.168.", "169.254.")), "私网地址"

LOG = "/tmp/e2e_verify.log"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def call(method, path, payload=None):
    assert path.startswith("/v1/")
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:150]

def chat(user, text, sid=None):
    body = {"user_id": user, "message": text}
    if sid: body["session_id"] = sid
    code, data = call("POST", "/v1/conversations/respond", body)
    if code != 200 or not isinstance(data, dict):
        log(f"  !! chat 失败 {user} http={code}"); return sid, None
    log(f"  [{user}] risk={data.get('risk',{}).get('risk_level')} reply={(data.get('reply',{}).get('text') or '')[:70]}")
    return data.get("session_id"), data.get("reply", {}).get("text")

def panel(user, tag):
    code, data = call("GET", f"/v1/me/profile-panel?user_id={user}")
    if code == 200:
        s = [(x["dimension"], [i["label"] for i in x["items"]]) for x in data.get("sections", [])]
        log(f"  PANEL[{tag}] familiarity={data['avatar']['familiarity']} {json.dumps(s, ensure_ascii=False)[:300]}")
    else:
        log(f"  !! panel 失败 http={code}")

TURNS = [
    "部门例会我能不说话就不说话，特别怕被评价，怕大家觉得我讲得烂",
    "轮到我汇报，我前一晚会把每句话在脑子里排练好多遍，还是怕出错",
    "上周讲一半感觉有人皱眉，我干脆找了个借口提前溜了，之后更不敢开口",
]

log("== V1: 确认路径 (u_v1) ==")
sid = None
for i, t in enumerate(TURNS, 1):
    sid, _ = chat("u_v1", t, sid); time.sleep(8)
    if i == 3:
        panel("u_v1", "三轮后（此时应已有 L4≥0.7 候选）")
_save = None
sid, _ = chat("u_v1", "对，就是这样，一想到要开口我就想躲，你说的完全对", sid)
panel("u_v1", "V1 确认后（D3 区应出现=confirm 生效）")

log("== V2: 否认路径 (u_v2) ==")
sid = None
for i, t in enumerate(TURNS, 1):
    sid, _ = chat("u_v2", t, sid); time.sleep(8)
sid, _ = chat("u_v2", "不是这样的，我不怕说话，我只是单纯不想说话，没有这回事", sid)
panel("u_v2", "V2 否认后（D8 边界应出现=reject 生效）")
log("== 验证轮结束 ==")
