"""用户注册与登录（JWT 认证的领域服务）。

username 注册后即成为 users.id / 全库数据关联键——既有客户端自报
user_id 的历史数据与匿名使用不受影响；商业化部署开启 AUTH_ENABLED
后新用户走 username/password 登录。

埋点只记动作（auth_register / auth_login / auth_login_failed），
不记凭据内容与失败原因细节（防用户枚举：登录失败统一 401）。
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import create_access_token, hash_password, verify_password
from psych_support_bot.infra.db.models import User, UserCredential
from psych_support_bot.infra.db.repositories import record_usage_event

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
_MIN_PASSWORD_LENGTH = 8


def _validate_credentials(username: str, password: str) -> None:
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-64 chars of letters, digits, '_' or '-'.",
        )
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")


def register_user(session: Session, username: str, password: str) -> dict[str, str]:
    _validate_credentials(username, password)
    if session.get(UserCredential, username) is not None or session.get(User, username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")
    session.add(User(id=username))
    session.add(UserCredential(user_id=username, username=username, password_hash=hash_password(password)))
    session.commit()
    record_usage_event(session, username, "auth_register")
    session.commit()
    return {"user_id": username, "access_token": create_access_token(username), "token_type": "bearer"}


def authenticate_user(session: Session, username: str, password: str) -> dict[str, str]:
    credential = session.get(UserCredential, username)
    if credential is None or not verify_password(password, credential.password_hash):
        record_usage_event(session, username or "unknown", "auth_login_failed")
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    record_usage_event(session, username, "auth_login")
    session.commit()
    return {"user_id": username, "access_token": create_access_token(username), "token_type": "bearer"}
