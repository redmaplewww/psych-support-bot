import asyncio

from psych_support_bot.api.lifecycle import UserLifecycleMiddleware


def test_same_user_deletion_waits_for_inflight_response_completion():
    async def scenario():
        release_write = asyncio.Event()
        write_started = asyncio.Event()
        events = []

        async def downstream(scope, receive, send):
            events.append(("start", scope["path"]))
            if scope["path"].endswith("/write"):
                write_started.set()
                await release_write.wait()
            events.append(("finish", scope["path"]))

        middleware = UserLifecycleMiddleware(downstream)

        def scope(path):
            return {
                "type": "http",
                "path": path,
                "method": "POST",
                "query_string": b"user_id=owner",
                "headers": [],
            }

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_):
            return None

        write_task = asyncio.create_task(middleware(scope("/v1/write"), receive, send))
        await write_started.wait()
        delete_task = asyncio.create_task(middleware(scope("/v1/delete"), receive, send))
        await asyncio.sleep(0)
        assert ("start", "/v1/delete") not in events

        release_write.set()
        await asyncio.gather(write_task, delete_task)
        assert events == [
            ("start", "/v1/write"),
            ("finish", "/v1/write"),
            ("start", "/v1/delete"),
            ("finish", "/v1/delete"),
        ]

    asyncio.run(scenario())
