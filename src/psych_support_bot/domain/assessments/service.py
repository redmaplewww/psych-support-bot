from typing import Any, cast

from fastapi import HTTPException

from psych_support_bot.domain.assessments.questionnaires import QUESTIONNAIRES
from psych_support_bot.domain.assessments.schemas import (
    AssessmentAnswerSet,
    AssessmentInterpretation,
    AssessmentResult,
    AssessmentSafetyFlag,
    AssessmentScore,
    AssessmentType,
    QuestionnaireGuide,
    QuestionnaireOption,
    QuestionnaireSessionItem,
    QuestionnaireSessionView,
)


def severity_for_score(assessment_type: AssessmentType, score: int) -> str:
    bands = {
        "phq9": [
            (4, "minimal"),
            (9, "mild"),
            (14, "moderate"),
            (19, "moderately_severe"),
        ],
        "gad7": [(4, "minimal"), (9, "mild"), (14, "moderate")],
        "isi": [(7, "none"), (14, "subthreshold"), (21, "moderate")],
    }
    for threshold, label in bands[assessment_type]:
        if score <= threshold:
            return label
    return "severe"


EN_GUIDE_OVERRIDES: dict[str, dict[str, object]] = {
    "phq9": {
        "title": "PHQ-9 Depression Screener",
        "timeframe": "Over the last 2 weeks",
        "purpose": "Screens for depressive symptoms and how much they may be affecting daily life.",
        "instructions": [
            "Answer based on your average experience over the last 2 weeks, not only your best or worst day.",
            "Choose the option that fits best even if none feels perfect.",
            "This is a screening tool, not a diagnosis.",
        ],
        "options": [
            {"value": 0, "label": "Not at all"},
            {"value": 1, "label": "Several days"},
            {"value": 2, "label": "More than half the days"},
            {"value": 3, "label": "Nearly every day"},
        ],
        "items": [
            "Little interest or pleasure in doing things",
            "Feeling down, depressed, or hopeless",
            "Trouble falling or staying asleep, or sleeping too much",
            "Feeling tired or having little energy",
            "Poor appetite or overeating",
            "Feeling bad about yourself or that you have let yourself or your family down",
            "Trouble concentrating on things",
            "Moving or speaking slowly, or being so fidgety or restless that others notice",
            "Thoughts that you would be better off dead or of hurting yourself in some way",
        ],
    },
    "gad7": {
        "title": "GAD-7 Anxiety Screener",
        "timeframe": "Over the last 2 weeks",
        "purpose": "Screens for anxiety symptoms such as excessive worry, tension, and restlessness.",
        "instructions": [
            "Answer based on the last 2 weeks.",
            "Think about how often these experiences showed up in daily life.",
            "This tool helps screening and self-understanding, not diagnosis.",
        ],
        "options": [
            {"value": 0, "label": "Not at all"},
            {"value": 1, "label": "Several days"},
            {"value": 2, "label": "More than half the days"},
            {"value": 3, "label": "Nearly every day"},
        ],
        "items": [
            "Feeling nervous, anxious, or on edge",
            "Not being able to stop or control worrying",
            "Worrying too much about different things",
            "Trouble relaxing",
            "Being so restless that it is hard to sit still",
            "Becoming easily annoyed or irritable",
            "Feeling afraid as if something awful might happen",
        ],
    },
    "isi": {
        "title": "Insomnia Severity Index",
        "timeframe": "Over the last 2 weeks",
        "purpose": "Screens for the severity and day-to-day impact of sleep difficulties.",
        "instructions": [
            "Answer based on the last 2 weeks, using your usual sleep pattern rather than a single night.",
            "Try to reflect both nighttime difficulty and daytime impact.",
            "This is a screening tool and does not diagnose a sleep disorder by itself.",
        ],
        "options": [
            {"value": 0, "label": "None"},
            {"value": 1, "label": "Mild"},
            {"value": 2, "label": "Moderate"},
            {"value": 3, "label": "Severe"},
            {"value": 4, "label": "Very severe"},
        ],
        "items": [
            "Difficulty falling asleep",
            "Difficulty staying asleep",
            "Problems waking too early",
            "How satisfied are you with your current sleep pattern?",
            "How noticeable to others do you think your sleep problem is in terms of impairing your quality of life?",
            "How worried or distressed are you about your current sleep problem?",
            "To what extent do you consider your sleep problem to interfere with daily functioning?",
        ],
    },
}


