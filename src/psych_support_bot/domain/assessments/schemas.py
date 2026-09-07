from typing import Literal

from pydantic import BaseModel, Field

AssessmentType = Literal["phq9", "gad7", "isi"]


class AssessmentScore(BaseModel):
    assessment_type: AssessmentType
    score: int = Field(..., ge=0)
    severity_band: str


class QuestionnaireOption(BaseModel):
    value: int
    label: str


class QuestionnaireGuide(BaseModel):
    code: AssessmentType
    title: str
    timeframe: str
    purpose: str
    instructions: list[str]
    options: list[QuestionnaireOption]
    items: list[str]
    # 须知页数据（20260904）：进入问卷前展示并勾选确认，服务端强校验。
    disclaimer_points: list[str] = Field(default_factory=list)
    disclaimer_version: str = ""


class AssessmentAnswerSet(BaseModel):
    answers: list[int] = Field(..., min_length=1, max_length=20)


class AssessmentSafetyFlag(BaseModel):
    code: str
    message: str


class AssessmentInterpretation(BaseModel):
    plain_meaning: str
    functional_impact: str
    care_consideration: str
    disclaimer: str
    needs_safety_followup: bool = False
    safety_flags: list[AssessmentSafetyFlag] = Field(default_factory=list)


class AssessmentResult(AssessmentScore):
    questionnaire_title: str
    timeframe: str
    interpretation: AssessmentInterpretation


class QuestionnaireSessionItem(BaseModel):
    index: int
    text: str
    options: list[QuestionnaireOption]


class QuestionnaireSessionStartRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    assessment_type: AssessmentType
    # 须知确认（20260904 起每次评估必须）：前端勾选须知页后置 true。
    # 服务端强校验（评估有临床语义，确认必须落后端），并打 assessment_consent
    # 埋点（只记 type + 条款版本）。
    consent_acknowledged: bool = False
    disclaimer_version: str = Field("", max_length=32)


class QuestionnaireSessionAnswerRequest(BaseModel):
    value: int = Field(..., ge=0, le=4)


class QuestionnaireSessionView(BaseModel):
    session_id: str
    user_id: str
    assessment_type: AssessmentType
    questionnaire_title: str
    timeframe: str
    status: str
    current_index: int
    total_items: int
    answers: list[int] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    next_item: QuestionnaireSessionItem | None = None


class QuestionnaireSessionResult(BaseModel):
    session: QuestionnaireSessionView
    result: AssessmentResult
