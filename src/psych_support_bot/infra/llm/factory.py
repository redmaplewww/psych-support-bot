from typing import NamedTuple

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from psych_support_bot.infra.config.settings import get_settings

# Temperature per conversation mode:
#   crisis     = 0.0  (deterministic, safety-critical)
#   assessment = 0.3  (low variance for consistent scoring guidance)
#   support    = 0.6  (warmth needs varied natural phrasing; safety_reviewer
#                     red lines remain the guardrail — a rigid 0.4 was a
#                     root cause of the mechanical, same-shaped replies)
#   planning   = 0.4  (structured but flexible)
#   intervention = 0.5 (slightly creative for technique suggestions)
MODE_TEMPERATURES: dict[str, float] = {
    "crisis": 0.0,
    "assessment": 0.3,
    "support": 0.6,
    "planning": 0.4,
    "intervention": 0.5,
    # Risk classification must be deterministic and reproducible — a judgement
    # that flips with sampling noise directly changes crisis-mode routing.
    "risk_classification": 0.0,
}
DEFAULT_TEMPERATURE = 0.4


def get_temperature_for_mode(mode: str) -> float:
    """Return the appropriate temperature for a conversation mode."""
    return MODE_TEMPERATURES.get(mode, DEFAULT_TEMPERATURE)


class ModelCallLimits(NamedTuple):
    max_tokens: int
    timeout: float


# Per-mode ceilings (max_tokens) and timeouts (seconds). Output caps only clip
# pathological runs — normal replies are 3 short bubbles, far below the limit;
# classification outputs a small JSON. Timeouts bound the worst case: retrying
# is owned by the _invoke choke point, so a hung HTTP call fails fast into the
# caller's declared fallback instead of stacking langchain's default retries.
MODE_LIMITS: dict[str, "ModelCallLimits"] = {
    "crisis": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "assessment": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "support": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "planning": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "intervention": ModelCallLimits(max_tokens=1024, timeout=30.0),
    # 风险分类两级（2026-09-09）：risk_screen 关思考快筛（~1s，高危用例
    # 6/6 正确识别且规则通道独立兜底）；risk_classification 思考开二次确认
    # （安全关键分级依赖思维链，evals 基线对照：关思考 routing 大面积滑向
    # support/低危）。快筛判 low 直接采纳，非 low 升级确认。
    # max_tokens 2048 + timeout 25s（20260912）：思考开时思维链与正文共享
    # token 预算，1024 下思考挤爆预算 → 正文返回空 → 咽喉层空内容重试
    # （退避 0.5/1.0s）把单轮拖到 10~25s。上调后实测空内容消失，但思考
    # 合法变长（成功样本 10.6s），15s 超时成为新瓶颈 → 同步放宽到 25s，
    # 让多数调用首发成功而非「15s 掐断 → 重试堆叠」。分级语义不变。
    "risk_screen": ModelCallLimits(max_tokens=512, timeout=10.0),
    "risk_classification": ModelCallLimits(max_tokens=2048, timeout=25.0),
}


def build_chat_model(
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    mode: str = "support",
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> ChatOpenAI:
    """Build a ChatOpenAI. Defaults (caps/timeout) come from the call mode;
    explicit arguments override — speculative/parallel callers may tighten them.

    max_retries=0: retry policy lives solely in _invoke (choke point) so the
    effective worst case is (1 + len(_RETRY_BACKOFF_SECONDS)) HTTP calls, not
    (1+2 langchain) × (1+2 choke point).
    """
    settings = get_settings()
    key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    limits = MODE_LIMITS.get(mode)
    # 思考开关按调用类型分流（2026-09-09 evals 基线对照结论）：
    # - 关思考（快 4-8 倍）：机械性任务——STT 转写、结构化小输出。回复生成
    #   invoke 6.1s→0.74s、首 token 1.68s→0.15s，质量实测无损。
    # - 保持思考：risk_classification（mode="risk_classification"）。关思考后
    #   routing 大面积滑向 support/低危（11 失败 vs 基线 4），分级判定依赖
    #   思维链，安全关键不允许为延迟牺牲。
    disable_thinking = mode != "risk_classification"
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=key,
        base_url=settings.openai_base_url or None,
        temperature=temperature,
        max_retries=0,
        timeout=timeout if timeout is not None else (limits.timeout if limits else 30.0),
        max_tokens=max_tokens if max_tokens is not None else (limits.max_tokens if limits else 1024),
        default_headers={"api-key": settings.openai_api_key},
        # dots 网关关思考姿势（实测生效）：reasoning_effort 落请求顶层，
        # chat_template_kwargs 经 extra_body 合并进请求体顶层。
        **(
            {"reasoning_effort": "none", "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
            if disable_thinking
            else {}
        ),
    )
