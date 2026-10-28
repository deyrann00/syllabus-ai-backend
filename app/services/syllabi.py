import uuid
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extraction import SUPPORTED_TYPES
from app.auth.rbac import Principal, syllabus_filter
from app.config import get_settings
from app.enums import AiStatus, ApprovalStage, VersionSource
from app.models import ApprovalEvent, Course, Program, School, Syllabus, SyllabusVersion, UploadFolder, User
from app.storage import files


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def get_syllabus(session: AsyncSession, syllabus_id: uuid.UUID, *, lock: bool = False) -> Syllabus:
    query = sa.select(Syllabus).where(Syllabus.id == syllabus_id)
    if lock:
        query = query.with_for_update()
    syllabus = await session.scalar(query)
    if syllabus is None:
        raise not_found("Syllabus")
    return syllabus


async def get_version(session: AsyncSession, version_id: uuid.UUID) -> SyllabusVersion:
    version = await session.get(SyllabusVersion, version_id)
    if version is None:
        raise not_found("Version")
    return version


async def current_version(session: AsyncSession, syllabus: Syllabus) -> SyllabusVersion | None:
    return await session.scalar(
        sa.select(SyllabusVersion).where(
            SyllabusVersion.syllabus_id == syllabus.id, SyllabusVersion.version == syllabus.current_version
        )
    )


async def create_syllabus(
    session: AsyncSession,
    *,
    course: Course,
    professor_id: uuid.UUID,
    language: str,
    semester: str,
    folder_id: uuid.UUID | None = None,
) -> Syllabus:
    exists = await session.scalar(
        sa.select(Syllabus.id).where(Syllabus.course_id == course.id, Syllabus.semester == semester)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{course.name} already has a syllabus for {semester}")
    syllabus = Syllabus(
        course_id=course.id,
        professor_id=professor_id,
        language=language,
        semester=semester,
        current_version=0,
        ai_status=AiStatus.not_checked,
        approval_stage=ApprovalStage.draft,
        folder_id=folder_id,
    )
    session.add(syllabus)
    await session.flush()
    return syllabus


def validate_upload(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a .docx or .pdf file")
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds {get_settings().max_upload_mb} MB"
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty")
    return SUPPORTED_TYPES[suffix]


async def add_version(
    session: AsyncSession,
    syllabus: Syllabus,
    *,
    filename: str,
    data: bytes,
    user_id: uuid.UUID,
    source: VersionSource,
    folder_id: uuid.UUID | None = None,
) -> SyllabusVersion:
    """Store a new immutable version. The syllabus re-enters the workflow at Draft until its check completes."""
    content_type = validate_upload(filename, data)
    number = syllabus.current_version + 1
    stored = files.save_version_file(syllabus.id, number, filename, data)
    version = SyllabusVersion(
        syllabus_id=syllabus.id,
        version=number,
        filename=files.safe_filename(filename),
        file_path=stored.relative_path,
        content_type=content_type,
        size=stored.size,
        sha256=stored.sha256,
        created_by=user_id,
        source=source,
        folder_id=folder_id or syllabus.folder_id,
    )
    session.add(version)
    previous_stage = syllabus.approval_stage
    syllabus.current_version = number
    syllabus.issues = None
    syllabus.ai_status = AiStatus.not_checked
    syllabus.approval_stage = ApprovalStage.draft
    if folder_id:
        syllabus.folder_id = folder_id
    session.add(
        ApprovalEvent(
            syllabus_id=syllabus.id,
            kind="version",
            version=number,
            from_stage=previous_stage,
            to_stage=ApprovalStage.draft,
            actor_id=user_id,
            comment=f"Uploaded v{number} ({version.filename})",
        )
    )
    await session.flush()
    return version


@dataclass
class SyllabusFilters:
    semester: str | None = None
    school_id: uuid.UUID | None = None
    program_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    ai_status: AiStatus | None = None
    approval_stage: ApprovalStage | None = None
    language: str | None = None
    q: str | None = None
    mine: bool = False


def syllabus_rows_query(principal: Principal) -> sa.Select:
    return (
        sa.select(
            Syllabus,
            Course.name.label("course_name"),
            Course.slug.label("course_slug"),
            Course.lead_id,
            Program.id.label("program_id"),
            Program.name.label("program_name"),
            School.id.label("school_id"),
            School.name.label("school_name"),
            User.name.label("professor_name"),
            UploadFolder.path.label("folder_path"),
        )
        .join(Course, Course.id == Syllabus.course_id)
        .join(Program, Program.id == Course.program_id)
        .join(School, School.id == Program.school_id)
        .join(User, User.id == Syllabus.professor_id)
        .outerjoin(UploadFolder, UploadFolder.id == Syllabus.folder_id)
        .where(syllabus_filter(principal))
    )


def apply_filters(query: sa.Select, f: SyllabusFilters, principal: Principal) -> sa.Select:
    if f.semester:
        query = query.where(Syllabus.semester == f.semester)
    if f.school_id:
        query = query.where(School.id == f.school_id)
    if f.program_id:
        query = query.where(Program.id == f.program_id)
    if f.course_id:
        query = query.where(Course.id == f.course_id)
    if f.ai_status:
        query = query.where(Syllabus.ai_status == f.ai_status)
    if f.approval_stage:
        query = query.where(Syllabus.approval_stage == f.approval_stage)
    if f.language:
        query = query.where(Syllabus.language == f.language)
    if f.q:
        like = f"%{f.q.strip()}%"
        query = query.where(sa.or_(Course.name.ilike(like), User.name.ilike(like), Program.name.ilike(like)))
    if f.mine:
        query = query.where(Course.lead_id == principal.id)
    return query


def row_to_dict(row: sa.Row) -> dict:
    s: Syllabus = row[0]
    return {
        "id": s.id,
        "course_id": s.course_id,
        "course_name": row.course_name,
        "course_slug": row.course_slug,
        "course_lead_id": row.lead_id,
        "program_id": row.program_id,
        "program_name": row.program_name,
        "school_id": row.school_id,
        "school_name": row.school_name,
        "professor_id": s.professor_id,
        "professor_name": row.professor_name,
        "language": s.language,
        "semester": s.semester,
        "current_version": s.current_version,
        "ai_status": s.ai_status,
        "approval_stage": s.approval_stage,
        "issues": s.issues,
        "folder_path": row.folder_path,
        "updated_at": s.updated_at,
    }


async def list_syllabi(
    session: AsyncSession, principal: Principal, f: SyllabusFilters, *, limit: int, offset: int
) -> tuple[list[dict], int]:
    query = apply_filters(syllabus_rows_query(principal), f, principal)
    total = await session.scalar(sa.select(sa.func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.execute(query.order_by(Syllabus.updated_at.desc()).limit(limit).offset(offset))
    ).all()
    return [row_to_dict(r) for r in rows], total


async def syllabus_detail(session: AsyncSession, principal: Principal, syllabus_id: uuid.UUID) -> dict:
    row = (
        await session.execute(syllabus_rows_query(principal).where(Syllabus.id == syllabus_id))
    ).one_or_none()
    if row is None:
        raise not_found("Syllabus")
    return row_to_dict(row)
