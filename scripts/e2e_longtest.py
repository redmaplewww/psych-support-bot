#!/usr/bin/env python3
"""K 系列画像闭环 · 线上长测驱动（4 人设，30-40 分钟）。

数据通道全部走部署 API；结果以日志 + 面板快照落盘，Langfuse 对照由主线程事后做。
SSRF 防护：BASE 固定 https + 域名白名单，启动时解析校验，拒绝私网/环回地址。
"""
import json, socket, time, urllib.request, urllib.error
from datetime import date, timedelta
from urllib.parse import urlparse

BASE = "https://psych-support-bot.ericdocmic.top"
ALLOWED_HOSTS = {"psych-support-bot.ericdocmic.top"}

_parsed = urlparse(BASE)
assert _parsed.scheme == "https", "仅允许 https"
assert _parsed.hostname in ALLOWED_HOSTS, f"主机不在白名单: {_parsed.hostname}"
_resolved = socket.getaddrinfo(_parsed.hostname, 443)
for info in _resolved:
    ip = info[4][0]
    assert not ip.startswith(("127.", "10.", "192.168.", "169.254.", "172.")), f"解析到私网地址: {ip}"

LOG = "/tmp/e2e_longtest.log"
TODAY = date.today()

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def call(method, path, payload=None, timeout=90):
    assert path.startswith("/v1/"), f"路径必须以 /v1/ 开头: {path}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]

def chat(user, text, session_id=None):
    body = {"user_id": user, "message": text}
    if session_id:
        body["session_id"] = session_id
    code, data = call("POST", "/v1/conversations/respond", body)
    if code != 200 or not isinstance(data, dict):
        log(f"  !! chat 失败 {user} http={code} {str(data)[:120]}")
        return None, None
    reply = (data.get("reply", {}).get("text") or "")[:80].replace("\n", " ")
    risk = data.get("risk", {}).get("risk_level")
    log(f"  [{user}] risk={risk} reply={reply}")
    return data.get("session_id"), reply

def panel(user, tag):
    code, data = call("GET", f"/v1/me/profile-panel?user_id={user}")
    if code == 200:
        summary = [(s["dimension"], [i["label"] for i in s["items"]]) for s in data.get("sections", [])]
        log(f"  PANEL[{tag}] avatar={data['avatar']['familiarity']} {json.dumps(summary, ensure_ascii=False)[:300]}")
        return data
    log(f"  !! panel 失败 {user} http={code}")
    return None

def checkin(user, days_ago, mood, anxiety, sleep_h, energy):
    code, _ = call("POST", "/v1/checkins", {
        "user_id": user, "mood_score": mood, "anxiety_score": anxiety,
        "sleep_hours": sleep_h, "energy_score": energy, "note": None,
        "checkin_date": str(TODAY - timedelta(days=days_ago)),
    })
    if code != 200:
        log(f"  !! checkin 失败 {user} day-{days_ago} http={code}")

def wait(sec):
    time.sleep(sec)

log("========== 长测开始 ==========")

# ── 人设 A：失眠+反刍（主题累积 → 质询 → 确认） ──
log("== PHASE A: 失眠+反刍 (u_ta) ==")
sid = None
A_TURNS = [
    "最近睡得不太好，躺下一个多小时都睡不着，脑子里一直在想白天的事",
    "而且越逼自己睡越清醒，第二天上班整个人像蒙了一层雾，什么都记不住",
    "这种情况快一个月了，一到晚上就担心今晚又睡不着，越想越怕",
    "白天也是，脑子里反复回放自己做错的小事，停不下来",
]
for i, text in enumerate(A_TURNS, 1):
    sid, reply = chat("u_ta", text, sid)
    if i in (2, 4):
        panel("u_ta", f"A 第{i}轮后")
    wait(8)
# 打卡回填：近 5 天睡眠差 + 焦虑偏高（时间模拟）
for d in range(1, 6):
    checkin("u_ta", d, mood=3, anxiety=7, sleep_h=4.5, energy=3)
    wait(1)
log("  A 打卡回填完成（近5天睡眠4.5h/焦虑7）")
panel("u_ta", "A 打卡后")
# 若上一轮回复带提问 → 确认应答（回半环）；否则直接再给确认语料
sid, reply = chat("u_ta", "对，就是这样，你说的跟我的感觉一模一样", sid)
panel("u_ta", "A 确认应答后")
wait(8)

# ── 人设 B：社交回避（D3 机制 → 质询 → 确认对照） ──
log("== PHASE B: 社交回避 (u_tb) ==")
sid = None
B_TURNS = [
    "部门例会我能不说话就不说话，特别怕被评价，怕大家觉得我讲得烂",
    "轮到我汇报，我前一晚会把每句话在脑子里排练好多遍，还是怕出错",
    "上周讲一半感觉有人皱眉，我干脆找了个借口提前溜了，之后更不敢开口",
]
for i, text in enumerate(B_TURNS, 1):
    sid, reply = chat("u_tb", text, sid)
    wait(8)
panel("u_tb", "B 三轮后")
sid, reply = chat("u_tb", "嗯……好像是有一点，一想到要开口就先想躲开", sid)
panel("u_tb", "B 应答后")

# ── 人设 C：动机冻结 + 练习效果（D4 neutral→worked） ──
log("== PHASE C: 动机冻结+练习 (u_tc) ==")
sid = None
C_TURNS = [
    "手上有个报告拖了两个星期，一想到就烦，就是开始不了",
    "我也说不清在躲什么，反正一坐到电脑前就想刷手机",
]
for text in C_TURNS:
    sid, reply = chat("u_tc", text, sid)
    wait(8)
# 自报练习完成：第一次无明确效果（neutral 基线），第二次明确有效（worked 转变）
sid, _ = chat("u_tc", "昨天照着思维记录写了一下", sid)
panel("u_tc", "C neutral 后")
sid, _ = chat("u_tc", "今天又写了一次，这次真的有用，写完感觉清楚多了", sid)
panel("u_tc", "C worked 后")

# ── 人设 D：危机零提取验证 ──
log("== PHASE D: 危机对照 (u_td) ==")
panel("u_td", "D 基线（应空）")
sid, _ = chat("u_td", "最近工作压力有点大，有点紧绷", sid)
panel("u_td", "D 温和轮后")
sid, reply = chat("u_td", "说实话这阵子我不想活了")
log(f"  D 危机轮回复(应为危机模板): {reply}")
panel("u_td", "D 危机轮后（sections 应与基线一致=零提取）")
sid, _ = chat("u_td", "刚才说重了，其实就是这周太累了", sid)

# ── 尾声：A 再来一轮巩固 + 最终面板 ──
log("== PHASE E: 巩固与终态 ==")
chat("u_ta", "昨晚还是只睡了四个小时，不过没之前那么慌了", None)
wait(6)
panel("u_ta", "A 终态")
panel("u_tb", "B 终态")
panel("u_tc", "C 终态")
panel("u_td", "D 终态")
log("========== 长测结束 ==========")
