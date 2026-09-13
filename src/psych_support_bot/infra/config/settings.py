import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Psychological Support Bot"
    environment: str = "development"
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    database_url: str = "sqlite:///./psych_support_bot.db"
    redis_url: str = "redis://localhost:6379/0"
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
    # 流量环境标记：生产入口保持默认 "production"；eval/测试入口在进程
    # 早期覆盖为 "eval"/"test"（见 evals/runner.py 与 tests/conftest.py），
    # 使 Langfuse 仪表盘与巡检可按环境过滤——基线巡检（2026-09-06）显示
    # 自动化流量占 trace 总量约 89%，不分流则所有统计先要人工排噪。
    langfuse_environment: str = Field(default="production", alias="LANGFUSE_ENVIRONMENT")
    default_conversation_mode: str = "support"
    app_debug: bool = False
    # LLM-as-judge model (separate model for evaluation scoring)
    judge_api_key: str = Field(default="", alias="JUDGE_API_KEY")
    judge_base_url: str = Field(default="", alias="JUDGE_BASE_URL")
    judge_model: str = Field(default="DeepSeek-V4-Flash", alias="JUDGE_MODEL")
    # M2 首答延迟优化：规则判 low/elevated 且支持模式时，风险 LLM 分类与回复
    # 生成并行投机；风险升级 high/critical 则丢弃投机回复走危机路径。
    # 测试环境由 conftest 置 false，避免单测触发真实回复生成。
    speculative_reply_enabled: bool = Field(default=True, alias="SPECULATIVE_REPLY_ENABLED")
    # 记忆层记录模块热插拔开关（ai/memory_modules.py 注册表）：
    # 关闭的模块不渲染进 prompt，仅影响上下文参考信息——安全地板
    # （safety_floor_risk_level）走独立通道，不受这些开关影响。
    memory_module_assessments: bool = Field(default=True, alias="MEMORY_MODULE_ASSESSMENTS")
    memory_module_checkins: bool = Field(default=True, alias="MEMORY_MODULE_CHECKINS")
    memory_module_exercises: bool = Field(default=True, alias="MEMORY_MODULE_EXERCISES")
    memory_module_profile: bool = Field(default=True, alias="MEMORY_MODULE_PROFILE")
    # 画像渲染动态预算（ai/profile/renderer.py，知识提炼 §6 定稿）：不沿用
    # 记录层 DEFAULT_MODULE_BUDGET。每轮按供给侧压力调节——
    # budget = clamp(BASE × (1.2 − 0.4 × pressure), FLOOR, CAP)，pressure 由
    # 历史/摘要负载（0.7）与本轮主题命中数（0.3）构成；整条装箱不做残句
    # 截断；FLOOR 内必保 D8 负记忆。终值由 eval 证据更新（慢速自调）。
    profile_render_base: int = Field(default=480, alias="PROFILE_RENDER_BASE")
    profile_render_floor: int = Field(default=160, alias="PROFILE_RENDER_FLOOR")
    profile_render_cap: int = Field(default=720, alias="PROFILE_RENDER_CAP")
    # K2 LLM 语义提取（ai/profile/semantic.py）：D2/D3/D5 + 语义 D1。
    # P2 决策：成本暂不设上限、全量统计；节流只做 worker 纪律（触发条件
    # 限定）而非成本上限。危机轮不调用（高危内容不入画像层）。
    profile_llm_extraction_enabled: bool = Field(default=True, alias="PROFILE_LLM_EXTRACTION_ENABLED")
    profile_llm_every_turns: int = Field(default=3, alias="PROFILE_LLM_EVERY_TURNS")
    # 画像提取（ai/profile/extractor.py，K1b）：每轮 _finalize 后的确定性
    # 提取（D1 图内 topics + D4 练习效果信号），无额外 LLM 调用。fail-open：
    # 提取异常只记统计不阻断对话；危机轮零提取（高危内容不入画像层）。
    profile_extraction_enabled: bool = Field(default=True, alias="PROFILE_EXTRACTION_ENABLED")
    # JWT 认证：默认关闭（面板登录 UI 尚未上线，开启即拦截全部 /v1 数据端点）。
    # 商业化部署置 AUTH_ENABLED=true 并显式配置 JWT_SECRET_KEY。
    auth_enabled: bool = Field(default=False, alias="AUTH_ENABLED")
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")

    # ── 语音 I/O（与主 LLM 完全分离的供应商配置，便于独立换模型）────────
    # STT：provider = "openai"（multipart /audio/transcriptions，OpenAI/兼容
    # 网关）| "dots"（chat completions + audio_url 内容块，base64 data URI
    # 内联音频，无需公网托管 URL）。base_url/key/model 独立配置；缺省
    # 回落 OPENAI_*（行为兼容旧部署），但 dots 模式必须显式配置。
    voice_stt_provider: str = Field(default="", alias="VOICE_STT_PROVIDER")
    voice_stt_base_url: str = Field(default="", alias="VOICE_STT_BASE_URL")
    voice_stt_api_key: str = Field(default="", alias="VOICE_STT_API_KEY")
    voice_stt_model: str = Field(default="", alias="VOICE_STT_MODEL")
    voice_stt_language: str = Field(default="", alias="VOICE_STT_LANGUAGE")
    # STT 上下文提示词表：偏向领域高频词（正念/恐慌/心悸…），提升专名识别。
    # 缺省用适配层内置词表（按语种选中/英版）；显式配置则覆盖；置为 off 禁用。
    voice_stt_prompt: str = Field(default="", alias="VOICE_STT_PROMPT")
    # TTS：provider = "minimax"（wss /ws/v1/t2a_v2_bidi 双向流式，用户指定）
    # | "openai"（POST /audio/speech，OpenAI 兼容）。api_key/model/voice
    # 两家共用；minimax 另需 ws_url。provider 缺省时按 base_url 是否配置
    # 回落 openai（旧行为兼容）；dots 平台无 TTS 端点。
    voice_tts_provider: str = Field(default="", alias="VOICE_TTS_PROVIDER")
    voice_tts_ws_url: str = Field(default="", alias="VOICE_TTS_WS_URL")
    voice_tts_language_boost: str = Field(default="Chinese", alias="VOICE_TTS_LANGUAGE_BOOST")
    voice_tts_base_url: str = Field(default="", alias="VOICE_TTS_BASE_URL")
    voice_tts_api_key: str = Field(default="", alias="VOICE_TTS_API_KEY")
    voice_tts_model: str = Field(default="", alias="VOICE_TTS_MODEL")
    voice_tts_voice: str = Field(default="", alias="VOICE_TTS_VOICE")

    @model_validator(mode="after")
    def apply_dashscope_fallbacks(self) -> "Settings":
        if not self.openai_api_key:
            self.openai_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not self.openai_base_url:
            self.openai_base_url = os.getenv("DASHSCOPE_BASE_URL", "")
        if self.openai_model in {"", "gpt-4.1-mini"}:
            self.openai_model = os.getenv("DASHSCOPE_MODEL", self.openai_model)
        if not self.jwt_secret_key:
            # 未显式配置时生成随机临时密钥（注册/登录端点始终可用，需可签发）：
            # AUTH_ENABLED=true 的部署重启后所有已签发 token 失效——
            # 生产必须显式配置 JWT_SECRET_KEY。
            import secrets

            self.jwt_secret_key = secrets.token_urlsafe(48)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