def questionnaire_guide(assessment_type: AssessmentType, language: str = "zh") -> QuestionnaireGuide:
    definition = cast(dict[str, Any], QUESTIONNAIRES[assessment_type]).copy()
    if language == "en":
        definition.update(EN_GUIDE_OVERRIDES[assessment_type])
    return QuestionnaireGuide(
        code=assessment_type,
        title=str(definition["title"]),
        timeframe=str(definition["timeframe"]),
        purpose=str(definition["purpose"]),
        instructions=cast(list[str], definition["instructions"]),
        options=[
            QuestionnaireOption(
                value=int(cast(dict[str, Any], option)["value"]),
                label=str(cast(dict[str, Any], option)["label"]),
            )
            for option in cast(list[dict[str, object]], definition["options"])
        ],
        items=cast(list[str], definition["items"]),
    )


def list_questionnaire_guides(language: str = "zh") -> list[QuestionnaireGuide]:
    return [questionnaire_guide(assessment_type, language=language) for assessment_type in QUESTIONNAIRES]


def build_questionnaire_session_view(
    *,
    session_id: str,
    user_id: str,
    assessment_type: AssessmentType,
    answers: list[int],
    status: str,
    language: str = "zh",
) -> QuestionnaireSessionView:
    guide = questionnaire_guide(assessment_type, language=language)
    total_items = len(guide.items)
    current_index = min(len(answers), total_items)
    next_item = None
    if status != "completed" and current_index < total_items:
        next_item = QuestionnaireSessionItem(
            index=current_index,
            text=guide.items[current_index],
            options=guide.options,
        )
    return QuestionnaireSessionView(
        session_id=session_id,
        user_id=user_id,
        assessment_type=assessment_type,
        questionnaire_title=guide.title,
        timeframe=guide.timeframe,
        status=status,
        current_index=current_index,
        total_items=total_items,
        answers=answers,
        instructions=guide.instructions,
        next_item=next_item,
    )


def detect_questionnaire_request(message: str) -> AssessmentType | None:
    lowered = message.casefold()
    mapping = {
        "phq9": [
            "phq-9",
            "phq9",
            "抑郁量表",
            "抑郁筛查",
            "情绪量表",
            "测一下抑郁",
            "测测抑郁",
            "测抑郁",
            "抑郁测试",
            "抑郁测评",
            "抑郁程度",
        ],
        "gad7": [
            "gad-7",
            "gad7",
            "焦虑量表",
            "焦虑筛查",
            "测一下焦虑",
            "测测焦虑",
            "测焦虑",
            "焦虑测试",
            "焦虑测评",
            "焦虑程度",
        ],
        "isi": [
            "isi",
            "失眠量表",
            "睡眠量表",
            "失眠筛查",
            "测一下睡眠",
            "测睡眠",
            "睡眠测试",
            "失眠测试",
            "睡眠测评",
        ],
    }
    for assessment_type, keywords in mapping.items():
        if any(keyword in lowered for keyword in keywords):
            return cast(AssessmentType, assessment_type)
    return None


