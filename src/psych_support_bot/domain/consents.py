"""练习/评估须知与协议条款（单一事实源）。

设计约束：
- 条款文本只放后端——前端不硬编码，法务调整只改这里；
- 版本号随文本变更递增：每次练习确认必须匹配当前版本（用户决策
  2026-09-04：每次练习都确认，防后续练习更新带来的条款漂移）；
- 隐私协议与数据处理协议是"使用前一次性确认"（版本变更后重新确认），
  练习/评估须知是"每次进入确认"。
"""

DISCLAIMER_VERSION = "20260904.1"
PRIVACY_CONSENT_VERSION = "20260915.2"
PROFILE_BETA_VERSION = "20260916.1"


def processing_services() -> list[dict[str, str]]:
    """Expose actual configured destinations without credentials or URL paths."""
    from urllib.parse import urlsplit

    from psych_support_bot.infra.config.settings import get_settings
    from psych_support_bot.infra.voice.adapter import get_stt_config, get_tts_config

    settings = get_settings()
    services = [
        {
            "purpose": "文本 AI / Text AI",
            "host": urlsplit(settings.openai_base_url or "https://api.openai.com").hostname or "",
            "model": settings.openai_model,
        }
    ]
    for purpose, config in (
        ("语音识别 / Speech recognition", get_stt_config()),
        ("语音合成 / Speech synthesis", get_tts_config()),
    ):
        if config is not None:
            services.append(
                {
                    "purpose": purpose,
                    "host": urlsplit(config.base_url or getattr(config, "ws_url", "")).hostname or "",
                    "model": config.model,
                }
            )
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        analytics_purpose = (
            "去标识化对话产品分析与运行指标 / De-identified conversation analytics and operational metrics"
            if settings.langfuse_content_analytics
            else "运行指标 / Operational metrics"
        )
        services.append(
            {
                "purpose": analytics_purpose,
                "host": urlsplit(settings.langfuse_host).hostname or "",
                "model": "",
            }
        )
    for enabled, host, label in (
        (settings.auth_google_client_ids, "accounts.google.com", "Google 身份验证 / Sign-in"),
        (settings.auth_apple_client_ids, "appleid.apple.com", "Apple 身份验证 / Sign-in"),
        (settings.auth_huawei_client_ids, "accounts.huawei.com", "Huawei 身份验证 / Sign-in"),
    ):
        if enabled.strip():
            services.append({"purpose": label, "host": host, "model": ""})
    return services


def current_privacy_version() -> str:
    import hashlib
    import json

    fingerprint = hashlib.sha256(json.dumps(processing_services(), sort_keys=True).encode()).hexdigest()[:12]
    return f"{PRIVACY_CONSENT_VERSION}-{fingerprint}"


# ---------------------------------------------------------------------------
# 隐私协议 + 数据处理协议（使用前确认；条目化摘要，前端弹窗渲染）
# ---------------------------------------------------------------------------

PRIVACY_AGREEMENT_POINTS_ZH = [
    "保存范围：服务端保存内部账号、登录身份与会话、对话与摘要、测评答案和结果、练习回答与引导、打卡、计划、周报，以及由这些内容生成的个性化记忆和历史切片。Web Access Token 仅在页面内存中，Refresh Token 位于 HttpOnly Cookie；原生端 Refresh Token 应保存到系统 Keychain/Keystore。浏览器还保存游客恢复秘密、未提交草稿和部分记录缓存。",
    "使用范围：这些数据用于你自己的心理支持、上下文记忆、风险识别和记录展示，不公开展示，不用于广告。系统会从对话和记录中形成内部个性化理解，用于调整回复方式，不用于诊断或人格推断。授权运维人员可能因故障排查和删除请求接触服务端数据。",
    '同意与拒绝：同意当前版本后才可提交内容或调用 AI；暂不同意仍可查看已有记录、下载数据或注销。你可在"我"页随时暂停个性化记忆；暂停后不再提取或使用记忆，已有记忆保留，亦可另行关闭并清除。练习和测评另有进入须知。',
    "下载与注销：下载包含在线业务库中的记录、派生数据和非秘密身份元数据，不包含密码哈希、令牌/挑战哈希、Passkey 公钥、供应商用户标识或 Langfuse 分析副本。注销删除登录身份、Passkey、认证挑战、会话及全部业务数据，并清理当前浏览器的本产品缓存和认证 Cookie。你已下载的文件及其他设备缓存需自行删除。",
    "外部清理：注销后，系统用不可逆假名标识定位 Langfuse 中可关联的历史与当前追踪并异步删除，可凭注销回执查询。确认追踪已删除后，回执中的临时定位信息会移除；缺少关联标识的旧追踪需运营人员核查。",
    "备份边界：在线注销不代表离线备份已同步销毁。部署方须处理备份到期删除，并在恢复备份前重放注销清单；备份和模型服务商留存不能由此页面证明已清除。",
]

