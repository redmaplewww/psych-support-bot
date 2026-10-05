"""Serialize one user's HTTP requests through streaming response completion.

The shipped server runs one worker. In particular, deletion must wait for an
already-running conversation/analysis to commit before erasing its records.
Multi-process deployments need a shared lifecycle lock before scaling out.
"""

import asyncio
import json
from urllib.parse import parse_qs
from weakref import WeakValueDictionary

from fastapi import HTTPException
from starlette.responses import JSONResponse

from psych_support_bot.api.auth import decode_access_token
from psych_support_bot.infra.config.settings import get_settings


class UserLifecycleMiddleware:
    def __init__(self, app):
        self.app = app
        self.locks = WeakValueDictionary()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        query = parse_qs(scope.get("query_string", b"").decode())
        subject = (query.get("user_id") or [headers.get("x-user-id", "")])[0]
        replay = []
        if "application/json" in headers.get("content-type", ""):
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                replay.append(message)
                body.extend(message.get("body", b""))
                if len(body) > 1024 * 1024:
                    await JSONResponse({"detail": "JSON body too large"}, status_code=413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    declared = payload.get("user_id") or (
                        payload.get("username") if scope["path"].startswith("/v1/auth/") else None
                    )
                    if declared:
                        if subject and subject != declared:
                            await JSONResponse({"detail": "Conflicting user identities"}, status_code=403)(
                                scope, receive, send
                            )
                            return
                        subject = declared
            except (ValueError, UnicodeDecodeError):
                pass
        if get_settings().auth_enabled and not scope["path"].startswith("/v1/auth/"):
            try:
                subject = decode_access_token(headers.get("authorization", "").removeprefix("Bearer ").strip())
            except HTTPException:
                subject = ""  # Actual authentication guard returns the 401.

        async def replay_receive():
            return replay.pop(0) if replay else await receive()

        if not isinstance(subject, str) or not subject:
            await self.app(scope, replay_receive, send)
            return
        key = (asyncio.get_running_loop(), subject)
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        async with lock:
            await self.app(scope, replay_receive, send)