def parse_questionnaire_answer(message: str, assessment_type: AssessmentType) -> int | None:
    lowered = message.strip().casefold()
    if lowered.isdigit():
        value = int(lowered)
        max_score = int(cast(dict[str, Any], QUESTIONNAIRES[assessment_type])["item_max_score"])
        if 0 <= value <= max_score:
            return value

    option_aliases: dict[AssessmentType, dict[int, tuple[str, ...]]] = {
        "phq9": {
            0: ("not at all", "没有", "完全没有", "从不"),
            1: ("several days", "几天", "偶尔"),
            2: ("more than half the days", "一半以上天数", "大半时间"),
            3: ("nearly every day", "几乎每天", "每天"),
        },
        "gad7": {
            0: ("not at all", "没有", "完全没有", "从不"),
            1: ("several days", "几天", "偶尔"),
            2: ("more than half the days", "一半以上天数", "大半时间"),
            3: ("nearly every day", "几乎每天", "每天"),
        },
        "isi": {
            0: ("none", "没有", "无"),
            1: ("mild", "轻度", "轻微"),
            2: ("moderate", "中度", "一般"),
            3: ("severe", "重度", "严重"),
            4: ("very severe", "非常严重", "很严重"),
        },
    }
    for value, aliases in option_aliases[assessment_type].items():
        if any(alias in lowered for alias in aliases):
            return value
    return None


def detect_skip_or_exit(message: str) -> bool:
    lowered = message.strip().casefold()
    skip_words = [
        "skip",
        "pass",
        "don't want",
        "want to stop",
        "want to quit",
        "不想做了",
        "不想做",
        "跳过",
        "算了",
        "不要",
        "跳过这题",
        "退出",
        "停止",
        "cancel",
        "quit",
        "stop",
    ]
    return any(word in lowered for word in skip_words)


def build_questionnaire_prompt(view: QuestionnaireSessionView, *, error_hint: str | None = None) -> str:
    if view.next_item is None:
        return f"{view.questionnaire_title} is complete. I can help you interpret the result in plain language."
    options_text = ", ".join(f"{option.value}={option.label}" for option in view.next_item.options)
    answered = view.current_index
    remaining = view.total_items - answered - 1
    pct = round((answered + 1) / view.total_items * 100)
    prompt = (
        f"We can go step by step with {view.questionnaire_title}. {view.timeframe}. "
        f"Question {answered + 1} of {view.total_items} ({pct}% done, {remaining} remaining): {view.next_item.text} "
        f"Reply with one option ({options_text})."
    )
    if error_hint:
        prompt += f" {error_hint}"
    return prompt


def build_assessment_followup_reply(result: AssessmentResult, *, user_message: str = "") -> str:
    is_zh = bool(user_message) and any("\u4e00" <= c <= "\u9fff" for c in user_message)
    lines: list[str] = []

    if is_zh:
        lines.append(f"感谢你完成{result.questionnaire_title}。")
        lines.append(f"你的得分是{result.score}，属于{result.severity_band}范围。")
    else:
        lines.append(f"Thanks for completing {result.questionnaire_title}.")
        lines.append(f"Your score is {result.score}, which falls in the {result.severity_band} range.")

    lines.append(result.interpretation.plain_meaning)
    lines.append(result.interpretation.functional_impact)
    lines.append(result.interpretation.care_consideration)
    lines.append(result.interpretation.disclaimer)
    if result.interpretation.safety_flags:
        lines.extend(flag.message for flag in result.interpretation.safety_flags)

    if is_zh:
        lines.append("看完结果后，如果你想聊聊感受，或者想了解有什么小方法可以试试，随时告诉我。")
    else:
        lines.append("Is there anything from today's result you'd like to talk more about?")

    return " ".join(lines)


def validate_score(assessment_type: AssessmentType, score: int) -> None:
    definition = cast(dict[str, Any], QUESTIONNAIRES[assessment_type])
    maximum = int(definition["max_score"])
    if score < 0 or score > maximum:
        raise HTTPException(
            status_code=422,
            detail=f"Score for {assessment_type} must be between 0 and {maximum}.",
        )


def score_from_answers(assessment_type: AssessmentType, answers: AssessmentAnswerSet) -> int:
    definition = cast(dict[str, Any], QUESTIONNAIRES[assessment_type])
    expected_length = len(cast(list[str], definition["items"]))
    item_max_score = int(definition["item_max_score"])
    if len(answers.answers) != expected_length:
        raise HTTPException(
            status_code=422,
            detail=(f"{assessment_type} requires {expected_length} answers; received {len(answers.answers)}."),
        )
    if any(answer < 0 or answer > item_max_score for answer in answers.answers):
        raise HTTPException(
            status_code=422,
            detail=(f"Each answer for {assessment_type} must be between 0 and {item_max_score}."),
        )
    return sum(answers.answers)


