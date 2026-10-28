import uuid

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, DbSession
from app.enums import AiStatus
from app.models import Course, Program, RubricVersion, School, Syllabus, UploadFolder, User
from app.models.org import course_instructors
from app.schemas.catalog import (
    CourseOut,
    FolderIn,
    FolderOut,
    FolderPatch,
    ProfessorOut,
    ProgramOut,
    RubricOut,
    SchoolOut,
)
from app.services.sessions import ensure_can_manage

router = APIRouter(tags=["catalog"])


@router.get("/schools", response_model=list[SchoolOut])
async def schools(_: CurrentUser, session: DbSession):
    return list(await session.scalars(sa.select(School).order_by(School.name)))


@router.get("/programs", response_model=list[ProgramOut])
async def programs(_: CurrentUser, session: DbSession, school_id: uuid.UUID | None = None):
    query = sa.select(Program).order_by(Program.name)
    if school_id:
        query = query.where(Program.school_id == school_id)
    return [
        ProgramOut.model_validate(p).model_copy(update={"has_passport": bool(p.passport_text)})
        for p in await session.scalars(query)
    ]


@router.get("/courses", response_model=list[CourseOut])
async def courses(_: CurrentUser, session: DbSession, program_id: uuid.UUID | None = None):
    query = sa.select(Course).order_by(Course.name)
    if program_id:
        query = query.where(Course.program_id == program_id)
    return [
        CourseOut(
            id=c.id,
            slug=c.slug,
            name=c.name,
            program_id=c.program_id,
            credits=c.credits,
            lead_id=c.lead_id,
            instructor_ids=[u.id for u in c.instructors],
        )
        for c in await session.scalars(query)
    ]


@router.get("/professors", response_model=list[ProfessorOut])
async def professors(_: CurrentUser, session: DbSession):
    teaching = sa.select(course_instructors.c.user_id).union(sa.select(Course.lead_id))
    return list(await session.scalars(sa.select(User).where(User.id.in_(teaching)).order_by(User.name)))


@router.get("/rubric", response_model=RubricOut)
async def rubric(_: CurrentUser, session: DbSession, code: str | None = None):
    query = sa.select(RubricVersion)
    query = (
        query.where(RubricVersion.code == code) if code else query.where(RubricVersion.is_active.is_(True))
    )
    found = await session.scalar(query)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rubric not found")
    return found


async def _folder_out(session: DbSession, folder: UploadFolder) -> FolderOut:
    files, checked = (
        await session.execute(
            sa.select(
                sa.func.count(Syllabus.id),
                sa.func.count(Syllabus.id).filter(
                    Syllabus.ai_status.in_([AiStatus.checked, AiStatus.needs_revision])
                ),
            ).where(Syllabus.folder_id == folder.id)
        )
    ).one()
    return FolderOut(
        id=folder.id,
        school_id=folder.school_id,
        path=folder.path,
        semester=folder.semester,
        files=files,
        checked=checked,
    )


@router.get("/schools/{school_id}/folders", response_model=list[FolderOut])
async def list_folders(school_id: uuid.UUID, principal: CurrentUser, session: DbSession):
    if not (principal.university_wide or school_id in principal.school_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found")
    folders = await session.scalars(
        sa.select(UploadFolder).where(UploadFolder.school_id == school_id).order_by(UploadFolder.path)
    )
    return [await _folder_out(session, f) for f in folders]


@router.post("/schools/{school_id}/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(school_id: uuid.UUID, body: FolderIn, principal: CurrentUser, session: DbSession):
    ensure_can_manage(principal, school_id)
    if await session.get(School, school_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found")
    folder = UploadFolder(school_id=school_id, path=body.path.strip(), semester=body.semester)
    session.add(folder)
    try:
        await session.commit()
    except sa.exc.IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "This folder already exists") from exc
    return await _folder_out(session, folder)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(folder_id: uuid.UUID, body: FolderPatch, principal: CurrentUser, session: DbSession):
    folder = await session.get(UploadFolder, folder_id)
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    ensure_can_manage(principal, folder.school_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(folder, key, value.strip() if isinstance(value, str) else value)
    try:
        await session.commit()
    except sa.exc.IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "This folder already exists") from exc
    return await _folder_out(session, folder)
