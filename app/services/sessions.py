"""Checking sessions: bulk AI checks over a scope (university, School, program, folder or one file)."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, forbidden
from app.db.pg import session_scope
from app.enums import CheckKind, Role, RunStatus, SessionItemStatus, SessionScope, SessionStatus, Trigger
from app.models import (
    AssessmentRun,
    CheckSession,
    Course,
    Program,
    School,
    SessionItem,
    Syllabus,
    SyllabusVersion,
    UploadFolder,
)
from app.services.checks import create_run
from app.services.notifications import notify, users_with_role
from app.workers import dispatch

FINISHED = (SessionItemStatus.done, SessionItemStatus.failed)


def _now() -> datetime:
    return datetime.now(UTC)


async def _scope_school(
    session: AsyncSession, scope: SessionScope, ref: uuid.UUID | None
) -> uuid.UUID | None:
    """School that owns the scope (None for the whole university); 404 when the reference does not exist."""
    if scope == SessionScope.university:
        return None
    if ref is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"scope_ref is required for scope '{scope}'"
        )
    query = {
        SessionScope.school: sa.select(School.id).where(School.id == ref),
        SessionScope.program: sa.select(Program.school_id).where(Program.id == ref),
        SessionScope.folder: sa.select(UploadFolder.school_id).where(UploadFolder.id == ref),
        SessionScope.file: sa.select(Program.school_id)
        .join(Course, Course.program_id == Program.id)
        .join(Syllabus, Syllabus.course_id == Course.id)
        .join(SyllabusVersion, SyllabusVersion.syllabus_id == Syllabus.id)
        .where(SyllabusVersion.id == ref),
    }[scope]
    school_id = await session.scalar(query)
    if school_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{scope.value} not found")
    return school_id


def ensure_can_manage(principal: Principal, school_id: uuid.UUID | None) -> None:
    if principal.has(Role.provost):
        return
    if principal.has(Role.school_admin) and school_id is not None and school_id in principal.school_ids:
        return
    raise forbidden("Only the Provost Office, or a School administrator for their School, can run sessions")


async def create(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    scope_type: SessionScope,
    scope_ref: uuid.UUID | None,
    semester: str,
    checks: list[CheckKind],
    scheduled_at: datetime | None,
    notify_provost: bool,
    notify_school: bool,
) -> tuple[CheckSession, list[uuid.UUID]]:
    """Returns the session and the run ids to dispatch after commit (empty when scheduled)."""
    school_id = await _scope_school(session, scope_type, scope_ref)
    ensure_can_manage(principal, school_id)
    if not checks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Select at least one check")
    if scheduled_at and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    cs = CheckSession(
        name=name.strip(),
        scope_type=scope_type,
        scope_ref=scope_ref if scope_type != SessionScope.university else None,
        semester=semester,
        checks=[c.value for c in checks],
        scheduled_at=scheduled_at,
        status=SessionStatus.scheduled,
        notify_provost=notify_provost,
        notify_school=notify_school,
        created_by=principal.id,
    )
    session.add(cs)
    await session.flush()
    run_ids = [] if scheduled_at and scheduled_at > _now() else await start(session, cs)
    return cs, run_ids


async def _resolve_versions(session: AsyncSession, cs: CheckSession) -> list[tuple[uuid.UUID, uuid.UUID]]:
    query = (
        sa.select(Syllabus.id, SyllabusVersion.id)
        .join(Course, Course.id == Syllabus.course_id)
        .join(Program, Program.id == Course.program_id)
        .join(SyllabusVersion, SyllabusVersion.syllabus_id == Syllabus.id)
    )
    if cs.scope_type == SessionScope.file:
        return [
            (r[0], r[1])
            for r in (await session.execute(query.where(SyllabusVersion.id == cs.scope_ref))).all()
        ]
    query = query.where(Syllabus.semester == cs.semester, SyllabusVersion.version == Syllabus.current_version)
    if cs.scope_type == SessionScope.school:
        query = query.where(Program.school_id == cs.scope_ref)
    elif cs.scope_type == SessionScope.program:
        query = query.where(Program.id == cs.scope_ref)
    elif cs.scope_type == SessionScope.folder:
        query = query.where(SyllabusVersion.folder_id == cs.scope_ref)
    return [(r[0], r[1]) for r in (await session.execute(query)).all()]


async def start(session: AsyncSession, cs: CheckSession) -> list[uuid.UUID]:
    pairs = await _resolve_versions(session, cs)
    cs.status = SessionStatus.running
    cs.started_at = _now()
    checks = [CheckKind(c) for c in cs.checks]
    trigger = Trigger.scheduled_scan if cs.scheduled_at else Trigger.provost_session
    run_ids = []
    for syllabus_id, version_id in pairs:
        version = await session.get(SyllabusVersion, version_id)
        assert version is not None
        run = await create_run(session, version, trigger=trigger, checks=checks, session_id=cs.id)
        session.add(
            SessionItem(session_id=cs.id, syllabus_id=syllabus_id, version_id=version_id, run_id=run.id)
        )
        run_ids.append(run.id)
    if not pairs:
        await _finish(session, cs)
    await session.flush()
    return run_ids


async def pause(session: AsyncSession, principal: Principal, cs: CheckSession) -> None:
    ensure_can_manage(principal, await _scope_school(session, cs.scope_type, cs.scope_ref))
    if cs.status != SessionStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {cs.status.value}, not Running")
    cs.status = SessionStatus.paused


async def resume(session: AsyncSession, principal: Principal, cs: CheckSession) -> list[uuid.UUID]:
    """Queued runs were skipped while paused; return them for re-dispatch after commit."""
    ensure_can_manage(principal, await _scope_school(session, cs.scope_type, cs.scope_ref))
    if cs.status != SessionStatus.paused:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {cs.status.value}, not Paused")
    cs.status = SessionStatus.running
    return list(
        await session.scalars(
            sa.select(AssessmentRun.id).where(
                AssessmentRun.session_id == cs.id, AssessmentRun.status == RunStatus.queued
            )
        )
    )


async def on_run_finished(session: AsyncSession, run: AssessmentRun, *, ok: bool) -> None:
    """Called inside the run's completion transaction."""
    if not run.session_id:
        return
    item = await session.scalar(sa.select(SessionItem).where(SessionItem.run_id == run.id))
    if item is not None:
        item.status = SessionItemStatus.done if ok else SessionItemStatus.failed
        item.error = None if ok else run.error
    await session.flush()
    cs = await session.scalar(
        sa.select(CheckSession).where(CheckSession.id == run.session_id).with_for_update()
    )
    if cs is None or cs.status == SessionStatus.completed:
        return
    remaining = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SessionItem)
        .where(SessionItem.session_id == cs.id, SessionItem.status.not_in(FINISHED))
    )
    if remaining == 0:
        await _finish(session, cs)


