"""画像轮次提取器（K1b 确定性信号 + K2 LLM 语义提取）。

设计约束（docs/plans/profile-memory-knowledge.md §5/§6、PROFILE_DECISIONS P2）：
- 挂载点：services/conversation.py `_finalize` 末尾（消息落库之后，
  证据 message id 才存在）；fail-open——提取失败只记统计，绝不阻断对话。
- D1 复用图内现成信号：`state["topics"]` 是 LLM 语义主题（闭集校验）与
  关键词主题的并集，本轮零额外 LLM 成本。
- D4 吃练习完成信号：自报完成（detect_completed_exercise）与引导完成
  （practice_action="complete"），效果词从同轮用户话术做确定性扫描
  （负向词先判——"没什么用"包含"有用"子串）；无效果词记 neutral 基线。
- 危机轮（high/critical）零提取：高危内容不入画像层，只走 RiskEvent
  通道（知识提炼 §3 硬规则的提取侧实现）。
- P2 全量统计：每个收尾轮一行 profile_extraction_stats（含危机守卫与
  禁用跳过不记——禁用是开关不是调用）；K1 无 LLM 成本，节流与熔断阀
  留待 K2 引入 LLM 提取时启用。
- 单次 ≤3 条 claim；效果值经 update_belief_value 更新（neutral→worked
  的转变是 D4 的核心学习信号），belief key 恒为练习 tag。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from psych_support_bot.ai.profile.semantic import run_semantic_extraction, run_verification_judgment
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import Message
from psych_support_bot.infra.db.profile_repositories import (
    is_profile_memory_enabled,
    record_claim,
    record_extraction_stats,
    update_belief_value,
)

logger = logging.getLogger(__name__)

from psych_support_bot.ai.profile.constants import (
    GOAL_INITIAL_CONFIDENCE,
    LIFE_EVENT_EXPIRY_DAYS,
    LIFE_EVENT_INITIAL_CONFIDENCE,
    MAX_CLAIMS_PER_TURN,
    PROFILE_SIGNAL_THRESHOLD,
)

# 危机轮不提取（与 fixture neg_crisis 金标准一致）。
_CRISIS_LEVELS = {"high", "critical"}

# 效果词确定性扫描（K2 交 LLM 前的保守词表）。负向先判：
# "没什么用"含"有用"子串，顺序错会把负反馈记成正向。
_VALENCE_AVERSIVE: tuple[str, ...] = (
    "没什么用",
    "没有用",
    "没啥用",
    "不管用",
    "帮不上",
    "更糟",
    "更乱",
    "更焦虑",
    "更烦躁",
    "更难受",
    "不舒服",
    "不适合我",
    "做不下去",
    "坚持不下来",
    "worse",
    "didn't help",
    "not helpful",
    "not for me",
)
_VALENCE_WORKED: tuple[str, ...] = (
    "很有帮助",
    "有帮助",
    "真的有用",
    "挺有用",
    "有用",
    "帮到我",
    "帮到我了",
    "舒服多了",
    "轻松多了",
    "平静多了",
    "好多了",
    "helped",
    "helpful",
    "worked for me",
    "feeling calmer",
)


@dataclass(frozen=True)
class ProfileClaim:
    """单条提取产出——record_claim 的入参载体（契约形状见知识提炼 §5）。"""

    dimension: str
    key: str
    claim_text: str
    value: dict = field(default_factory=dict)
    relation: str = "supports"
    confidence: float = 0.0
    evidence_message_ids: list[int] = field(default_factory=list)


def scan_valence(text: str) -> str:
    """确定性效果判定：aversive / worked / neutral。负向词优先。"""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _VALENCE_AVERSIVE):
        return "aversive"
    if any(marker in lowered for marker in _VALENCE_WORKED):
        return "worked"
    return "neutral"


def d1_topic_claims(topics: list[str], *, evidence_message_id: int | None) -> list[ProfileClaim]:
    """D1 关注主题：图内 topics 逐条成 claim（调用方负责总量截断）。"""
    evidence = [evidence_message_id] if evidence_message_id else []
    return [
        ProfileClaim(
            dimension="D1",
            key=topic,
            claim_text=f"对话中出现主题信号：{topic}",
            value={"via": "graph_topics"},
            confidence=PROFILE_SIGNAL_THRESHOLD,
            evidence_message_ids=evidence,
        )
        for topic in topics
    ]


# ── Part C：生活事件检测 ──────────────────────────────────────────────
# 时间标记 + 具体动作 → 生活事件（区别于话题泛化）。
_TEMPORAL_MARKERS = ("最近", "上周", "昨天", "前天", "这几天", "这两天", "刚", "刚刚", "上个月")
_EVENT_PATTERNS = (
    "换工作",
    "换了工作",
    "新工作",
    "入职",
    "辞职",
    "离职",
    "搬家",
    "搬了家",
    "搬到",
    "搬去",
    "吵架",
    "吵了一架",
    "闹翻",
    "分手",
    "离婚",
    "冷战",
    "结婚",
    "订婚",
    "怀孕",
    "生了",
    "出生",
    "住院",
    "出院",
    "手术",
    "确诊",
    "毕业",
    "入学",
    "考试",
    "去世",
    "离开",
    "走了",
    "失去",
)


def _detect_life_event(text: str) -> str:
    """检测用户消息中的生活事件描述，返回事件摘要或空串。

    要求同时命中时间标记和事件模式（减少误报）。
    """
    if not text:
        return ""
    for marker in _TEMPORAL_MARKERS:
        if marker not in text:
            continue
        for pattern in _EVENT_PATTERNS:
            if pattern in text:
                # 提取 marker + pattern 附近的短语作为摘要（最多30字）
                start = max(0, text.find(marker) - 5)
                end = min(len(text), text.find(pattern) + len(pattern) + 10)
                snippet = text[start:end].strip()
                return snippet[:30] if snippet else pattern
    return ""


def d1_life_event_claim(
    event_summary: str,
    *,
    evidence_message_id: int | None,
) -> ProfileClaim | None:
    """D1 生活事件：具体、有时间标记的生活变化（30天过期）。

    key 用事件摘要的 hash 保证唯一（不同事件不互相覆盖）。
    """
    if not event_summary:
        return None
    import hashlib

    event_hash = hashlib.sha256(event_summary.encode()).hexdigest()[:12]
    key = f"life_event.{event_hash}"
    evidence = [evidence_message_id] if evidence_message_id else []
    from datetime import UTC, datetime, timedelta

    return ProfileClaim(
        dimension="D1",
        key=key,
        claim_text=f"用户描述了近期生活事件：{event_summary}",
        value={
            "via": "life_event",
            "event_type": "life_event",
            "summary": event_summary,
            "expires_at": (datetime.now(UTC) + timedelta(days=LIFE_EVENT_EXPIRY_DAYS)).isoformat(),
        },
        confidence=LIFE_EVENT_INITIAL_CONFIDENCE,
        evidence_message_ids=evidence,
    )


def d4_intervention_claim(
    exercise_tag: str,
    *,
    valence_text: str,
    evidence_message_id: int | None,
    target_symptom: str = "",
    specific_detail: str = "",
) -> ProfileClaim:
    """D4 干预响应：练习完成 + 同轮效果词。belief key 恒为练习 tag。

    调参 C（2026-09-13）：明确效价（worked/aversive）单次即达面板水位
    0.55——用户亲口说"有用/没用"是直接反馈，不该和模糊信号一样攒两次；
    neutral 保持 0.3 基线（完成事实 ≠ 效果判断）。

    通路2：当 target_symptom 由 AI 反馈管线提供时，存入 value_json，
    渲染时可输出"思维记录 — 对反刍有帮助"而非"思维记录，有帮助"。
    """
    effect = scan_valence(valence_text)
    confidence = {"worked": 0.55, "aversive": 0.55, "neutral": 0.3}[effect]
    symptom_hint = f"（针对{target_symptom}）" if target_symptom else ""
    claim_text = {
        "worked": f"练习 {exercise_tag} 完成后用户表达了正向反馈{symptom_hint}",
        "aversive": f"练习 {exercise_tag} 使用中用户表达了负向反馈{symptom_hint}",
        "neutral": f"用户完成了练习 {exercise_tag}（本轮无明确效果反馈）",
    }[effect]
    evidence = [evidence_message_id] if evidence_message_id else []
    value: dict[str, object] = {"tag": exercise_tag, "effect": effect}
    if target_symptom:
        value["target_symptom"] = target_symptom
    if specific_detail:
        value["specific_detail"] = specific_detail
    return ProfileClaim(
        dimension="D4",
        key=exercise_tag,
        claim_text=claim_text,
        value=value,
        confidence=confidence,
        evidence_message_ids=evidence,
    )


# Severity ordering for change-direction comparison (lower index = milder).
_SEVERITY_ORDER: dict[str, int] = {
    "minimal": 0,
    "none": 0,
    "mild": 1,
    "subthreshold": 1,
    "moderate": 2,
    "moderately_severe": 3,
    "severe": 4,
}

_SEVERITY_CONFIDENCE: dict[str, float] = {
    "minimal": 0.3,
    "none": 0.3,
    "mild": 0.4,
    "subthreshold": 0.4,
    "moderate": 0.55,
    "moderately_severe": 0.7,
    "severe": 0.85,
}


def d2_assessment_claim(
    assessment_type: str,
    severity_band: str,
    score: int,
    *,
    prev_score: int | None = None,
    prev_severity: str = "",
) -> ProfileClaim | None:
    """D2 严重度：测评完成时自动创建严重度信念。

    key 命名空间 ``severity.{type}``（如 severity.phq9），与现有
    ``worry_uncontrollable`` 锚 key 不冲突。当存在上次测评时，
    severity 改善 = contradicts，恶化 = supports。
    """
    key = f"severity.{assessment_type}"
    confidence = _SEVERITY_CONFIDENCE.get(severity_band, 0.4)
    relation = "supports"
    if prev_severity and prev_severity != severity_band:
        prev_ord = _SEVERITY_ORDER.get(prev_severity, 2)
        new_ord = _SEVERITY_ORDER.get(severity_band, 2)
        if new_ord < prev_ord:
            relation = "contradicts"  # severity decreased — contradicts prior belief

    claim_text = f"{assessment_type.upper()} 测评显示{severity_band}级别症状"
    value: dict[str, object] = {
        "assessment_type": assessment_type,
        "score": score,
        "severity": severity_band,
    }
    if prev_score is not None:
        value["prev_score"] = prev_score
        value["delta"] = score - prev_score
    return ProfileClaim(
        dimension="D2",
        key=key,
        claim_text=claim_text,
        value=value,
        relation=relation,
        confidence=confidence,
    )


def _latest_user_message_id(session: Session, session_id: str) -> int | None:
    stmt = (
        select(Message.id)
        .where(Message.session_id == session_id, Message.role == "user")
        .order_by(desc(Message.id))
        .limit(1)
    )
    return session.scalar(stmt)


# 量表/测评语境标记：用户在这些语境里提到的情绪词是"测评行为"的组成部分
# （如"焦虑量表没测准"），不是当期话题陈述——行为层频率信号不进画像内容层
# （fixture redline_reassurance_retest 金标准）。命中即抑制本轮 D1 主题
# claim（保守豁免：漏一轮的主题会在后续无测评语境的轮次自然累积）。
_ASSESSMENT_CONTEXT_MARKERS: tuple[str, ...] = (
    "量表",
    "测评",
    "评分",
    "重测",
    "测一次",
    "测一下",
    "phq",
    "gad-7",
    "gad7",
    "questionnaire",
    "retake the",
    "retest",
)


def is_assessment_context(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _ASSESSMENT_CONTEXT_MARKERS)


# ── 跨情境标签 ────────────────────────────────────────────────────────
_CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "工作": ("工作", "上班", "领导", "同事", "老板", "公司", "加班", "开会", "项目", "deadline", "kpi"),
    "关系": ("男朋友", "女朋友", "老公", "老婆", "伴侣", "对象", "分手", "吵架", "冷战", "约会", "婚姻"),
    "家庭": ("爸妈", "父母", "家人", "孩子", "小孩", "家庭", "回家", "过年", "亲戚"),
    "健康": ("身体", "生病", "医院", "睡不好", "失眠", "头疼", "胃疼", "体重", "运动"),
    "学业": ("考试", "作业", "上课", "老师", "同学", "成绩", "毕业", "论文", "考研"),
    "社交": ("朋友", "社交", "聚会", "人多", "独处", "孤单", "合群"),
}


def _detect_context_tags(text: str) -> list[str]:
    """从用户消息中检测对话情境标签（工作/关系/家庭/健康/学业/社交）。"""
    if not text:
        return []
    tags: list[str] = []
    for tag, keywords in _CONTEXT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return tags


def build_turn_claims(
    *,
    topics: list[str],
    risk_level: str,
    exercise_tag: str | None,
    valence_text: str,
) -> list[ProfileClaim]:
    """组装本轮 claims：D4 优先保留（更稀缺），D1 填充至总量上限。"""
    if risk_level in _CRISIS_LEVELS:
        return []
    claims: list[ProfileClaim] = []
    if exercise_tag:
        claims.append(d4_intervention_claim(exercise_tag, valence_text=valence_text, evidence_message_id=None))
    # D1 抑制（两条确定性规则，均为 fixture 金标准）：
    # - 量表语境：测评行为语境中的情绪词不是当期话题（行为层信号不进内容层）；
    # - 练习完成轮：完成叙述里的情绪词是练习使用的情境，不是当期话题陈述。
    # 保守豁免的漏检会在后续无此语境的轮次自然累积，代价可接受。
    if exercise_tag or is_assessment_context(valence_text):
        d1_topics: list[str] = []
    else:
        d1_topics = topics
    # Part C：生活事件优先于话题泛化（更具体、更有时效性）。
    event = _detect_life_event(valence_text) if not exercise_tag else ""
    remaining = MAX_CLAIMS_PER_TURN - len(claims)
    if event and remaining > 0:
        event_claim = d1_life_event_claim(event, evidence_message_id=None)
        if event_claim:
            claims.append(event_claim)
            remaining -= 1
    # 目标追踪：用户显式目标写入 D5 goal.* 命名空间。
    if remaining > 0:
        goal = _detect_goal(valence_text)
        if goal:
            claims.append(d5_goal_claim(goal, evidence_message_id=None))
            remaining -= 1
    # 保护因素：用户提到的支持者/应对/专业资源/生活价值。
    if remaining > 0:
        for d7_key, summary in _detect_protective_factors(valence_text):
            if remaining <= 0:
                break
            claims.append(d7_protective_claim(d7_key, summary, evidence_message_id=None))
            remaining -= 1
    claims.extend(d1_topic_claims(d1_topics[:remaining], evidence_message_id=None))
    return claims


def _detect_goal(text: str) -> str:
    """检测用户显式表达的目标（"我想改善睡眠"→"改善睡眠"）。"""
    if not text:
        return ""
    m = _BACKGROUND_GOAL.search(text)
    return m.group(1).strip() if m and m.group(1) else ""


def d5_goal_claim(goal_text: str, *, evidence_message_id: int | None) -> ProfileClaim:
    """D5 目标：用户显式表达的改变目标。key 用 goal.{hash} 保证唯一。"""
    import hashlib

    goal_hash = hashlib.sha256(goal_text.encode()).hexdigest()[:12]
    key = f"goal.{goal_hash}"
    evidence = [evidence_message_id] if evidence_message_id else []
    return ProfileClaim(
        dimension="D5",
        key=key,
        claim_text=f"用户表达了目标：{goal_text}",
        value={"goal": goal_text, "status": "active"},
        confidence=GOAL_INITIAL_CONFIDENCE,
        evidence_message_ids=evidence,
    )


# ── 保护因素 D7 提取（K1 确定性）──────────────────────────────────────
# 检测用户提到的支持者、应对策略、专业资源和生活价值。

_D7_SUPPORT_PEOPLE = re.compile(
    r"(?:我的|我有个?|有个?)(?:好朋友|朋友|闺蜜|兄弟|哥们|家人|老公|老婆|"
    r"爸爸|妈妈|爸妈|父母|孩子|同事|同学|伴侣|对象|男朋友|女朋友)"
    r"(.{0,15}?)(?:，|。|$|很|会|能|帮|陪|支持|关心)"
)
_D7_COPING = re.compile(
    r"(?:我会|我喜欢|我靠|靠|通过|用)(?:运动|跑步|散步|听音乐|冥想|"
    r"呼吸|写日记|画画|唱歌|瑜伽|游泳|做饭| gardening|整理|读书)"
    r"(.{0,10}?)(?:来|让自己|放松|平静|好受|缓解)"
)
_D7_PROFESSIONAL = re.compile(
    r"(?:我的(?:咨询师|治疗师|医生|心理老师)|看过|在看|找过"
    r"(?:咨询师|治疗师|医生|心理老师)|热线|400-161-9995|988|120)"
)
_D7_REASONS = re.compile(
    r"(?:为了|因为|放不下|舍不得|还有|不想让)(?:孩子|家人|爸妈|父母|"
    r"他们|ta|他|她|朋友|想做的事| unfinished)"
)


def _detect_protective_factors(text: str) -> list[tuple[str, str]]:
    """检测用户提到的保护因素，返回 [(d7_key, summary), ...]。"""
    results: list[tuple[str, str]] = []
    if not text or len(text) < 4:
        return results
    if _D7_SUPPORT_PEOPLE.search(text):
        results.append(("protective.support_people", _D7_SUPPORT_PEOPLE.search(text).group(0)[:30]))
    if _D7_PROFESSIONAL.search(text):
        results.append(("protective.professional_contacts", _D7_PROFESSIONAL.search(text).group(0)[:30]))
    if _D7_COPING.search(text):
        results.append(("protective.coping_strategies", _D7_COPING.search(text).group(0)[:30]))
    if _D7_REASONS.search(text):
        results.append(("protective.reasons_for_living", _D7_REASONS.search(text).group(0)[:30]))
    return results


def d7_protective_claim(
    d7_key: str,
    summary: str,
    *,
    evidence_message_id: int | None,
) -> ProfileClaim:
    """D7 保护因素：用户提到的支持者/应对/专业资源/生活价值。"""
    evidence = [evidence_message_id] if evidence_message_id else []
    return ProfileClaim(
        dimension="D7",
        key=d7_key,
        claim_text=f"用户提到了保护因素：{summary}",
        value={"summary": summary},
        confidence=PROFILE_SIGNAL_THRESHOLD,
        evidence_message_ids=evidence,
    )


# ── 身份背景采集（K1 确定性，写入 UserProfile）──────────────────────────
# 11 个心理学常见背景维度（生物-心理-社会模型 + 5P 模型）。
# 敏感维度（创伤史/物质使用/家族史）只在用户主动提及时采集，不主动追问。

_BACKGROUND_OCCUPATION = re.compile(
    r"(?:我是|我在|我做|我搞|我从事|我在.{0,4}(?:上班|工作|读书|上学))"
    r"(.{1,20}?)(?:的|，|。|$)"
)
_BACKGROUND_FAMILY = re.compile(
    r"(?:我有.{0,2}(?:个)?(?:孩子|小孩|女儿|儿子|宝宝)|"
    r"我(?:结婚了|离婚了|单身|有对象|有男朋友|有女朋友|已婚)|"
    r"我(?:爸|妈|父母|家人)(?:身体|年纪))"
)
_BACKGROUND_AGE = re.compile(r"(?:我今年|我|我.{0,2})(\d{2,3})(?:岁|了)")
_BACKGROUND_LIVING = re.compile(
    r"(?:我(?:一个人住|独居|和.{0,6}住|住在|搬到了|住在宿舍|住校)|"
    r"(?:租房|自己住|跟父母住|和朋友合租))"
)
_BACKGROUND_MEDICAL = re.compile(
    r"(?:我(?:有|在吃|在服用|得了|确诊|查出来)|医生说(?:我)?)(?:.{0,4})"
    r"(?:抑郁症|焦虑症|双相|失眠|甲[状]?腺|高血压|糖尿病|慢性|吃药|服药|药物|"
    r"安眠药|抗抑郁|抗焦虑|SSRI|百忧解|舍曲林|文拉法辛|阿普唑仑)"
)
_BACKGROUND_CULTURAL = re.compile(r"(?:我是|我家是|我来自)(?:.{0,6})(?:人|地方|农村|城市|少数民族|海外|移民|留学生)")
_BACKGROUND_RELIGION = re.compile(
    r"(?:我(?:信|相信|是|信仰)(?:佛|基督|天主|伊斯兰|道|神|上帝|菩萨|因果)|"
    r"(?:宗教|信仰|灵性)(?:对我|方面))"
)
_BACKGROUND_SUPPORT_NETWORK = re.compile(
    r"(?:我(?:没什么|没有|很少有|有很多|有几个|身边有)(?:朋友|可以说话的人|可以倾诉的人|支持我的人)|"
    r"(?:孤单|孤独|没人理解|没人关心|有人陪|不缺陪伴))"
)
_BACKGROUND_TRAUMA = re.compile(
    r"(?:我(?:经历过|遭遇过|小时候|曾经被|曾经发生过)(?:.{0,10})(?:虐待|侵犯|暴力|事故|失去|创伤|霸凌|欺凌)|"
    r"(?:PTSD|创伤后|心理阴影))"
)
_BACKGROUND_FAMILY_HISTORY = re.compile(
    r"(?:我(?:家(?:里|族)|爸|妈|亲戚)(?:.{0,6})(?:有|得过|也有)(?:.{0,6})(?:抑郁|焦虑|精神|心理|双相|自杀)|"
    r"(?:家族|遗传|家里人)(?:.{0,4})(?:病史|问题))"
)
_BACKGROUND_SUBSTANCE = re.compile(
    r"(?:我(?:喝酒|抽烟|吸|嗑|用药|依赖)(?:.{0,10})|"
    r"(?:酒精|大麻|安非他命|药物依赖|成瘾|戒断))"
)
_BACKGROUND_CONCERN = re.compile(r"(?:主要(?:想解决|困扰|困扰我的|让我难受的))(?:是|就是)?(.{2,30}?)(?:，|。|$)")
_BACKGROUND_SUPPORT_PREF = re.compile(
    r"(?:我(?:更)?喜欢|我希望你|别(?:问|跟我说)|少问|多听|简短|深入|不要(?:问|说))(?:.{0,15}?)(?:，|。|$)"
)
# 目标检测（D5 goal.* 命名空间用，不写入 UserProfile）。
_BACKGROUND_GOAL = re.compile(r"(?:我想|我希望|我的目标|我希望(?:能|可以))(.{2,30}?)(?:，|。|$)")


def _extract_background_info(text: str, *, sensitive_background_enabled: bool = False) -> dict[str, str]:
    """K1 身份背景检测。

    只从用户主动提供的信息中提取，不主动追问敏感话题。
    敏感字段（医疗/创伤/家族史）仅在 sensitive_background_enabled=True 时提取。
    宗教/信仰始终以代号存储，不保存具体内容。
    """
    result: dict[str, str] = {}
    if not text or len(text) < 4:
        return result

    # 职业
    for m in _BACKGROUND_OCCUPATION.finditer(text):
        val = m.group(1).strip()
        if val:
            result["occupation"] = val
            break
    # 年龄
    for m in _BACKGROUND_AGE.finditer(text):
        age = int(m.group(1))
        if 10 <= age <= 120:
            result["age"] = str(age)
            break
    # 家庭/关系
    if _BACKGROUND_FAMILY.search(text):
        result["family"] = _BACKGROUND_FAMILY.search(text).group(0)
    # 居住状况
    if _BACKGROUND_LIVING.search(text):
        result["living"] = _BACKGROUND_LIVING.search(text).group(0)[:30]
    # 文化背景
    if _BACKGROUND_CULTURAL.search(text):
        result["cultural"] = _BACKGROUND_CULTURAL.search(text).group(0)[:30]
    # 社会支持
    if _BACKGROUND_SUPPORT_NETWORK.search(text):
        result["support_network"] = _BACKGROUND_SUPPORT_NETWORK.search(text).group(0)[:30]
    # 宗教/信仰：检测到即存为代号，不保存具体内容，前端不展示。
    if _BACKGROUND_RELIGION.search(text):
        result["religion"] = "faith"
    # 敏感背景（需用户授权）：医疗/创伤/家族史。
    if sensitive_background_enabled:
        if _BACKGROUND_MEDICAL.search(text):
            result["medical"] = _BACKGROUND_MEDICAL.search(text).group(0)[:30]
        if _BACKGROUND_TRAUMA.search(text):
            result["trauma"] = "noted"  # 不存具体内容，只标记存在
        if _BACKGROUND_FAMILY_HISTORY.search(text):
            result["family_history"] = "noted"
    # 关切
    for m in _BACKGROUND_CONCERN.finditer(text):
        val = m.group(1).strip()
        if val:
            result["concern"] = val
            break
    # 支持偏好
    for m in _BACKGROUND_SUPPORT_PREF.finditer(text):
        result["support_pref"] = m.group(0).strip()
        break
    return result


def _apply_background_to_profile(
    session: Session,
    user_id: str,
    background: dict[str, str],
) -> None:
    """将提取到的背景信息写入 UserProfile（追加模式，不覆盖已有内容）。"""
    if not background:
        return
    from psych_support_bot.infra.db.repositories import get_user_profile, upsert_user_profile

    existing = get_user_profile(session, user_id)
    concerns = existing.primary_concerns if existing else ""
    prefs = existing.support_preferences if existing else ""

    if "concern" in background and background["concern"] not in concerns:
        concerns = f"{concerns}；{background['concern']}" if concerns else background["concern"]
    if "support_pref" in background and background["support_pref"] not in prefs:
        prefs = f"{prefs}；{background['support_pref']}" if prefs else background["support_pref"]

    # 结构化背景写入 background_json（11 维度）。
    import json as _json

    bg_json = _json.loads(existing.background_json if existing and existing.background_json else "{}")
    changed = False
    for key in (
        "occupation",
        "age",
        "family",
        "living",
        "medical",
        "cultural",
        "religion",
        "support_network",
        "trauma",
        "family_history",
        "substance",
    ):
        if key in background and not bg_json.get(key):
            bg_json[key] = background[key]
            changed = True

    if (
        concerns != (existing.primary_concerns if existing else "")
        or prefs != (existing.support_preferences if existing else "")
        or changed
    ):
        upsert_user_profile(
            session,
            user_id,
            display_name=existing.display_name if existing else "",
            primary_concerns=concerns,
            goals=existing.goals if existing else "",
            support_preferences=prefs,
            risk_notes=existing.risk_notes if existing else "",
            background_json=_json.dumps(bg_json, ensure_ascii=False),
        )


def run_turn_extraction(
    session: Session,
    *,
    user_id: str,
    session_id: str,
    topics: list[str],
    risk_level: str,
    exercise_tag: str | None,
    valence_text: str,
    turn_count: int = 0,
    slice_id: str = "",
) -> None:
    """每轮收尾提取入口（_finalize 挂载点）。fail-open，绝不抛出。

    顺序：确定性提取（K1，零成本）→ LLM 语义提取（K2，节流触发、
    危机轮跳过）。两层各自独立提交、独立统计，互不阻断。
    """
    if not get_settings().profile_extraction_enabled or not is_profile_memory_enabled(session, user_id):
        return
    # 画像 Beta 知悉协议未确认时，不进行画像提取。
    from psych_support_bot.infra.db.profile_repositories import is_profile_beta_accepted

    if not is_profile_beta_accepted(session, user_id):
        return
    crisis = risk_level in _CRISIS_LEVELS
    trigger = "crisis_guard" if crisis else ("practice_event" if exercise_tag else "topic_flow")
    try:
        stats = record_extraction_stats(
            session,
            user_id,
            session_id=session_id,
            trigger=trigger,
            model="deterministic/k1",
        )
        claims = build_turn_claims(
            topics=topics,
            risk_level=risk_level,
            exercise_tag=exercise_tag,
            valence_text=valence_text,
        )
        message_id = _latest_user_message_id(session, session_id)
        ctx_tags = _detect_context_tags(valence_text)
        for claim in claims:
            belief, _event = record_claim(
                session,
                user_id,
                dimension=claim.dimension,
                key=claim.key,
                claim_text=claim.claim_text,
                value=claim.value,
                relation=claim.relation,
                confidence=claim.confidence,
                session_id=session_id,
                evidence_message_ids=claim.evidence_message_ids or ([message_id] if message_id else []),
                context_tags=ctx_tags or None,
                stats_id=stats.id,
                origin_slice_id=(slice_id or None),
            )
            # 效果值更新：neutral→worked/aversive 的转变是 D4 的学习信号；
            # belief 行的 value_json 不随 support 自动覆盖，需显式更新。
            if belief is not None and claim.dimension == "D4" and claim.value.get("effect"):
                update_belief_value(
                    session,
                    user_id,
                    claim.key,
                    claim.value,
                    stats_id=stats.id,
                    evidence=[message_id] if message_id else [],
                )
        stats.claims_out = len(claims)

        # 身份背景采集：检测用户主动提供的身份/处境/偏好/目标信息，
        # 写入 UserProfile（已在 build_memory_snapshot 中被读取渲染）。
        if not crisis and not exercise_tag:
            try:
                from psych_support_bot.infra.db.profile_repositories import is_sensitive_background_enabled

                sensitive_ok = is_sensitive_background_enabled(session, user_id)
                bg = _extract_background_info(valence_text, sensitive_background_enabled=sensitive_ok)
                _apply_background_to_profile(session, user_id, bg)
            except Exception:  # noqa: BLE001 — background extraction must not block
                pass

        session.commit()
    except Exception:  # noqa: BLE001 - profile extraction must not block the response
        logger.warning("Profile extraction failed; skipping turn")
        session.rollback()
        try:
            # 失败也要留 P2 统计痕迹（error 行），但统计写入自身失败则放弃。
            record_extraction_stats(
                session,
                user_id,
                session_id=session_id,
                trigger=trigger,
                model="deterministic/k1",
                status="error",
                error="extraction_failed",
            )
            session.commit()
        except Exception:  # noqa: BLE001 — fail-open 的兜底自身也必须兜住
            session.rollback()

    # 回半环（K2 收尾）：上一轮若质询过且本轮是首次回应，先判定应答并
    # 驱动 confirm/reject；质询判定占用本轮时跳过常规语义提取（该轮的
    # 信号属于"对假设的回应"，不是新主题）。
    # 注：用户确认/否决保持同步（立即生效），K2 语义提取异步化（Worker）。
    verification_handled = run_verification_judgment(
        session,
        user_id=user_id,
        session_id=session_id,
        user_text=valence_text,
    )

    # K2 LLM 语义提取：质询判定占用本轮时不执行（该轮信号属于
    # "对假设的回应"而非新主题）。自身已有 _should_llm_extract 节流，
    # 实际每 session 仅触发 1-3 次 LLM 调用。
    if not verification_handled:
        run_semantic_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            user_text=valence_text,
            turn_count=turn_count,
            risk_level=risk_level,
            practice_event=bool(exercise_tag),
            message_id=_latest_user_message_id(session, session_id),
        )


def record_turn_interventions(
    session: Session,
    *,
    user_id: str,
    session_id: str,
    practice_action: str,
    exercise_tag: str | None,
    question_candidates: list[str],
    no_question_mode: bool,
    mode: str,
    risk_level: str,
) -> None:
    """K2c 干预→反应事件：只记动作元数据，结果由事件序列派生。fail-open。"""
    from psych_support_bot.infra.db.profile_repositories import (
        has_unanswered_injection,
        record_intervention_event,
    )

    if not is_profile_memory_enabled(session, user_id):
        return

    if practice_action in {"offer", "start", "complete"}:
        record_intervention_event(
            session,
            user_id,
            session_id=session_id,
            kind=f"practice_{practice_action}",
            detail={"tag": exercise_tag or "panic_grounding_5_4_3_2_1"},
        )
    if question_candidates and not no_question_mode and mode != "crisis" and risk_level not in _CRISIS_LEVELS:
        # 上一条注入尚未被判定时不重复注入：否则当前轮的新注入（晚于本轮
        # 用户消息）会遮蔽上一轮，回半环（"注入后恰好一条用户消息"）永远
        # 无法命中——线上 V1/V2 实测定位到的就是这个问题。
        if has_unanswered_injection(session, user_id):
            logger.info("Question injection skipped: previous injection still unanswered.")
        else:
            # 标签是系统生成的友善措辞，不是对话内容（UsageEvent 伦理边界同源）。
            record_intervention_event(
                session,
                user_id,
                session_id=session_id,
                kind="question_injected",
                detail={"belief_label": question_candidates[0]},
            )
    session.commit()
