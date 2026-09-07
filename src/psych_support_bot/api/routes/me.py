"""「我」页路由：数据导出 / 清空历史记录 / 注销账号。

破坏性操作的确认模型（两步式）：
1. POST /v1/me/confirm-intent {action} → 签发 10 分钟有效的 HMAC 确认令牌；
2. DELETE /records 或 /account 携带 confirm_token → 验签通过才执行。
令牌绑定 user_id + action，防止跨操作/跨用户误用；密钥复用 JWT_SECRET_KEY
（settings 校验器保证始终有值）。
"""

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import request_user_id
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.me_repositories import (
    clear_user_records,
    delete_user_account,
    export_user_data,
    me_summary,
)
from psych_support_bot.infra.db.repositories import record_usage_event
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/me", tags=["me"])

CONFIRM_ACTIONS = ("clear_records", "delete_account")
CONFIRM_TOKEN_TTL_SECONDS = 600


class ConfirmIntentRequest(BaseModel):
    user_id: str = Field("", max_length=64)
    action: str = Field(..., pattern="^(clear_records|delete_account)$")


class ConfirmIntentResponse(BaseModel):
    action: str
    confirm_token: str
    expires_in_seconds: int


def _sign_intent(user_id: str, action: str, expires_at: int) -> str:
    secret = get_settings().jwt_secret_key.encode()
    message = f"me-confirm|{user_id}|{action}|{expires_at}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{expires_at}.{digest}"


def _verify_intent_token(user_id: str, action: str, token: str) -> None:
    try:
        expires_at_raw, _digest = token.split(".", 1)
        expires_at = int(expires_at_raw)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="确认令牌格式无效，请重新发起操作。") from exc
    if expires_at < int(time.time()):
        raise HTTPException(status_code=403, detail="确认令牌已过期，请重新发起操作。")
    expected = _sign_intent(user_id, action, expires_at)
    if not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="确认令牌校验失败，请重新发起操作。")


@router.post("/confirm-intent", response_model=ConfirmIntentResponse)
def create_confirm_intent(
    payload: ConfirmIntentRequest,
    request: Request,
) -> ConfirmIntentResponse:
    """为破坏性操作签发短时效确认令牌（第二步 DELETE 时验签）。"""
    user_id = request_user_id(request, payload.user_id)
    if payload.action not in CONFIRM_ACTIONS:
        raise HTTPException(status_code=422, detail="Unknown confirm action.")
    expires_at = int(time.time()) + CONFIRM_TOKEN_TTL_SECONDS
    return ConfirmIntentResponse(
        action=payload.action,
        confirm_token=_sign_intent(user_id, payload.action, expires_at),
        expires_in_seconds=CONFIRM_TOKEN_TTL_SECONDS,
    )


class MeSummaryResponse(BaseModel):
    user_id: str
    created_at: str | None
    counts_30d: dict[str, int]


@router.get("/summary", response_model=MeSummaryResponse)
def get_me_summary(
    request: Request,
    user_id: str = Query(""),
    session: Session = Depends(get_db_session),
) -> MeSummaryResponse:
    """「我」页头部：账号事实 + 最近 30 天三类动作计数。"""
    user_id = request_user_id(request, user_id)
    summary = me_summary(session, user_id)
    return MeSummaryResponse(**summary)


@router.get("/export")
def export_me_data(
    request: Request,
    user_id: str = Query(""),
    session: Session = Depends(get_db_session),
) -> JSONResponse:
    """全量导出（协议承诺的数据可携带权）：测评/练习/打卡/对话一次带回。"""
    user_id = request_user_id(request, user_id)
    data = export_user_data(session, user_id)
    data["counts"] = {
        "assessments": len(data["assessments"]),
        "exercise_records": len(data["exercise_records"]),
        "checkins": len(data["checkins"]),
        "conversations": len(data["conversations"]),
        "messages": len(data["messages"]),
    }
    record_usage_event(session, user_id, "data_exported", **data["counts"])
    session.commit()
    today = data["exported_at"][:10].replace("-", "")
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="psych-support-export-{today}.json"'},
    )


@router.delete("/records")
def clear_my_records(
    request: Request,
    user_id: str = Query(""),
    confirm_token: str = Query(..., min_length=8),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    """清空主动记录（测评/练习/打卡），保留聊天与账号。需确认令牌。"""
    user_id = request_user_id(request, user_id)
    _verify_intent_token(user_id, "clear_records", confirm_token)
    counts = clear_user_records(session, user_id)
    record_usage_event(session, user_id, "records_cleared", **counts)
    session.commit()
    return {"status": "cleared", "deleted": counts}


@router.delete("/account")
def delete_my_account(
    request: Request,
    user_id: str = Query(""),
    confirm_token: str = Query(..., min_length=8),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    """注销：级联删除身份与全部数据（含聊天）。需确认令牌。"""
    user_id = request_user_id(request, user_id)
    _verify_intent_token(user_id, "delete_account", confirm_token)
    counts = delete_user_account(session, user_id)
    # 审计事件会随用户数据一并删除（usage_events 属于删除范围），
    # 只保留服务端日志作为注销动作的痕迹。
    session.commit()
    return {"status": "account_deleted", "deleted": counts}