DATA_PROCESSING_POINTS_ZH = [
    "身份验证：选择 Apple、Google 或华为登录时，对应供应商处理登录交互并向本服务签发身份令牌。本服务校验签名、签发方、客户端、有效期和一次性随机数，仅保存由供应商、签发方和用户标识生成的密钥假名，不保存身份令牌，也不会按邮箱自动合并账号。",
    "文本 AI：对话原文、必要的近期历史、摘要、个性化记忆、相关记录，以及你主动提交的测评或练习内容，可能发送到所配置的大模型服务，用于回复、风险分类、反馈和记忆提取。程序不会保证输入已完全脱敏，请避免填写无关的姓名、联系方式等身份信息。",
    "语音 AI：使用录音时，音频发送到语音识别服务；开启朗读时，待朗读文本发送到语音合成服务。应用不持久保存用户录音，转写文字按对话记录处理。",
    "Langfuse 产品分析：部署启用内容分析时，本地会先隐藏常见邮箱、手机号、证件号、IP、链接和凭据，再上传本轮用户输入、最终呈现给用户的模型回复、耗时、用量和失败标记。原始用户 ID 与会话 ID 会替换为服务端密钥生成的不可逆假名标识，用于分组分析和注销删除；不上传系统提示词、历史上下文、摘要或个性化记忆字段。",
    "去标识化限制：自由文本可能通过姓名、事件或上下文重新识别个人，自动规则无法保证完全匿名。请勿输入无关身份信息。上述内容仅用于产品质量、安全和性能分析，不用于广告；商业化用量埋点仍只记录动作元数据。",
    "供应商边界：本产品不将内容用于广告或主动用于模型训练；外部服务的留存、训练政策及删除能力由部署方与供应商的约定决定，本产品无法承诺第三方零留存或即时删除。",
    "运行日志：应用日志只记录技术状态、计数和固定错误类型，不主动记录对话、个性化记忆、原始用户标识或供应商错误正文；部署方负责日志访问控制和到期删除。",
    "风险识别用于优先展示求助资源，不构成医疗诊断或治疗。",
]

PRIVACY_AGREEMENT_POINTS_EN = [
    "Storage: the server stores the internal account, login identities and sessions, conversations and summaries, assessments, guided exercise answers, check-ins, plans, reports, personalized memory and history slices. Web access tokens stay in page memory and refresh tokens use HttpOnly cookies; native refresh tokens belong in system Keychain/Keystore. The browser also stores a guest recovery secret, drafts and some cached records.",
    "Purpose: personal support, contextual memory, risk screening and your records; no public display or advertising. The system forms an internal personalized understanding from conversations and records, used to adjust how it responds — not for diagnosis or personality profiling. Authorized operators may access server data for maintenance and erasure requests.",
    "Choice: accepting the current policy is required before submitting content or using AI. You may still read, export or delete existing data without accepting. Personalized memory can be paused at any time from Me; pausing stops memory extraction and use while retaining existing data, which can also be disabled and erased separately. Exercises and assessments have additional notices.",
    "Export and deletion: exports include online business records, derived data and non-secret identity metadata. Password, token and challenge hashes, Passkey public keys, provider subjects and Langfuse copies are excluded. Account deletion erases login identities, Passkeys, challenges, sessions and all business records, then clears this browser's product caches and authentication cookies. Remove downloaded exports and other devices' caches yourself.",
    "After account deletion, irreversible pseudonymous identifiers locate linked historical and current Langfuse traces for asynchronous erasure. A receipt reports progress. Temporary lookup data is removed after verification; unlinked legacy traces require operator review.",
    "Offline backups and provider retention are separate: operators must expire backups and replay erasures before restoring them. This page cannot verify deletion from backups or model providers.",
]