def interpretation_for_result(
    assessment_type: AssessmentType,
    score: int,
    severity_band: str,
    answers: list[int] | None = None,
    language: str = "en",
) -> AssessmentInterpretation:
    if language == "zh":
        common_disclaimer = (
            "这是一份筛查结果，不等同于临床诊断。需要结合你近期的压力、身体状况、睡眠情况和日常功能一起理解。"
        )
        if assessment_type == "phq9":
            plain_meaning = {
                "minimal": "你的回答显示，目前抑郁相关症状水平较低。",
                "mild": "你的回答显示，目前存在一定程度的低落情绪相关症状。",
                "moderate": "你的回答显示，目前抑郁相关症状已经达到值得认真关注的程度。",
                "moderately_severe": "你的回答显示，目前抑郁相关症状处在相对较高的水平。",
                "severe": "你的回答显示，目前抑郁相关症状水平较高。",
            }[severity_band]
            functional_impact = (
                "这种状态可能会影响动力、注意力、精力、自我照顾能力，以及维持工作、学习或人际关系的能力。"
            )
        elif assessment_type == "gad7":
            plain_meaning = {
                "minimal": "你的回答显示，目前焦虑相关症状水平较低。",
                "mild": "你的回答显示，目前存在一定程度的焦虑相关症状。",
                "moderate": "你的回答显示，焦虑可能已经比较明显地影响到日常生活。",
                "severe": "你的回答显示，目前焦虑相关症状水平较高。",
            }[severity_band]
            functional_impact = "这种状态可能表现为反复想很多、紧绷、坐立不安、易烦躁、难以专注，以及回避有压力的情境。"
        else:
            plain_meaning = {
                "none": "你的回答显示，目前失眠相关症状水平较低。",
                "subthreshold": "你的回答显示，目前存在一定睡眠困难，但还没有达到最严重的水平。",
                "moderate": "你的回答显示，睡眠问题可能已经明显影响到白天生活。",
                "severe": "你的回答显示，目前失眠相关痛苦或功能受损程度较高。",
            }[severity_band]
            functional_impact = "这种状态可能影响疲劳感、情绪、注意力、白天表现，以及你对睡眠的信心。"

        if severity_band in {"minimal", "none"}:
            care_consideration = (
                "从筛查结果看，症状相对较轻；但如果你的主观痛苦感或日常影响仍然明显，依然值得考虑寻求支持。"
            )
        elif severity_band in {"mild", "subthreshold"}:
            care_consideration = (
                "目前已经出现一些症状。轻量自我照顾、心理教育和持续观察可能会有帮助，尤其当这些状态与近期压力有关时。"
            )
        elif severity_band == "moderate":
            care_consideration = "这个程度可能已经影响日常生活。如果症状持续超过几周，或者已经干扰睡眠、工作、学习或关系，值得认真考虑专业支持。"
        else:
            care_consideration = "这个程度可能已经对日常功能造成较大影响，强烈建议认真考虑专业评估与支持。"
    else:
        common_disclaimer = "This is a screening result, not a diagnosis. It should be understood together with your recent stress, health, sleep, and daily functioning."
        if assessment_type == "phq9":
            plain_meaning = {
                "minimal": "Your responses suggest very low depressive symptoms right now.",
                "mild": "Your responses suggest some low-mood symptoms are present right now.",
                "moderate": "Your responses suggest a meaningful level of depressive symptoms right now.",
                "moderately_severe": "Your responses suggest a relatively high level of depressive symptoms right now.",
                "severe": "Your responses suggest a very high level of depressive symptoms right now.",
            }[severity_band]
            functional_impact = "This pattern can affect motivation, concentration, energy, self-care, and the ability to keep up with work, study, or relationships."
        elif assessment_type == "gad7":
            plain_meaning = {
                "minimal": "Your responses suggest anxiety symptoms are low on this screening right now.",
                "mild": "Your responses suggest some anxiety symptoms are present right now.",
                "moderate": "Your responses suggest anxiety may be affecting daily life in a noticeable way.",
                "severe": "Your responses suggest a high level of anxiety symptoms right now.",
            }[severity_band]
            functional_impact = "This pattern can show up as overthinking, tension, restlessness, irritability, trouble concentrating, and avoiding stressful situations."
        else:
            plain_meaning = {
                "none": "Your responses suggest insomnia symptoms are low on this screening right now.",
                "subthreshold": "Your responses suggest some sleep difficulty is present, but not at the highest levels.",
                "moderate": "Your responses suggest sleep problems may be meaningfully affecting daytime life.",
                "severe": "Your responses suggest a high level of insomnia-related distress or impairment right now.",
            }[severity_band]
            functional_impact = (
                "This pattern can affect fatigue, mood, concentration, daytime performance, and confidence about sleep."
            )

        if severity_band in {"minimal", "none"}:
            care_consideration = "Symptoms look relatively low on this screening. If distress or daily-life impact still feels significant, support can still be worth considering."
        elif severity_band in {"mild", "subthreshold"}:
            care_consideration = "Some symptoms are present. Gentle self-care, psychoeducation, and monitoring may help, especially if this feels linked to recent stress."
        elif severity_band == "moderate":
            care_consideration = "This level may be affecting daily life. Professional support is worth considering, especially if symptoms have lasted more than a couple of weeks or are interfering with sleep, work, study, or relationships."
        else:
            care_consideration = "This level may be having a substantial impact on daily functioning. A professional evaluation is strongly worth considering."

    safety_flags: list[AssessmentSafetyFlag] = []
    if assessment_type == "phq9" and answers and len(answers) >= 9 and answers[8] > 0:
        safety_flags.append(
            AssessmentSafetyFlag(
                code="self_harm_signal",
                message=(
                    "有一道题提示你可能存在与死亡或自我伤害相关的想法。量表本身不能准确判断即时危险。如果这些想法正在发生、在加重，或者你觉得很难控制，请立刻寻求线下紧急支持，不要只依赖量表结果。"
                    if language == "zh"
                    else "One answer suggests possible thoughts about death or self-harm. The questionnaire cannot judge immediate danger well. If these thoughts are current, worsening, or feel hard to control, seek urgent local support now and do not rely only on the questionnaire."
                ),
            )
        )

    return AssessmentInterpretation(
        plain_meaning=plain_meaning,
        functional_impact=functional_impact,
        care_consideration=care_consideration,
        disclaimer=common_disclaimer,
        needs_safety_followup=bool(safety_flags),
        safety_flags=safety_flags,
    )


def build_assessment_score(assessment_type: AssessmentType, score: int) -> AssessmentScore:
    validate_score(assessment_type, score)
    return AssessmentScore(
        assessment_type=assessment_type,
        score=score,
        severity_band=severity_for_score(assessment_type, score),
    )


def build_assessment_result(
    assessment_type: AssessmentType,
    *,
    score: int | None = None,
    answers: AssessmentAnswerSet | None = None,
    language: str = "en",
) -> AssessmentResult:
    if answers is not None:
        resolved_score = score_from_answers(assessment_type, answers)
        answer_values = answers.answers
    elif score is not None:
        validate_score(assessment_type, score)
        resolved_score = score
        answer_values = None
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either a total score or a full set of answers.",
        )

    severity_band = severity_for_score(assessment_type, resolved_score)
    guide = questionnaire_guide(assessment_type)
    interpretation = interpretation_for_result(
        assessment_type,
        resolved_score,
        severity_band,
        answers=answer_values,
        language=language,
    )
    return AssessmentResult(
        assessment_type=assessment_type,
        score=resolved_score,
        severity_band=severity_band,
        questionnaire_title=guide.title,
        timeframe=guide.timeframe,
        interpretation=interpretation,
    )
