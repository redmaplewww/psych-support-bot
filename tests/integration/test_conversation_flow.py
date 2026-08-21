from uuid import uuid4

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service


init_db()


def test_support_flow_returns_response() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="test-user", message="I feel stressed and want support"
            ),
            session=session,
        )

    assert result.mode in {
        "support",
        "assessment",
        "intervention",
        "planning",
        "crisis",
    }
    assert result.reply.text
    assert result.risk.risk_level in {"low", "elevated", "high", "critical"}


def test_crisis_flow_triggers_high_risk() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="test-user", message="I want to die and hurt myself"
            ),
            session=session,
        )

    assert result.mode == "crisis"
    assert result.risk.needs_crisis_mode is True


def test_chinese_crisis_flow_triggers_crisis_mode() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="test-user", message="我今晚想自杀，我手边有刀"
            ),
            session=session,
        )

    assert result.mode == "crisis"
    assert result.risk.risk_level == "critical"
    assert result.risk.needs_crisis_mode is True


def test_non_crisis_support_reply_is_not_treatment_heavy() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="test-user", message="我最近压力很大，总觉得心里发紧"
            ),
            session=session,
        )

    assert result.mode == "support"
    assert result.reply.text
    assert "治疗" not in result.reply.text


def test_conversation_can_start_and_complete_questionnaire() -> None:
    user_id = f"assessment-user-llm-flow-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="I want to take GAD-7"),
            session=session,
        )

        final = start
        for _ in range(7):
            final = conversation_service.respond(
                ConversationRequest(user_id=user_id, message="1"),
                session=session,
            )

    assert start.mode == "assessment"
    assert start.reply.text
    assert start.debug["source"] == "assessment_start"
    assert start.debug["llm_used"] is True

    assert final.mode == "assessment"
    assert final.debug["source"] == "assessment_result"
    assert final.debug["llm_used"] is True
    assert final.debug["assessment_score"] == 7
    assert final.reply.text


def test_assessment_followup_includes_supportive_interpretation() -> None:
    user_id = f"phq-followup-llm-flow-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="I want to take PHQ-9"),
            session=session,
        )
        final = None
        answers = [1, 1, 1, 1, 1, 1, 1, 1, 0]
        for value in answers:
            final = conversation_service.respond(
                ConversationRequest(user_id=user_id, message=str(value)),
                session=session,
            )

    assert final is not None
    assert final.debug["source"] == "assessment_result"
    assert final.debug["llm_used"] is True
    assert "some low-mood symptoms are present" in final.reply.text
    assert "motivation, concentration, energy" in final.reply.text
    assert "screening result" in final.reply.text
    assert "not a diagnosis" in final.reply.text


def test_consultation_metadata_is_exposed_for_consult_request() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="consult-user", message="请从不同流派会诊一下我的治疗方向"
            ),
            session=session,
        )

    assert result.debug["consultation_required"] is True
    assert result.debug["consultation_agents"] == [
        "CBT Agent",
        "Psychodynamic Agent",
        "Humanistic Agent",
        "ACT Agent",
        "DBT Agent",
    ]
    assert isinstance(result.debug["consultation_opinions"], list)


def test_process_metadata_is_exposed_for_open_exploration() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="process-user",
                message="我现在脑子很乱，不知道到底是工作还是关系让我这么累",
            ),
            session=session,
        )

    assert result.debug["interview_stage"] == "exploration"
    assert result.debug["question_strategy"] == "open"
    assert result.debug["challenge_allowed"] is False


def test_exhaustion_language_does_not_overtrigger_challenge() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="process-user-3",
                message="我最近总觉得很累，下班以后一句话都不想说。",
            ),
            session=session,
        )

    assert result.debug["interview_stage"] == "exploration"
    assert result.debug["question_strategy"] == "open"
    assert result.debug["challenge_allowed"] is False


