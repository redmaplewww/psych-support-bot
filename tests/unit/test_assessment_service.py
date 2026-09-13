import pytest
from fastapi import HTTPException

from psych_support_bot.domain.assessments.schemas import AssessmentAnswerSet
from psych_support_bot.domain.assessments.service import (
    build_assessment_followup_reply,
    build_assessment_result,
    detect_questionnaire_request,
    questionnaire_guide,
    score_from_answers,
)


def test_questionnaire_guide_exposes_instructions() -> None:
    guide = questionnaire_guide("gad7")

    assert guide.code == "gad7"
    assert guide.instructions
    assert len(guide.items) == 7


def test_score_from_answers_validates_length() -> None:
    with pytest.raises(HTTPException):
        score_from_answers("phq9", AssessmentAnswerSet(answers=[0, 1]))


def test_score_from_answers_validates_range() -> None:
    with pytest.raises(HTTPException):
        score_from_answers("gad7", AssessmentAnswerSet(answers=[0, 1, 2, 3, 4, 0, 1]))


def test_assessment_result_interprets_phq9_safety_signal() -> None:
    result = build_assessment_result(
        "phq9",
        answers=AssessmentAnswerSet(answers=[0, 1, 1, 1, 0, 0, 1, 0, 1]),
    )

    assert result.score == 5
    assert result.severity_band == "mild"
    assert result.interpretation.needs_safety_followup is True


def test_assessment_result_supports_isi_boundaries() -> None:
    result = build_assessment_result("isi", score=15, language="en")

    assert result.severity_band == "moderate"
    assert "sleep" in result.interpretation.functional_impact.lower()


def test_detect_questionnaire_request_supports_natural_chinese_phrases() -> None:
    assert detect_questionnaire_request("我想测一下抑郁程度") == "phq9"
    assert detect_questionnaire_request("我想做焦虑量表") == "gad7"
    assert detect_questionnaire_request("我想做失眠量表") == "isi"


def test_questionnaire_guide_is_localized_for_chinese_ui() -> None:
    guide = questionnaire_guide("phq9")

    assert "抑郁" in guide.title
    assert "过去两周" in guide.timeframe
    assert guide.options[0].label == "完全没有"
    assert any("做事时提不起兴趣" in item for item in guide.items)


def test_followup_reply_includes_interpretation_and_disclaimer() -> None:
    """build_assessment_followup_reply 是确定性模板：解读文案与免责声明逐字拼接。

    （集成层只断言 LLM 转述契约——分数与筛查声明出现；逐字文案在此覆盖。）
    """
    result = build_assessment_result("phq9", answers=AssessmentAnswerSet(answers=[1, 1, 1, 1, 1, 1, 1, 1, 0]))
    reply = build_assessment_followup_reply(result, user_message="I want to take PHQ-9")

    # 分数与严重程度
    assert "8" in reply
    assert "mild" in reply
    # 解读文案（plain_meaning / functional_impact）
    assert "some low-mood symptoms are present" in reply
    assert "motivation, concentration, energy" in reply
    # 筛查而非诊断
    assert "not a diagnosis" in reply
    # 结尾邀请继续聊
    assert "talk more about" in reply


def test_followup_reply_is_localized_for_chinese() -> None:
    result = build_assessment_result(
        "phq9", answers=AssessmentAnswerSet(answers=[1, 1, 1, 1, 1, 1, 1, 1, 0]), language="zh"
    )
    reply = build_assessment_followup_reply(result, user_message="我想做PHQ-9")

    assert "感谢你完成" in reply
    assert "得分是8" in reply
    assert "轻度" in reply
