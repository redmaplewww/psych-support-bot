#!/usr/bin/env python3
"""并行第二车道：哀伤(E) + 惊恐/放松(F)。SSRF 防护同 e2e_longtest.py。"""
import json, socket, time, urllib.request, urllib.error
from urllib.parse import urlparse

BASE = "https://psych-support-bot.ericdocmic.top"
assert urlparse(BASE).scheme == "https" and urlparse(BASE).hostname == "psych-support-bot.ericdocmic.top"
for info in socket.getaddrinfo(urlparse(BASE).hostname, 443):
    assert not info[4][0].startswith(("127.", "10.", "192.168.", "169.254.")), "私网地址"

LOG = "/tmp/e2e_lane2.log"

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
        log(f"  PANEL[{tag}] familiarity={data['avatar']['familiarity']} {json.dumps(s, ensure_ascii=False)[:260]}")

log("== 车道2 开始 ==")
log("== E: 哀伤 (u_te) ==")
sid = None
for t in ["妈妈走了快半年了，前几天翻出她包的饺子，我一个人在厨房站了一下午",
          "最近总是一个人发呆，饭也不太想吃，朋友约我也不想见",
          "有时候会想，要是当时多陪陪她就好了，越想越难受"]:
    sid, _ = chat("u_te", t, sid); time.sleep(8)
panel("u_te", "E 三轮后")
sid, _ = chat("u_te", "对，就是这样，一直走不出来", sid)
panel("u_te", "E 应答后")

log("== F: 惊恐+放松 (u_tf) ==")
sid = None
for t in ["上周开会的时候突然心慌出汗，心跳快得吓人，觉得自己要晕倒",
          "这几天总担心再来一次，坐地铁都不敢坐太挤的车厢",
          "有什么办法能让自己平静下来吗？想放松一下"]:
    sid, _ = chat("u_tf", t, sid); time.sleep(8)
panel("u_tf", "F 三轮后")
sid, _ = chat("u_tf", "照着那个接地练习做了几轮，做完感觉平静多了", sid)
panel("u_tf", "F 练习后")
log("== 车道2 结束 ==")
