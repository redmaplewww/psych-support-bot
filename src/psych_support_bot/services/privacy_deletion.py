"""Durable, retryable historical trace erasure, separate from local deletion.

Langfuse deletes asynchronously: a successful DELETE is not confirmation. Jobs
are re-queried after an ingestion grace period and only then clear identifiers.
No payloads are fetched (fields=core), and provider errors are never persisted.
"""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import ConversationSession, PrivacyDeletionJob
from psych_support_bot.infra.db.session import SessionLocal

logger = logging.getLogger(__name__)
INGESTION_GRACE = timedelta(minutes=30)
VERIFY_INTERVAL = timedelta(minutes=15)
REQUEST_OPTIONS = {"timeout_in_seconds": 10, "max_retries": 0}


def enqueue_external_deletion(session: Session, user_id: str) -> PrivacyDeletionJob:
    sessions = [row[0] for row in session.query(ConversationSession.id).filter(ConversationSession.user_id == user_id)]
    needed = get_settings().langfuse_delete_history
    job = PrivacyDeletionJob(
        id=uuid4().hex,
        user_id=user_id if needed else None,
        session_ids_json=json.dumps(sessions) if needed else "[]",
        status="pending" if needed else "not_required",
    )
    session.add(job)
    return job


def _trace_api():
    from psych_support_bot.infra.telemetry.tracing import get_langfuse

    client = get_langfuse()
    return client.api.trace if client is not None else None


def _matching_trace_ids(api, job: PrivacyDeletionJob) -> set[str]:
    # Collect before deleting so asynchronous deletes cannot shift pagination.
    from psych_support_bot.infra.telemetry.tracing import telemetry_subject_id

    if not job.user_id:
        raise RuntimeError("deletion_identity_missing")
    trace_ids: set[str] = set()
    session_ids = json.loads(job.session_ids_json)
    filters = [
        # Raw filters cover traces created before pseudonymised analytics.
        {"user_id": job.user_id},
        {"user_id": telemetry_subject_id("user", job.user_id)},
        *({"session_id": sid} for sid in session_ids),
        *({"session_id": telemetry_subject_id("session", sid)} for sid in session_ids),
    ]
    for query in filters:
        for page in range(1, 101):
            result = api.list(page=page, limit=100, fields="core", request_options=REQUEST_OPTIONS, **query)
            trace_ids.update(item.id for item in result.data)
            if page >= result.meta.total_pages:
                break
        else:
            raise RuntimeError("trace_page_limit")  # Never report partial work as complete.
    return trace_ids


def process_deletion_job(session: Session, job: PrivacyDeletionJob, api, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    updated = job.updated_at.replace(tzinfo=UTC) if job.updated_at.tzinfo is None else job.updated_at
    if job.status == "verifying" and now - updated < VERIFY_INTERVAL:
        return
    if api is None:
        job.error_code = "langfuse_credentials_required"
        session.commit()
        return
    try:
        trace_ids = sorted(_matching_trace_ids(api, job))
        for offset in range(0, len(trace_ids), 100):
            api.delete_multiple(trace_ids=trace_ids[offset : offset + 100], request_options=REQUEST_OPTIONS)
        created = job.created_at.replace(tzinfo=UTC) if job.created_at.tzinfo is None else job.created_at
        if not trace_ids and now - created >= INGESTION_GRACE:
            job.status = "linked_traces_deleted"
            job.user_id = None
            job.session_ids_json = "[]"
        else:
            job.status = "verifying"
        job.error_code = ""
    except Exception:  # noqa: BLE001 — retry durable jobs; never store payload-bearing provider errors
        job.error_code = "langfuse_cleanup_failed"
        logger.warning("Historical trace deletion requires retry")
    job.updated_at = now
    session.commit()


def process_pending_deletions() -> None:
    with SessionLocal() as session:
        jobs = (
            session.query(PrivacyDeletionJob)
            .filter(PrivacyDeletionJob.status.in_(("pending", "verifying")))
            .limit(10)
            .all()
        )
        if not jobs:
            return
        api = _trace_api()
        for job in jobs:
            process_deletion_job(session, job, api)


async def deletion_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(process_pending_deletions)
        except Exception:  # noqa: BLE001 — database/provider outages are retried on the next tick
            logger.warning("Privacy deletion worker will retry")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=60)