async def _finish(session: AsyncSession, cs: CheckSession) -> None:
    cs.status = SessionStatus.completed
    cs.completed_at = _now()
    counts = await progress(session, cs.id)
    title = f"Session '{cs.name}' completed: {counts['done']} checked, {counts['failed']} failed"
    recipients: list[uuid.UUID] = []
    if cs.notify_provost:
        recipients += await users_with_role(session, Role.provost)
    if cs.notify_school:
        school_ids = await session.scalars(
            sa.select(Program.school_id)
            .join(Course, Course.program_id == Program.id)
            .join(Syllabus, Syllabus.course_id == Course.id)
            .join(SessionItem, SessionItem.syllabus_id == Syllabus.id)
            .where(SessionItem.session_id == cs.id)
            .distinct()
        )
        for school_id in school_ids:
            recipients += await users_with_role(session, Role.school_admin, school_id=school_id)
    notify(
        session,
        recipients or [cs.created_by],
        kind="session_completed",
        title=title,
        payload={"session_id": str(cs.id)},
    )


async def progress(session: AsyncSession, session_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await session.execute(
            sa.select(SessionItem.status, sa.func.count())
            .where(SessionItem.session_id == session_id)
            .group_by(SessionItem.status)
        )
    ).all()
    by_status = {s: n for s, n in rows}
    done, failed = by_status.get(SessionItemStatus.done, 0), by_status.get(SessionItemStatus.failed, 0)
    total = sum(by_status.values())
    return {
        "total": total,
        "done": done,
        "failed": failed,
        "completed": done + failed,
        "pending": total - done - failed,
    }


async def get_session_obj(session: AsyncSession, principal: Principal, session_id: uuid.UUID) -> CheckSession:
    cs = await session.get(CheckSession, session_id)
    if cs is None or not _visible(principal, cs, await _scope_school(session, cs.scope_type, cs.scope_ref)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return cs


def _visible(principal: Principal, cs: CheckSession, school_id: uuid.UUID | None) -> bool:
    if principal.university_wide or cs.created_by == principal.id:
        return True
    return principal.has(Role.school_admin) and school_id in principal.school_ids


async def list_sessions(session: AsyncSession, principal: Principal) -> list[CheckSession]:
    rows = list(await session.scalars(sa.select(CheckSession).order_by(CheckSession.created_at.desc())))
    return [
        cs
        for cs in rows
        if _visible(principal, cs, await _scope_school(session, cs.scope_type, cs.scope_ref))
    ]


async def items(session: AsyncSession, session_id: uuid.UUID, *, limit: int, offset: int) -> list[dict]:
    rows = (
        await session.execute(
            sa.select(
                SessionItem,
                Course.name,
                SyllabusVersion.filename,
                SyllabusVersion.version,
                SyllabusVersion.issue_count,
            )
            .join(Syllabus, Syllabus.id == SessionItem.syllabus_id)
            .join(Course, Course.id == Syllabus.course_id)
            .join(SyllabusVersion, SyllabusVersion.id == SessionItem.version_id)
            .where(SessionItem.session_id == session_id)
            .order_by(SessionItem.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {
            "id": item.id,
            "syllabus_id": item.syllabus_id,
            "version_id": item.version_id,
            "run_id": item.run_id,
            "course_name": course_name,
            "filename": filename,
            "version": version,
            "status": item.status,
            "issues": issue_count if item.status == SessionItemStatus.done else None,
            "error": item.error,
            "updated_at": item.updated_at,
        }
        for item, course_name, filename, version, issue_count in rows
    ]


async def start_due_sessions() -> int:
    """celery beat: start scheduled sessions whose time has come."""
    async with session_scope() as session:
        due = list(
            await session.scalars(
                sa.select(CheckSession)
                .where(CheckSession.status == SessionStatus.scheduled, CheckSession.scheduled_at <= _now())
                .with_for_update(skip_locked=True)
            )
        )
        run_ids = [rid for cs in due for rid in await start(session, cs)]
    await dispatch.check_runs(run_ids)
    return len(due)
