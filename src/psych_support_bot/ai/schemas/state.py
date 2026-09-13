from typing import Any, TypedDict

from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    GeneratedReply,
    RiskResult,
)


class GraphState(TypedDict):
    user_id: str
    session_id: str
    user_message: str
    memory_summary: str
    # 情绪扫描专用通道：用户原话（近 3 个会话）+ 会话摘要，不含记录层
    # 渲染文本与 profile——"失眠严重程度量表"等临床词汇不能被
    # _detect_cross_turn_contradiction / _has_previous_elevated 误读为
    # 用户情绪；summary 里的 risk=elevated 标记由此进入升级判定。
    user_history_text: str
    # 结构化风险通道：窗口期内最近一次 high/critical RiskEvent 等级
    # （RiskEvent 表，7 天窗口，""=无）。跨轮升级判定的主来源。
    recent_risk_level: str
    knowledge_context: str
    mode: ConversationMode
    risk_result: RiskResult
    generated_reply: GeneratedReply
    session_summary: str
    topics: list[str]
    fallback_used: bool
    consultation_required: bool
    consultation_agents: list[str]
    consultation_notes: str
    consultation_opinions: list[dict[str, str]]
    interview_stage: str
    question_strategy: str
    challenge_allowed: bool
    loop_hint: str
    # B3.1: Exercise history and refusal history for personalized recommendations
    exercise_history: list[str]
    refusal_history: list[str]
    # Language determined from conversation history to keep language consistent
    # even when the current message is language-neutral (e.g. pure numbers).
    expected_language: str
    # Number of prior messages in this session; drives stage-floor escalation.
    turn_count: int
    # Minimum risk level enforced by the classifier ("elevated"/"high"/"" );
    # derived from recent screening results that flagged safety follow-up.
    safety_floor_risk_level: str
    # Disengagement preference: user asked NOT to be questioned this turn
    # ("我只想安静待一会儿"). Safe paths (crisis/high risk) ignore this.
    no_question_mode: bool
    # Most recent bot reply in this session ("" when none). The response
    # generator uses it to avoid serving a verbatim-identical reply twice.
    last_bot_reply: str
    # M2 首答延迟优化：risk_classifier 在规则判 low/elevated 且 support 模式时
    # 与风险 LLM 分类并行投机生成的回复（带 elevated 加温备注）。最终风险
    # 裁决仍 ≤ elevated 时 response_generator 直接采用，跳过自己的 LLM 调用；
    # 升级 high/critical 则丢弃走危机路径。投机失败为 None。
    speculative_reply: str | None
    # K2 质询闭环：待验证画像假设（L4 且 confidence ≥ 质询水位）的友善标签，
    # 图启动前由服务层从 DB 载入（图谱内不持 DB 会话）。consultation_planner
    # 在 no_question_mode 关闭且非危机时，把至多一条经 loop_hint 注入，
    # 让本轮提问自然完成一次 belief 验证。空列表 = 本轮无质询候选。
    profile_question_candidates: list[str]
    # LLM 语义层输出的情绪读数（一句话，用户此刻的情绪状态；""=不可用）。
    # 生成端经 build_boundary_prompt 注入，让回复直接镜像当前情绪而非只看
    # risk_level 代理值。关键词层无此通道。
    emotional_state: str
    # LLM 语义层输出的知识主题（闭集枚举，≤3 个；[]=不可用）。knowledge_loader
    # 与关键词 topics 取并集作检索通道——"心情很低落"这类词表外表达由此可达。
    llm_topics: list[str]
    # 会话逐字近史（P3 上下文拼接修复）：最近 N 条 user/assistant 消息
    # （[{"role": "user"|"assistant", "content": str}]，时间正序）。以标准
    # API 格式追加在消息列表末尾——「换个方向吧」这类上下文依赖型消息
    # 此前因模型看不到逐字近史而被误读（Langfuse 2026-09-06 实证）。
    recent_history: list[dict[str, str]]
    # 图内引导练习（对话式 54321）：服务层预注入 {"tag", "current_step",
    # "step_responses", "transcript"}（practice_sessions 表，每轮从 DB 重建
    # 的状态由此进图）；无练习会话时为 None。practice_responder 据此路由。
    active_practice: dict[str, Any] | None
    # intent_router 的练习路由裁决（offer/consent/continue/pause/resume/
    # restart；""=本轮与练习无关）。条件边据此改道 practice_responder。
    practice_route: str
    # practice_responder 的裁决输出：advance（记录回答→下一步）/ pause
    # （软着陆暂停）/ complete（第 5 步已答→收尾）；""=本轮非练习轮。
    # 落库由服务层在图结束后执行（图内不持 DB 会话）。
    practice_action: str
    # LLM→TTS 句子级流式开关：True 时 response_generator 普通路径经
    # get_stream_writer 逐块吐 token，供 /respond/stream SSE 消费。
    # 仅影响是否推流式事件，不改变最终 state 文本（safety_reviewer/持久化不变）。
    stream_tokens: bool
