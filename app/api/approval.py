import uuid

from fastapi import APIRouter, status

from app.auth.rbac import ensure_can_view
from app.deps import CurrentUser, DbSession
from app.schemas.syllabus import ActivityOut, CommentIn, TransitionIn
from app.services import approval, syllabi

router = APIRouter(tags=["approval"])


@router.post(
    "/syllabi/{syllabus_id}/transitions", response_model=ActivityOut, status_code=status.HTTP_201_CREATED
)
async def transition(syllabus_id: uuid.UUID, body: TransitionIn, principal: CurrentUser, session: DbSession):
    syllabus = await syllabi.get_syllabus(session, syllabus_id, lock=True)
    await ensure_can_view(session, principal, syllabus)
    event = await approval.transition(session, principal, syllabus, body.to_stage, body.comment)
    await session.commit()
    return next(a for a in await approval.activity(session, syllabus_id) if a["id"] == event.id)


@router.post(
    "/syllabi/{syllabus_id}/comments", response_model=ActivityOut, status_code=status.HTTP_201_CREATED
)
async def comment(syllabus_id: uuid.UUID, body: CommentIn, principal: CurrentUser, session: DbSession):
    syllabus = await syllabi.get_syllabus(session, syllabus_id)
    await ensure_can_view(session, principal, syllabus)
    event = await approval.add_comment(session, principal, syllabus, body.text)
    await session.commit()
    return next(a for a in await approval.activity(session, syllabus_id) if a["id"] == event.id)


@router.get("/syllabi/{syllabus_id}/activity", response_model=list[ActivityOut])
async def activity(syllabus_id: uuid.UUID, principal: CurrentUser, session: DbSession):
    await ensure_can_view(session, principal, await syllabi.get_syllabus(session, syllabus_id))
    return await approval.activity(session, syllabus_id)