def test_relational_avoidance_still_triggers_gentle_challenge() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="process-user-4",
                message="每次他一问我怎么了，我就更不想说。",
            ),
            session=session,
        )

    assert result.debug["interview_stage"] == "resistance_exploration"
    assert result.debug["question_strategy"] == "gentle_challenge"
    assert result.debug["challenge_allowed"] is True


def test_process_metadata_is_exposed_for_contradiction_style_message() -> None:
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(
                user_id="process-user-2",
                message="我明明很想辞职，但是又一直说服自己继续忍着",
            ),
            session=session,
        )

    assert result.debug["interview_stage"] == "hypothesis_testing"
    assert result.debug["question_strategy"] == "looping"
    assert result.debug["challenge_allowed"] is True


def test_non_assessment_message_during_assessment_falls_back_to_support() -> None:
    """活跃评估期间发送非评估消息应回落到对话图，不应当作无效答案。"""
    user_id = f"non-assessment-during-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        assert start.mode == "assessment"
        assert start.debug["source"] == "assessment_start"

        # 在量表进行中发送一句支持意图消息（不是数字也不是退出指令）
        mid = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我最近还感到很焦虑"),
            session=session,
        )

    # 应当回落到对话图，不进入 invalid_answer 分支
    assert mid is not None
    assert mid.debug.get("source") not in {"questionnaire_progress", "assessment_result"}
    assert mid.mode in {"support", "intervention", "planning", "crisis"}
    assert mid.reply.text


def test_help_message_during_assessment_does_not_continue_questionnaire() -> None:
    """活跃评估期间发送 help 意图不应让评估继续推进。"""
    user_id = f"help-during-assessment-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 ISI"),
            session=session,
        )
        assert start.mode == "assessment"

        # 发送帮助消息
        help_resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="你能帮我吗"),
            session=session,
        )

        # 帮助回复不能是量表提示或要求输入数字的错误提示
        text = help_resp.reply.text
        assert "请回复一个数字" not in text
        assert "Please reply with a number" not in text
        # 也不能推进到下一题（source 不是 questionnaire_progress）
        assert help_resp.debug.get("source") != "questionnaire_progress"


def test_numeric_answer_during_assessment_proceeds_normally() -> None:
    """回归测试：评估期间发送数字答案不应被 detect_mode 拦截。

    旧实现将 detect_mode 检查放在 parse_questionnaire_answer 之前，
    导致 detect_mode("2") 返回 "support"（纯数字无关键词匹配），
    进而 return None 中断评估流程。此测试确保数字答案正常推进量表。
    """
    user_id = f"numeric-answer-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        assert start.mode == "assessment"
        assert start.debug["source"] == "assessment_start"

        # 发送数字答案 "2"（PHQ-9 有效范围 0-3）
        step1 = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="2"),
            session=session,
        )

    # 数字答案应被正常接受，不应回落到对话图
    assert step1 is not None
    assert step1.debug["source"] == "questionnaire_progress"
    assert step1.mode == "assessment"
    assert "请回复一个数字" not in step1.reply.text
    assert "Please reply with a number" not in step1.reply.text


def test_numeric_zero_answer_during_assessment_proceeds_normally() -> None:
    """回归测试：评估期间发送 "0"（最低分）也应正常推进。"""
    user_id = f"numeric-zero-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="PHQ-9"),
            session=session,
        )
        step1 = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="0"),
            session=session,
        )

    assert step1 is not None
    assert step1.debug["source"] == "questionnaire_progress"
    assert step1.mode == "assessment"


def test_verbal_option_answer_during_assessment_proceeds_normally() -> None:
    """回归测试：评估期间发送文字选项（如"没有"）也应正常推进。"""
    user_id = f"verbal-option-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="GAD-7"),
            session=session,
        )
        step1 = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="没有"),
            session=session,
        )

    assert step1 is not None
    assert step1.debug["source"] == "questionnaire_progress"
    assert step1.mode == "assessment"
