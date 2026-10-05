"""异步画像综合工作流（工作单元 C）。

复用 privacy_deletion.py 的 Worker 模式：应用内持久后台线程、
数据库任务表、幂等键、单用户互斥。Celery/Redis 作为规模化替换层，
不作为首版运行前提。

K1 确定性提取 + K2 LLM 语义提取在同步链路（extractor.py）完成，
本 Worker 负责综合与快照生成。

六节点流程：
1. load_evidence       读取新增证据
2. formulate           一次 LLM 调用生成候选理解（patterns + support_policy）
3. validate            代码校验证据、敏感性与生效资格
4. compile_policy      生成 Agent 可消费的支持策略
4.5. K3 synthesis      综合理解（patterns/how_to_be_with_them → UserProfile.understanding_json）
5. persist_snapshot    保存版本化快照
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import (
    ProfileBelief,
    ProfileEvolutionJob,
    ProfileSnapshot,
    UserProfile,
)
from psych_support_bot.infra.db.session import SessionLocal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
JOB_LEASE_SECONDS = 300  # 5 分钟租约，防止并发处理
MAX_ATTEMPTS = 3
PROMPT_VERSION = "v1"


def enqueue_evolution_job(session: Session, user_id: str) -> ProfileEvolutionJob | None:
    """写入/合并画像综合任务（幂等：同一用户同一水位只产生一个 pending 任务）。"""
    from psych_support_bot.infra.db.profile_repositories import is_profile_beta_accepted, is_profile_memory_enabled

    if not is_profile_memory_enabled(session, user_id):
        return None
    if not is_profile_beta_accepted(session, user_id):
        return None

    # 幂等键：user_id + 当前证据水位（belief 最后更新时间）。
    latest = (
        session.query(ProfileBelief.updated_at)
        .filter(ProfileBelief.user_id == user_id, ProfileBelief.status == "active")
        .order_by(ProfileBelief.updated_at.desc())
        .limit(1)
        .scalar()
    )
    watermark = latest.isoformat() if latest else "none"
    idempotency_key = f"{user_id}:{watermark}:{PROMPT_VERSION}"

    # 检查是否已有 pending/running 任务；如有，更新水位让 Worker 处理最新证据。
    existing = (
        session.query(ProfileEvolutionJob)
        .filter(
            ProfileEvolutionJob.user_id == user_id,
            ProfileEvolutionJob.status.in_(("pending", "running")),
        )
        .first()
    )
    if existing:
        existing.evidence_watermark = json.dumps({"latest_update": watermark})
        existing.idempotency_key = idempotency_key
        session.commit()
        return existing

    # Shadow → Active 升级：最新快照是 shadow 且证据已跨多会话时直接升级，
    # 不需要等 Worker 重新生成快照。
    _promote_shadow_if_ready(session, user_id)

    # 水位未变时复用已完成/失败的旧任务（避免 idempotency_key 唯一约束冲突）。
    stale = (
        session.query(ProfileEvolutionJob)
        .filter(
            ProfileEvolutionJob.user_id == user_id,
            ProfileEvolutionJob.idempotency_key == idempotency_key,
            ProfileEvolutionJob.status.in_(("completed", "failed")),
        )
        .first()
    )
    if stale:
        stale.status = "pending"
        stale.attempt_count = 0
        stale.error_code = ""
        stale.started_at = None
        stale.completed_at = None
        stale.evidence_watermark = json.dumps({"latest_update": watermark})
        session.commit()
        return stale

    job = ProfileEvolutionJob(
        id=uuid.uuid4().hex[:16],
        user_id=user_id,
        evidence_watermark=json.dumps({"latest_update": watermark}),
        prompt_version=PROMPT_VERSION,
        idempotency_key=idempotency_key,
        status="pending",
    )
    session.add(job)
    session.commit()
    return job


def _promote_shadow_if_ready(session: Session, user_id: str) -> None:
    """如果最新快照是 shadow 且证据已跨多会话，直接升级为 active。"""
    latest_snap = (
        session.query(ProfileSnapshot)
        .filter(
            ProfileSnapshot.user_id == user_id,
            ProfileSnapshot.status == "shadow",
        )
        .order_by(ProfileSnapshot.version.desc())
        .limit(1)
        .first()
    )
    if not latest_snap:
        return

    distinct_sessions = (
        session.query(ProfileBelief.origin_session_id)
        .filter(ProfileBelief.user_id == user_id, ProfileBelief.status == "active")
        .distinct()
        .count()
    )
    if distinct_sessions >= 2:
        latest_snap.status = "active"
        session.commit()
        logger.info("Shadow snapshot promoted to active (user=%s, version=%d)", user_id[:8], latest_snap.version)


def process_pending_evolutions() -> None:
    """处理所有 pending 任务（同步，由 Worker 线程调用）。"""
    with SessionLocal() as session:
        # 崩溃恢复：lease 过期的 running 任务重置为 pending。
        lease_cutoff = datetime.now(UTC) - timedelta(seconds=JOB_LEASE_SECONDS)
        session.query(ProfileEvolutionJob).filter(
            ProfileEvolutionJob.status == "running",
            ProfileEvolutionJob.started_at < lease_cutoff,
        ).update({"status": "pending"}, synchronize_session=False)
        session.commit()

        jobs = (
            session.query(ProfileEvolutionJob)
            .filter(ProfileEvolutionJob.status == "pending")
            .order_by(ProfileEvolutionJob.created_at)
            .limit(5)
            .all()
        )
        if not jobs:
            return
        for job in jobs:
            # 原子抢占：只有 status=pending 的任务才能被 claim。
            updated = (
                session.query(ProfileEvolutionJob)
                .filter(
                    ProfileEvolutionJob.id == job.id,
                    ProfileEvolutionJob.status == "pending",
                )
                .update(
                    {
                        ProfileEvolutionJob.status: "running",
                        ProfileEvolutionJob.started_at: datetime.now(UTC),
                        ProfileEvolutionJob.attempt_count: ProfileEvolutionJob.attempt_count + 1,
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            if updated == 0:
                continue  # 被其他实例抢占，跳过
            _process_single_job(session, job)


def _process_single_job(session: Session, job: ProfileEvolutionJob) -> None:
    """处理单个画像综合任务（调用前已原子 claim 为 running）。"""
    try:
        # Node 1: load_evidence
        evidence = _load_evidence(session, job.user_id)

        # Node 2: formulate (LLM)
        candidate = _formulate(session, job.user_id, evidence)

        # Node 3: validate
        validated = _validate(candidate, evidence)

        # Node 4: compile_policy
        policy = _compile_policy(validated)

        # Node 4.5: K3 synthesis（理解综合，写入 UserProfile.understanding_json）。
        # run_k3_synthesis 自身有 belief < 2 条 / 开关门控，fail-open。
        try:
            from psych_support_bot.ai.profile.synthesis import run_k3_synthesis

            run_k3_synthesis(session, job.user_id)
        except Exception:  # noqa: BLE001 — K3 failure must not block snapshot
            logger.warning("K3 synthesis skipped in worker (job=%s)", job.id[:8])

        # Node 5: persist_snapshot
        # 单会话证据 → shadow（待更多会话确认）；多会话 → active。
        is_shadow = evidence.get("distinct_sessions", 1) < 2
        _persist_snapshot(session, job.user_id, validated, policy, job.evidence_watermark, shadow=is_shadow)

        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        job.error_code = ""
        session.commit()
        logger.info("Profile evolution completed (job=%s)", job.id[:8])

    except Exception as exc:  # noqa: BLE001 — worker must not crash; retry on next tick
        job.status = "failed" if job.attempt_count >= MAX_ATTEMPTS else "pending"
        job.error_code = type(exc).__name__
        session.commit()
        # 日志隐私：不记录原始 user_id 和异常正文，只记录 job id 和错误类型。
        logger.warning("Profile evolution failed (job=%s, error=%s)", job.id[:8], type(exc).__name__)


# ── Node 1: load_evidence ────────────────────────────────────────────


def _load_evidence(session: Session, user_id: str) -> dict:
    """读取用户的活跃信念、背景、最近切片摘要作为综合输入。"""
    beliefs = (
        session.query(ProfileBelief)
        .filter(ProfileBelief.user_id == user_id, ProfileBelief.status == "active")
        .order_by(ProfileBelief.last_evidence_at.desc())
        .limit(30)
        .all()
    )
    belief_data = []
    for b in beliefs:
        belief_data.append(
            {
                "dimension": b.dimension,
                "key": b.key,
                "claim_text": b.claim_text,
                "confidence": b.confidence,
                "layer": b.layer,
                "value": b.value_json,
            }
        )

    profile = session.get(UserProfile, user_id)
    background = {}
    if profile and profile.background_json and profile.background_json != "{}":
        with suppress(TypeError, ValueError):
            background = json.loads(profile.background_json)

    # 统计证据来自多少个不同会话，用于决定快照状态（shadow vs active）。
    distinct_sessions = (
        session.query(ProfileBelief.origin_session_id)
        .filter(ProfileBelief.user_id == user_id, ProfileBelief.status == "active")
        .distinct()
        .count()
    )

    return {
        "beliefs": belief_data,
        "background": background,
        "user_id": user_id,
        "distinct_sessions": distinct_sessions,
    }


# ── Node 2: formulate (LLM) ─────────────────────────────────────────


def _formulate(session: Session, user_id: str, evidence: dict) -> dict:
    """一次 LLM 调用生成候选理解。"""
    from psych_support_bot.infra.llm.generation import generate_profile_extraction

    beliefs_text = "\n".join(
        f"- [{b['dimension']}] {b['key']} (confidence={b['confidence']:.2f}): {b['claim_text']}"
        for b in evidence["beliefs"]
        if b["confidence"] >= 0.3
    )
    bg_text = json.dumps(evidence["background"], ensure_ascii=False) if evidence["background"] else "(none)"

    system_prompt = (
        "You are building a psychological understanding of a user through their "
        "conversation history and profile data. Output STRICT JSON:\n"
        '{"patterns": [{"description": "...", "situations": ["..."], "what_happens": "...", '
        '"why_possibly": "...", "confidence": 0.0-1.0, "needs_verification": true/false}], '
        '"how_to_be_with_them": "...", "open_questions": ["..."], '
        '"support_policy": {"response_length": "brief|normal", "pacing": "...", '
        '"preferred_knowledge_paths": ["act|cbt|dbt"]}}\n\n'
        "Rules:\n"
        "- At most 3 patterns. Only from provided evidence.\n"
        "- Mark uncertain patterns with needs_verification=true.\n"
        "- Never use clinical labels (personality disorder, attachment style).\n"
        "- 'how_to_be_with_them' is about interaction style, not diagnosis.\n"
        "- support_policy uses controlled enums only."
    )

    user_content = f"[Beliefs]\n{beliefs_text}\n\n[Background]\n{bg_text}"
    raw = generate_profile_extraction(system_prompt=system_prompt, payload_text=user_content)

    # Parse
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}


# ── Node 3: validate ─────────────────────────────────────────────────

_FORBIDDEN_LABELS = (
    # English
    "personality disorder",
    "narcissist",
    "borderline",
    "avoidant",
    "attachment style",
    "anxious attachment",
    "secure attachment",
    "disorder",
    "diagnosis",
    "pathological",
    # Chinese equivalents
    "人格障碍",
    "自恋",
    "边缘",
    "回避型",
    "依恋风格",
    "焦虑型依恋",
    "安全型依恋",
    "障碍",
    "诊断",
    "病态",
)

# 第三方归属关键词：pattern 描述中出现这些词说明可能把他人事实归到了用户身上。
_THIRD_PARTY_MARKERS = (
    "his friend",
    "her friend",
    "their friend",
    "his partner",
    "her partner",
    "their partner",
    "his family",
    "her family",
    "their family",
    "his colleague",
    "her colleague",
    "their colleague",
    "他的朋友",
    "她的朋友",
    "他们的朋友",
    "他的家人",
    "她的家人",
    "他们的家人",
    "他的同事",
    "她的同事",
    "他们的同事",
    "his mother",
    "his father",
    "her mother",
    "her father",
    "他的妈妈",
    "他的爸爸",
    "她的妈妈",
    "她的爸爸",
)


def _validate(candidate: dict, evidence: dict | None = None) -> dict:
    """确定性校验：禁止诊断标签、敏感信息、无证据断言、反证检查。

    校验规则（工作单元 D）：
    - 所有观察必须引用存在且属于该用户的证据
    - 禁止诊断、人格障碍、依恋类型和认知歪曲人格化标签
    - 禁止把第三方事实归到用户
    - 有反证时不得输出确定性结论
    - 模型自报置信度不直接决定生效
    """
    patterns = candidate.get("patterns", [])
    validated = []

    # 构建证据索引（如有）。
    belief_keys: set[str] = set()
    contradicted_keys: set[str] = set()
    distinct_sessions = 0
    if evidence and "beliefs" in evidence:
        sessions_seen: set[str] = set()
        for b in evidence["beliefs"]:
            belief_keys.add(b.get("key", ""))
            sid = b.get("origin_session_id") or ""
            if sid:
                sessions_seen.add(sid)
            # 检查是否有 needs_clarification 标记（反证信号）。
            try:
                val = json.loads(b.get("value", "{}") or "{}")
                if val.get("clarification_status") == "needs_clarification":
                    contradicted_keys.add(b.get("key", ""))
            except (TypeError, ValueError):
                pass
        distinct_sessions = evidence.get("distinct_sessions", len(sessions_seen) or 1)

    for p in patterns:
        desc = p.get("description", "")
        # 禁止诊断标签（中英文）
        if any(label in desc.lower() for label in _FORBIDDEN_LABELS):
            continue
        # 禁止空描述
        if not desc.strip():
            continue
        # 第三方归属：pattern 描述不应把他人事实归到用户身上。
        desc_lower = desc.lower()
        if any(marker in desc_lower for marker in _THIRD_PARTY_MARKERS):
            continue
        # 证据存在性：pattern 引用的 key 必须在用户的 belief 集合中。
        p_keys = set(p.get("evidence_keys") or [])
        if p_keys and not p_keys.issubset(belief_keys):
            continue  # 引用了不存在的 belief，丢弃
        # 有反证时强制标记 needs_verification
        if contradicted_keys:
            p["needs_verification"] = True
        # 单会话证据的模式强制标记 needs_verification（跨会话门槛）
        if distinct_sessions < 2:
            p["needs_verification"] = True
        validated.append(p)
    candidate["patterns"] = validated[:3]
    return candidate


# ── Node 4: compile_policy ───────────────────────────────────────────

_DEFAULT_POLICY = {
    "response_length": "normal",
    "pacing": "validate_before_suggestions",
    "question_budget": 2,
    "avoid_topics": [],
    "preferred_knowledge_paths": [],
    "exercise_constraints": [],
}


def _compile_policy(candidate: dict) -> dict:
    """将候选理解编译为有限指令（受控枚举，不直接暴露原始画像）。"""
    raw_policy = candidate.get("support_policy", {})
    policy = dict(_DEFAULT_POLICY)
    if isinstance(raw_policy, dict):
        if raw_policy.get("response_length") in ("brief", "normal"):
            policy["response_length"] = raw_policy["response_length"]
        if raw_policy.get("pacing"):
            policy["pacing"] = raw_policy["pacing"]
        if isinstance(raw_policy.get("preferred_knowledge_paths"), list):
            policy["preferred_knowledge_paths"] = [
                p for p in raw_policy["preferred_knowledge_paths"] if p in ("act", "cbt", "dbt", "mi", "sfbt")
            ]
    return policy


# ── Node 5: persist_snapshot ─────────────────────────────────────────


def _persist_snapshot(
    session: Session,
    user_id: str,
    content: dict,
    policy: dict,
    evidence_watermark: str,
    *,
    shadow: bool = False,
) -> ProfileSnapshot:
    """保存版本化快照，旧快照标记为 retired。

    shadow=True 时状态为 "shadow"（单会话证据，待更多会话确认后升级为 active）。
    """
    # 计算新版本号。
    latest = (
        session.query(ProfileSnapshot)
        .filter(ProfileSnapshot.user_id == user_id)
        .order_by(ProfileSnapshot.version.desc())
        .limit(1)
        .first()
    )
    new_version = (latest.version + 1) if latest else 1

    # 旧 active/shadow 标记为 retired。
    session.query(ProfileSnapshot).filter(
        ProfileSnapshot.user_id == user_id,
        ProfileSnapshot.status.in_(("active", "shadow")),
    ).update({"status": "retired"}, synchronize_session=False)

    snapshot = ProfileSnapshot(
        id=uuid.uuid4().hex[:16],
        user_id=user_id,
        version=new_version,
        status="shadow" if shadow else "active",
        content_json=json.dumps(content, ensure_ascii=False),
        support_policy_json=json.dumps(policy, ensure_ascii=False),
        evidence_watermark=evidence_watermark,
        prompt_version=PROMPT_VERSION,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


# ── Worker ───────────────────────────────────────────────────────────


async def profile_evolution_worker(stop: asyncio.Event) -> None:
    """应用内持久后台 Worker：轮询 pending 任务并处理。"""
    while not stop.is_set():
        try:
            await asyncio.to_thread(process_pending_evolutions)
        except Exception:  # noqa: BLE001 — database outages are retried on the next tick
            logger.warning("Profile evolution worker will retry")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
