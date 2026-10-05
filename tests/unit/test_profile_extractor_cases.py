"""K1d：eval fixture（27 case）→ 画像提取器回归座。

把 tests/evals/profile_extraction_cases.json 变成提取器的可持续回归，
三层判定（随 K2 语义提取落地，pending 逐案转正为 strict）：

1. 结构性不变量（全部 27 case，无条件）：无 distortion key、D1 key 必在
   主题闭集、单轮 claims ≤3、forbidden 内容零出现。
2. strict（K1 确定性可判，期望 claim 严格相等）：关键词可检 D1、全部 D4、
   结构性负例（危机/弱信号/持续话/体重红线/歪曲/求安心量表语境）。
3. pending（K2 语义提取范围）：纯语义 D1、D2/D3/D5、混合——只断言
   "产出 key ⊆ expected key"（不误报）；漏检记 pending 计数。
   PENDING_IDS 钉住 pending 名单：K2 落地一档就必须来这里转正，
   防止覆盖静默缩水。已知超报（OVERSHOOT_IDS：知识转述、控制挣扎对象）
   只做结构断言并在报告中计数——它们是 §5 消歧规则的语义边界教科书。

第二部分：渲染占用率基线——全部 case 经 K1 提取播种后渲染画像块，
采集字符分布（预算 eval 的需求侧基线数据）。
"""

import json
from pathlib import Path

from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS, detect_topics
from psych_support_bot.ai.profile.extractor import build_turn_claims
from psych_support_bot.ai.profile.renderer import render_profile_block
from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.tools.exercises import detect_completed_exercise
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.profile_repositories import record_claim
from psych_support_bot.infra.db.session import SessionLocal

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "evals" / "profile_extraction_cases.json"

# K2 待转正名单（case_id）。语义提取落地一档，就把对应 case 挪进 strict
# 断言并从本名单删除——名单缩短是 K2 的验收进度条。
PENDING_IDS: frozenset[str] = frozenset(
    {
        # 纯语义 D1（关键词表零命中或非主题陈述，考察语义理解）
        "d1_sleep_semantic_zh",
        "d1_grief_semantic_zh",
        "d1_burnout_semantic_zh",
        # D2 严重度信号（文本→条目簇映射，K2）
        "d2_worry_uncontrollable_zh",
        "d2_sleep_onset_zh",
        "d2_sleep_maintenance_en",
        "d2_sleep_early_waking_zh",
        # D3 机制（词锚召回 + LLM 判定，K2）
        "d3_cognitive_fusion_en",
        "d3_avoidance_social_zh",
        # D5 动机（change talk 词锚召回 + 判定，K2）
        "d5_change_talk_desire_zh",
        "d5_change_talk_commitment_zh",
        "d5_taking_steps_en",
        "d5_waiting_for_readiness_zh",
        "mixed_anxiety_desire_en",
    }
)

# 已知超报（产出 ⊄ expected，只做结构断言并在报告中计数）：
# - knowledge_recount：知识转述中的临床词（失眠/焦虑/抑郁）被关键词表命中——
#   "科普语境豁免"需要语义理解（§5 消歧规则的语义边界教科书）；
# - control_struggle：挣扎对象（"把焦虑赶走"）被当作当期话题（mixed case 同规）；
# - sustain_talk：指向方法的"没用"被 depression 词表命中（指向方法非自我评价）。
OVERSHOOT_IDS: frozenset[str] = frozenset(
    {
        "neg_knowledge_recount_zh",
        "d3_control_struggle_zh",
        "neg_sustain_talk_zh",
    }
)


def _load_cases() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [case for case in data["cases"] if not case.get("_meta")]


def _case_user_text(case: dict) -> str:
    return "\n".join(turn["content"] for turn in case["turns"] if turn["role"] == "user")


def _k1_extract(case: dict) -> list:
    """按生产链路的确定性部分组装 K1 claims（风险 → 主题 → 练习完成）。"""
    text = _case_user_text(case)
    risk_level = classify_message_risk(text).risk_level
    topics = detect_topics(text) if risk_level not in {"high", "critical"} else []
    exercise_tag = detect_completed_exercise(text)
    return build_turn_claims(
        topics=topics,
        risk_level=risk_level,
        exercise_tag=exercise_tag,
        valence_text=text,
    )


# --- 1. 结构性不变量（全部 case） ---


def test_structural_invariants_hold_for_every_case() -> None:
    for case in _load_cases():
        claims = _k1_extract(case)
        assert len(claims) <= 3, f"{case['case_id']}: 单轮 claims 超上限"
        for claim in claims:
            assert not claim.key.startswith("distortion."), f"{case['case_id']}: distortion key 泄漏"
            if claim.dimension == "D1":
                assert claim.key in TOPIC_KEYWORDS, f"{case['case_id']}: D1 key {claim.key!r} 不在主题闭集"


# --- 2. strict：K1 确定性可判 ---


