import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool

from psych_support_bot.ai.schemas.messages import (
    ConversationRequest,
    ConversationResponse,
    MessageHistoryItem,
    RiskEventItem,
    SessionHistoryItem,
)
from psych_support_bot.api.auth import request_user_id, require_auth
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.repositories import (
    get_session_messages,
    get_user_risk_events,
    get_user_sessions,
    session_belongs_to,
)
from psych_support_bot.infra.db.session import get_db_session
from psych_support_bot.services.conversation import conversation_service

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _verify_session_ownership(payload: ConversationRequest, request: Request, session: Session) -> None:
    """认证开启时校验客户端自报 session_id 的归属（Mimosa scan-c02b1f85311d 修复）。

    respond 允许客户端自带 session_id 续聊，历史消息原文会进 LLM 上下文——
    归属不校验即可借 LLM 复述他人对话（比 get_messages 的直读更隐蔽的外泄
    通道）。游客模式（auth_enabled=false）跳过：身份本就客户端自报，无归属
    可校验。不带 session_id 的新会话无需校验。404 语义：不暴露他人会话
    存在性。同时这里提前完成 request_user_id 的 token/身份一致性校验。
    """
    if not payload.session_id or not get_settings().auth_enabled:
        return
    user_id = request_user_id(request, payload.user_id)
    if not session_belongs_to(session, payload.session_id, user_id):
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/respond", response_model=ConversationResponse)
def respond(
    payload: ConversationRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ConversationResponse:
    _verify_session_ownership(payload, request, session)
    payload.user_id = request_user_id(request, payload.user_id)
    return conversation_service.respond(payload, session=session)


@router.post("/respond/stream")
async def respond_stream(
    payload: ConversationRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """LLM→TTS 句子级流式（SSE）。事件帧：data {type: sentence|revise|final}。

    - sentence：LLM 生成中切出的完整句（已过逐句规则扫描），前端可即时朗读/渲染
    - revise：全文 safety_reviewer 与已朗读内容不一致，前端停读并用 text 替换气泡
    - final：完整 ConversationResponse（结构化，与 /respond 同形状），收尾用
    同步服务生成器经 threadpool 驱动；X-Accel-Buffering:no 关代理缓冲保证逐帧下发。
    """
    _verify_session_ownership(payload, request, session)
    payload.user_id = request_user_id(request, payload.user_id)
    gen = conversation_service.respond_stream(payload, session=session)

    async def _sse():
        async for event in iterate_in_threadpool(gen):
            etype = event.get("type")
            if etype == "final":
                data = {"type": "final", "response": event["response"].model_dump(mode="json")}
            else:
                data = dict(event.items())
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history", response_model=list[SessionHistoryItem])
def get_history(
    request: Request,
    user_id: str = "",
    limit: int = 20,
    session: Session = Depends(get_db_session),
) -> list[SessionHistoryItem]:
    user_id = request_user_id(request, user_id)
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
    request: Request,
    user_id: str = "",
    _sub: str = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> list[MessageHistoryItem]:
    """会话内消息（本人可见；认证开启时 require_auth + sessions.user_id 归属校验防越权）。

    Mimosa scan-c02b1f85311d 修复：此前仅按路径 session_id 过滤、无认证
    无归属绑定——AUTH_ENABLED=true 部署下未认证即可读任意会话的对话原文。
    会话不存在或不属于当前认证用户一律 404（不区分两态，防会话枚举）。
    游客模式（auth_enabled=false）不做归属校验：身份本就客户端自报，
    保持既有行为（前端 /messages 请求不带 user_id 参数，不能引入 422）。
    """
    if get_settings().auth_enabled:
        owner = request_user_id(request, user_id)
        if not session_belongs_to(session, session_id, owner):
            raise HTTPException(status_code=404, detail="Session not found")
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
    request: Request,
    user_id: str = "",
    limit: int = 20,
    session: Session = Depends(get_db_session),
) -> list[RiskEventItem]:
    user_id = request_user_id(request, user_id)
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
