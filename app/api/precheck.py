import uuid

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import PlainTextResponse

from app.deps import CurrentUser, DbSession
from app.schemas.work import PrecheckOut, PrecheckStarted
from app.services import precheck

router = APIRouter(tags=["precheck"])


@router.post("/precheck", response_model=PrecheckStarted, status_code=status.HTTP_202_ACCEPTED)
async def start(
    principal: CurrentUser, session: DbSession, course_id: uuid.UUID = Form(...), file: UploadFile = File(...)
):
    """Private AI check before submission. Nothing is stored; the result expires after PRECHECK_TTL_S."""
    job_id = await precheck.start(
        session, principal, course_id, file.filename or "syllabus.docx", await file.read()
    )
    return PrecheckStarted(job_id=job_id)


@router.get("/precheck/{job_id}", response_model=PrecheckOut)
async def get(job_id: str, principal: CurrentUser):
    return await precheck.get(principal, job_id)


@router.get("/precheck/{job_id}/report.txt", response_class=PlainTextResponse)
async def report(job_id: str, principal: CurrentUser):
    state = await precheck.get(principal, job_id)
    return PlainTextResponse(
        precheck.report_text(state),
        headers={"Content-Disposition": 'attachment; filename="private-ai-review.txt"'},
    )