def test_strict_cases_exact_match() -> None:
    strict_ids = {
        # 关键词可检 D1
        "d1_anxiety_general_en",
        "d1_relationships_conflict_zh",
        # D4（完成短语 + 效果词，K1 确定性主战场）
        "d4_tipp_breathing_worked_zh",
        "d4_thought_record_flat_zh",
        "d4_thought_record_averse_en",
        # 结构性负例与红线
        "neg_crisis_zero_storage_zh",
        "neg_weak_signal_zh",
        "neg_weight_redline_zh",
        "redline_distortion_identify_only_zh",
        "redline_reassurance_retest_zh",
    }
    for case in _load_cases():
        if case["case_id"] not in strict_ids:
            continue
        expected_keys = {(c["dimension"], c["key"]) for c in case["expected_claims"]}
        produced = {(c.dimension, c.key) for c in _k1_extract(case)}
        assert produced == expected_keys, f"{case['case_id']}: 期望 {sorted(expected_keys)}，实际 {sorted(produced)}"


def test_negative_and_redline_cases_produce_zero_claims() -> None:
    for case in _load_cases():
        # 已知超报（OVERSHOOT_IDS）由 pending 名单测试与结构断言管辖
        if case["category"].startswith(("neg_", "redline_")) and case["case_id"] not in OVERSHOOT_IDS:
            claims = _k1_extract(case)
            assert claims == [], f"{case['case_id']}: 负例/红线 case 产出了 {[(c.dimension, c.key) for c in claims]}"


# --- 3. pending：K2 语义范围（只断言不误报超出 expected） ---


def test_pending_cases_never_produce_keys_outside_expected() -> None:
    for case in _load_cases():
        if case["case_id"] not in PENDING_IDS:
            continue
        expected_keys = {(c["dimension"], c["key"]) for c in case["expected_claims"]}
        produced = {(c.dimension, c.key) for c in _k1_extract(case)}
        overshoot = produced - expected_keys
        assert not overshoot or case["case_id"] in OVERSHOOT_IDS, (
            f"{case['case_id']}: pending case 超报 {sorted(overshoot)}（不在 OVERSHOOT_IDS 名单）"
        )


def test_pending_and_overshoot_registry_covers_exactly_the_gaps() -> None:
    """名单完整性：PENDING ∪ strict ∪ OVERSHOOT 必须 = 全集且互斥。

    K2 每转正一个 case，须同步把它从 PENDING_IDS 挪进 strict 断言——
    本测试保证名单与 fixture 永不失同步。
    """
    cases = _load_cases()
    all_ids = {case["case_id"] for case in cases}
    strict_ids = {
        "d1_anxiety_general_en",
        "d1_relationships_conflict_zh",
        "d4_tipp_breathing_worked_zh",
        "d4_thought_record_flat_zh",
        "d4_thought_record_averse_en",
        "neg_crisis_zero_storage_zh",
        "neg_weak_signal_zh",
        "neg_weight_redline_zh",
        "redline_distortion_identify_only_zh",
        "redline_reassurance_retest_zh",
    }
    assert not (PENDING_IDS & strict_ids), "pending 与 strict 名单重叠"
    assert not (PENDING_IDS & OVERSHOOT_IDS), "pending 与 overshoot 名单重叠"
    assert PENDING_IDS | strict_ids | OVERSHOOT_IDS == all_ids, "名单未覆盖全部 case（或多覆盖）"


# --- 4. 渲染占用率基线（预算 eval 的需求侧数据） ---


def test_render_utilization_baseline_over_fixture() -> None:
    """全部 case 播种 K1 信念后渲染，采集字符分布。

    断言只守上界（≤CAP、无残句）；分布数据打印供预算 eval 采样——
    K1 内容量下画像块的实际需求，是 PROFILE_RENDER_BASE 定值的输入之一。
    """
    cap = get_settings().profile_render_cap
    lengths: list[int] = []
    empty = 0
    for case in _load_cases():
        user_id = f"util-{case['case_id']}"
        text = _case_user_text(case)
        claims = _k1_extract(case)
        with SessionLocal() as session:
            for _round in range(2):  # 两轮同证据 → confidence 过 L4 渲染水位
                for claim in claims:
                    record_claim(
                        session,
                        user_id,
                        dimension=claim.dimension,
                        key=claim.key,
                        claim_text=claim.claim_text,
                        value=claim.value,
                        confidence=claim.confidence,
                    )
                session.commit()
            block = render_profile_block(
                session,
                user_id,
                language="zh",
                user_message=text,
                tail_load=900,
            )
        if block is None:
            empty += 1
            continue
        assert len(block) <= cap, f"{case['case_id']}: 渲染 {len(block)} 超过 CAP {cap}"
        assert "…" not in block, f"{case['case_id']}: 出现残句截断"
        lengths.append(len(block))

    assert lengths, "基线采集为空：fixture 未产出任何可渲染信念"
    report = (
        f"[预算基线] cases=27 渲染非空={len(lengths)} 空={empty} "
        f"chars min/mean/max = {min(lengths)}/{sum(lengths) // len(lengths)}/{max(lengths)}"
    )
    print(report)
