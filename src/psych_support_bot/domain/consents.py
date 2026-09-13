"""练习/评估须知与协议条款（单一事实源）。

设计约束：
- 条款文本只放后端——前端不硬编码，法务调整只改这里；
- 版本号随文本变更递增：每次练习确认必须匹配当前版本（用户决策
  2026-09-04：每次练习都确认，防后续练习更新带来的条款漂移）；
- 隐私协议与数据处理协议是"使用前一次性确认"（版本变更后重新确认），
  练习/评估须知是"每次进入确认"。
"""

DISCLAIMER_VERSION = "20260904.1"
PRIVACY_CONSENT_VERSION = "20260904.1"

# ---------------------------------------------------------------------------
# 隐私协议 + 数据处理协议（使用前确认；条目化摘要，前端弹窗渲染）
# ---------------------------------------------------------------------------

PRIVACY_AGREEMENT_POINTS_ZH = [
    "数据归属：你在本产品中写下的一切内容（对话、练习回答、打卡备注）仅你本人可见，不会用于对外展示或营销。",
    "AI 处理范围：只有在你主动开始练习/评估并确认须知后，你当次填写的内容才会被用于生成个人化反馈与引导；被动统计（分数趋势、完成次数）从不读取内容原文。",
    "商业化埋点只记录动作元数据（什么时候做了什么练习），绝不记录你的情绪内容。",
    "删除权：你可以要求删除你的账号与全部数据。",
]

DATA_PROCESSING_POINTS_ZH = [
    "你的输入会以脱敏请求的形式发送给大模型服务商用于即时生成回复，不做广告画像，不用于模型训练。",
    "练习报告（步骤回答、AI 反馈、引导对话）保存在你的账户下，仅向你本人展示。",
    "危机安全场景下，若你的输入包含自伤/自杀风险信号，系统会优先展示求助资源——这是安全设计，不构成诊断。",
]

PRIVACY_AGREEMENT_POINTS_EN = [
    "Data ownership: everything you write here (chats, exercise answers, notes) is visible only to you — never shown publicly or used for marketing.",
    "AI processing scope: content you enter is used to generate personalized feedback only after you actively start an exercise/assessment and confirm the notice; passive statistics (score trends, counts) never read your raw text.",
    "Commercial telemetry records action metadata only (what/when), never your emotional content.",
    "Right to deletion: you may request deletion of your account and all data.",
]

DATA_PROCESSING_POINTS_EN = [
    "Your input is sent as a request to the model provider solely to generate an immediate reply — no ad profiling, no model training.",
    "Exercise reports (step answers, AI feedback, guidance transcript) are stored under your account and shown only to you.",
    "In crisis-safety scenarios, if your input contains self-harm/suicide risk signals, the system prioritizes help resources — a safety design, not a diagnosis.",
]

# ---------------------------------------------------------------------------
# 每次练习/评估的须知（进入前勾选确认）
# ---------------------------------------------------------------------------

EXERCISE_DISCLAIMER_ZH = [
    "这是自助性质的练习，不是医疗诊断或治疗；如有持续困扰请寻求专业帮助。",
    "你在步骤中写下的回答，会在你本次确认后用于生成针对你的 AI 反馈与引导。",
    "这些内容仅你本人可见，可在「练习记录」中随时查看。",
    "如果你此刻正处于危机中，请优先使用页面底部的危机求助资源。",
]

EXERCISE_DISCLAIMER_EN = [
    "This is a self-help exercise, not medical diagnosis or treatment; seek professional help for persistent distress.",
    "Your step answers will be used to generate personalized AI feedback for this session, based on this confirmation.",
    "The content is visible only to you, and can be reviewed anytime under Exercise Records.",
    "If you are in crisis right now, please use the crisis resources at the bottom of the page first.",
]

ASSESSMENT_DISCLAIMER_ZH = [
    "这是心理筛查量表，结果反映近期状况，不构成诊断；解读仅供参考。",
    "作答过程大约需要几分钟，答案仅你本人可见。",
    "如果作答过程中出现强烈不适，你可以随时回复「暂停」保存进度。",
    "如果你此刻正处于危机中，请优先使用页面底部的危机求助资源。",
]

ASSESSMENT_DISCLAIMER_EN = [
    "This is a screening questionnaire — results reflect recent state, not a diagnosis; interpretation is for reference only.",
    "It takes a few minutes; your answers are visible only to you.",
    'If you feel strong discomfort while answering, you can reply "pause" anytime to save progress.',
    "If you are in crisis right now, please use the crisis resources at the bottom of the page first.",
]