DATA_PROCESSING_POINTS_EN = [
    "Identity: when you choose Apple, Google or Huawei sign-in, that provider handles the sign-in interaction and issues an identity token to this service. The service validates its signature, issuer, client, expiry and one-time nonce, stores only a keyed pseudonym derived from provider/issuer/subject, does not retain the identity token, and never merges accounts by email automatically.",
    "Text AI may receive your messages, relevant history, summaries, personalized memory, records and submitted assessment/exercise content for responses, risk screening, feedback and memory extraction. Inputs are not guaranteed to be fully de-identified; avoid unnecessary identity details.",
    "Voice: recordings go to speech recognition and spoken text goes to speech synthesis. The application does not persist user recordings; transcriptions are treated as conversation text.",
    "Langfuse product analytics: when content analytics is enabled, local rules first hide common email addresses, phone and ID numbers, IP addresses, links and credentials. Langfuse receives the current user input, final user-facing model reply, timing, usage and failure flags. Raw user/session IDs are replaced with irreversible keyed pseudonyms for grouping and account-erasure lookup. System prompts, history context, summaries and personalized memory fields are excluded.",
    "De-identification is limited: names, events or context in free text may still identify someone, and automated rules cannot guarantee complete anonymity. Avoid unnecessary identity details. The content is used for product quality, safety and performance analysis, not advertising; commercial usage telemetry remains action metadata only.",
    "The product does not use content for ads or actively train models with it. Provider retention, training and deletion depend on the operator's supplier agreements; third-party zero retention or immediate erasure cannot be promised here.",
    "Application logs contain technical status, counts and fixed error types, without intentionally recording conversations, personalized memory, raw user identifiers or provider error bodies. Operators control log access and expiry.",
    "Risk screening prioritizes support resources; it is not medical diagnosis or treatment.",
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
    "If you are in crisis now, please use the crisis resources at the bottom of the page first.",
]


# ---------------------------------------------------------------------------
# 画像（个性化记忆）Beta 知悉协议（独立于隐私协议，可选勾选）
# ---------------------------------------------------------------------------

PROFILE_BETA_POINTS_ZH = [
    "Beta 功能：个性化记忆是实验性功能（Beta）。系统会从你的对话和记录中提取观察，形成对你持续更新的理解，用于调整回复方式。这些观察不构成诊断或人格推断。",
    "数据范围：系统可能记住你提到的困扰、目标、支持偏好、生活背景等信息。所有记忆仅你本人可见，可在「我 → 画像」中查看、暂停或清除。",
    "敏感背景（可选）：如果你同意，系统还可以记住你的健康状况、生活经历等背景信息，以提供更个性化的支持。你可以在下方选择是否开启。宗教和信仰信息如被提及，系统会使用代号存储，不在前端展示。",
    "暂停与删除：你可以随时暂停画像功能（暂停后不再提取或使用记忆，已有记忆保留），也可以关闭并永久删除所有画像数据。",
]

PROFILE_BETA_POINTS_EN = [
    "Beta feature: personalized memory is experimental (Beta). The system extracts observations from your conversations and records, building a continuously updated understanding to adjust how it responds. These observations are not diagnoses or personality profiling.",
    "Data scope: the system may remember concerns, goals, support preferences, life context and similar information. All memory is visible only to you, and can be reviewed, paused or erased under Me → Profile.",
    "Sensitive background (optional): with your consent, the system can also remember health and life-experience details for more personalized support. You can choose whether to enable this below. Religious and faith information, if mentioned, is stored using codenames and not displayed on the frontend.",
    "Pause and erase: you can pause the profile feature at any time (pausing stops extraction and use while retaining existing data), or disable and permanently erase all profile data.",
]

PROFILE_BETA_SENSITIVE_ZH = [
    "允许系统记住我的健康状况、生活经历等背景信息。",
    "我了解宗教和信仰信息将使用代号存储，不在前端展示。",
]

PROFILE_BETA_SENSITIVE_EN = [
    "Allow the system to remember my health and life-experience background.",
    "I understand that religious and faith information will be stored using codenames and not displayed on the frontend.",
]
