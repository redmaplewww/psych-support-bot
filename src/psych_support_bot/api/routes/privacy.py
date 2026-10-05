"""A random erasure receipt can be checked after account credentials are gone."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import PrivacyDeletionJob
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])


@router.get("/deletions/{receipt}")
def deletion_status(receipt: str, session: Session = Depends(get_db_session)) -> dict:
    job = session.get(PrivacyDeletionJob, receipt)
    if job is None:
        raise HTTPException(status_code=404, detail="Deletion receipt not found")
    return {"local_data": "deleted", "external_cleanup": job.status, "error_code": job.error_code}
