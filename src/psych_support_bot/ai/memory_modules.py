"""记录层热插拔记忆模块。

把用户的结构化记录（量表评估 / 每日打卡 / 练习完成）渲染成带标签的
短摘要，注入 memory_snapshot，替代旧的裸字段拼接。设计约束：

- 模块只提供"上下文参考信息"，可经 MEMORY_MODULE_* 开关逐个关闭；
  安全信号（safety_floor_risk_level / RiskEvent）走独立结构化通道，
  绝不经由本层渲染，因此关闭模块不影响任何安全地板。
- 情绪扫描（跨轮矛盾检测 / 连续低落升级）只读 user_history_text
  （用户原话 + 会话摘要），不读本层文本——"失眠严重程度量表"这类
  临床词汇不能被误当成用户本人的情绪表达。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar, Protocol

from sqlalchemy.orm import Session

from psych_support_bot.ai.tools.exercises import get_exercise_by_tag
from psych_support_bot.domain.assessments.service import questionnaire_guide
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.exercise_repositories import get_user_exercise_records
from psych_support_bot.infra.db.repositories import get_recent_checkins, get_user_assessments

logger = logging.getLogger(__name__)

# 单模块渲染预算（字符）。超预算截断加省略号，防止记录多时把
# memory prompt 的预算全部挤占。
DEFAULT_MODULE_BUDGET = 200

# severity_band 英文枚举 -> 中文展示（与评估跟进文案共用一套口径）。
_ZH_BANDS = {
    "minimal": "极轻度",
    "mild": "轻度",
    "moderate": "中度",
    "moderately_severe": "中重度",
    "severe": "重度",
    "subthreshold": "亚阈值",
    "none": "无明显",
}


class MemoryModule(Protocol):
    """记录层模块协议：实现 name + render 即可注册进 _MEMORY_MODULES。"""

    name: str

    def render(
        self,
        session: Session,
        user_id: str,
        *,
        language: str,
        char_budget: int,
    ) -> str | None: ...


def _is_en(language: str) -> bool:
    return language == "en"


def _clip(text: str, budget: int) -> str:
    if not budget or len(text) <= budget:
        return text
    return text[: budget - 1].rstrip() + "…"


def _format_date(value: datetime | None, is_en: bool) -> str:
    if value is None:
        return ""
    return f"{value.month}/{value.day}" if is_en else f"{value.month}月{value.day}日"


def _fmt_score(value: float) -> str:
    return f"{value:g}"


def _streak_from_end(values: list[float], qualifies) -> int:
    """从最新一条往回数连续命中的天数。"""
    streak = 0
    for value in reversed(values):
        if qualifies(value):
            streak += 1
        else:
            break
    return streak


class AssessmentMemoryModule:
    """量表评估趋势：最新 2-3 条 + 相邻两次的分差（趋势优先于均分）。"""

    name = "assessments"

    def render(self, session: Session, user_id: str, *, language: str, char_budget: int) -> str | None:
        records = get_user_assessments(session, user_id, limit=4)
        if not records:
            return None
        is_en = _is_en(language)
        entries: list[str] = []
        for index, record in enumerate(records[:3]):
            title = questionnaire_guide(record.assessment_type, language="en" if is_en else "zh").title
            band = record.severity_band if is_en else _ZH_BANDS.get(record.severity_band, record.severity_band)
            body = f"{title} {record.score}分（{band}）" if not is_en else f"{title} {record.score} ({band})"
            date_text = _format_date(record.created_at, is_en)
            if date_text:
                body = f"{date_text} {body}"
            previous = records[index + 1] if index + 1 < len(records) else None
            if previous is not None:
                delta = record.score - previous.score
                if delta > 0:
                    body += f"，较上次上升{delta}分" if not is_en else f", up {delta} from last"
                elif delta < 0:
                    body += f"，较上次下降{-delta}分" if not is_en else f", down {-delta} from last"
            entries.append(body)
        joiner = "；" if not is_en else "; "
        label = "评估记录：" if not is_en else "Assessments: "
        return _clip(label + joiner.join(entries), char_budget)


class CheckinMemoryModule:
    """打卡趋势：近 7 天心情/焦虑/睡眠的走向箭头，连续命中才标注关注。"""

    name = "checkins"

    # 关注阈值：从最新一条往回连续 STREAK_WINDOW 天命中才标注，
    # 单日波动不触发，避免记忆里充满噪音。
    MOOD_LOW = 4
    ANXIETY_HIGH = 7
    SLEEP_SHORT_HOURS = 5.0
    STREAK_WINDOW = 3

    def render(self, session: Session, user_id: str, *, language: str, char_budget: int) -> str | None:
        records = get_recent_checkins(session, user_id, limit=7)
        if not records:
            return None
        is_en = _is_en(language)
        chronological = list(reversed(records))
        metric_specs = [
            # (zh 标签, en 标签, 取值, 判定"需关注"的方向, zh 描述, en 描述)
            ("心情", "mood", lambda r: float(r.mood_score), lambda v: v <= self.MOOD_LOW, "低位", "low"),
            ("焦虑", "anxiety", lambda r: float(r.anxiety_score), lambda v: v >= self.ANXIETY_HIGH, "偏高", "high"),
            ("睡眠", "sleep", lambda r: float(r.sleep_hours), lambda v: v <= self.SLEEP_SHORT_HOURS, "不足", "short"),
        ]
        parts: list[str] = []
        for zh_label, en_label, getter, qualifies, zh_word, en_word in metric_specs:
            values = [getter(record) for record in chronological]
            arrows = "→".join(_fmt_score(value) for value in values)
            text = f"{en_label if is_en else zh_label} {arrows}"
            streak = _streak_from_end(values, qualifies)
            if streak >= self.STREAK_WINDOW:
                text += f"（连续{streak}天{zh_word}，需关注）" if not is_en else f" ({en_word} {streak}d in a row)"
            parts.append(text)
        joiner = "，" if not is_en else ", "
        label = "打卡趋势：" if not is_en else "Check-in trend: "
        return _clip(label + joiner.join(parts), char_budget)


class ExerciseMemoryModule:
    """练习完成记录：最近 3 条，带来源（对话内引导 / 用户面板打卡）。"""

    name = "exercises"

    _SOURCE_ZH: ClassVar[dict[str, str]] = {"chat": "对话", "panel": "面板"}
    _SOURCE_EN: ClassVar[dict[str, str]] = {"chat": "chat", "panel": "panel"}

    def render(self, session: Session, user_id: str, *, language: str, char_budget: int) -> str | None:
        records = get_user_exercise_records(session, user_id, limit=3)
        if not records:
            return None
        is_en = _is_en(language)
        source_map = self._SOURCE_EN if is_en else self._SOURCE_ZH
        entries: list[str] = []
        for record in records:
            name = record.exercise_tag
            exercise = get_exercise_by_tag(record.exercise_tag, language="en" if is_en else "zh")
            if exercise and exercise.get("name"):
                name = str(exercise["name"])
            source = source_map.get(record.source or "", record.source or "")
            body = f"{name}（{source}）" if not is_en else f"{name} ({source})"
            date_text = _format_date(record.completed_at, is_en)
            if date_text:
                body = f"{date_text} {body}"
            entries.append(body)
        joiner = "；" if not is_en else "; "
        label = "最近完成：" if not is_en else "Recent exercises: "
        return _clip(label + joiner.join(entries), char_budget)


_MEMORY_MODULES: list[MemoryModule] = [
    AssessmentMemoryModule(),
    CheckinMemoryModule(),
    ExerciseMemoryModule(),
]


def render_record_layers(session: Session, user_id: str, language: str = "") -> str:
    """渲染全部启用的记录层模块，按 MEMORY_MODULE_<NAME> 开关热插拔。

    单模块渲染失败只跳过该层，绝不阻断对话（fail-open）；返回串以
    "\\n" 连接，由 build_memory_snapshot 作为独立片段并入快照。
    """
    settings = get_settings()
    parts: list[str] = []
    for module in _MEMORY_MODULES:
        if not getattr(settings, f"memory_module_{module.name}", True):
            continue
        try:
            rendered = module.render(session, user_id, language=language, char_budget=DEFAULT_MODULE_BUDGET)
        except Exception:
            logger.warning("Memory module %r failed to render; skipping layer.", module.name, exc_info=True)
            continue
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)
