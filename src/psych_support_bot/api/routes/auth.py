"""认证端点：注册 / 登录（签发 JWT）。

独立于数据端点，始终可用（注册登录本身不要求携带 token）。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.domain.auth.service import authenticate_user, register_user
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=AuthResponse)
def register(payload: AuthRequest, session: Session = Depends(get_db_session)) -> AuthResponse:
    return AuthResponse(**register_user(session, payload.username, payload.password))


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthRequest, session: Session = Depends(get_db_session)) -> AuthResponse:
    return AuthResponse(**authenticate_user(session, payload.username, payload.password))
