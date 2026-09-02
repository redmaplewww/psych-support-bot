from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from psych_support_bot.ai.schemas.messages import (
    ConversationRequest,
    ConversationResponse,
    MessageHistoryItem,
    RiskEventItem,
    SessionHistoryItem,
)
from psych_support_bot.infra.db.repositories import (
    get_session_messages,
    get_user_risk_events,
    get_user_sessions,
)
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.services.conversation import conversation_service

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.post("/respond", response_model=ConversationResponse)
def respond(
    payload: ConversationRequest,
    session: Session = Depends(get_db_session),
) -> ConversationResponse:
    return conversation_service.respond(payload, session=session)


@router.get("/history", response_model=list[SessionHistoryItem])
def get_history(
    user_id: str,
    limit: int = 20,
    session: Session = Depends(get_db_session),
) -> list[SessionHistoryItem]:
    records = get_user_sessions(session, user_id=user_id, limit=limit)
    return [
        SessionHistoryItem(
            session_id=record.id,
            mode=record.mode,
            risk_level=record.risk_level,
            summary=record.summary,
            created_at=record.created_at.isoformat(),
        )
        for record in records
    ]


@router.get("/{session_id}/messages", response_model=list[MessageHistoryItem])
def get_messages(
    session_id: str,
    session: Session = Depends(get_db_session),
) -> list[MessageHistoryItem]:
    records = get_session_messages(session, session_id=session_id)
    return [
        MessageHistoryItem(
            role=record.role,
            content=record.content,
            safety_flag=record.safety_flag,
            created_at=record.created_at.isoformat(),
        )
        for record in records
    ]


@router.get("/risk-events", response_model=list[RiskEventItem])
def get_risk_events(
    user_id: str,
    limit: int = 20,
    session: Session = Depends(get_db_session),
) -> list[RiskEventItem]:
    records = get_user_risk_events(session, user_id=user_id, limit=limit)
    return [
        RiskEventItem(
            session_id=record.session_id,
            risk_level=record.risk_level,
            risk_reason=record.risk_reason,
            created_at=record.created_at.isoformat(),
        )
        for record in records
    ]
