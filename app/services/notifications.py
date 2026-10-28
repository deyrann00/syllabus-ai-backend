import asyncio
import logging
import smtplib
import uuid
from collections.abc import Iterable
from email.message import EmailMessage

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.pg import session_scope
from app.enums import Role
from app.models import Course, Notification, Program, RoleAssignment, User

log = logging.getLogger(__name__)


def notify(
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID],
    *,
    kind: str,
    title: str,
    body: str | None = None,
    payload: dict | None = None,
    email: bool = True,
) -> None:
    """Queue in-app notifications (and outbox emails) in the caller's transaction."""
    for user_id in dict.fromkeys(user_ids):
        session.add(
            Notification(
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                payload=payload or {},
                email_status="pending" if email else None,
            )
        )


async def users_with_role(
    session: AsyncSession,
    role: Role,
    *,
    school_id: uuid.UUID | None = None,
    program_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    query = sa.select(RoleAssignment.user_id).where(RoleAssignment.role == role)
    if school_id:
        query = query.where(RoleAssignment.school_id == school_id)
    if program_id:
        query = query.where(RoleAssignment.program_id == program_id)
    return list((await session.scalars(query)).unique())


async def program_heads_for_course(session: AsyncSession, course_id: uuid.UUID) -> list[uuid.UUID]:
    program_id = await session.scalar(sa.select(Course.program_id).where(Course.id == course_id))
    return await users_with_role(session, Role.program_head, program_id=program_id)


async def school_admins_for_course(session: AsyncSession, course_id: uuid.UUID) -> list[uuid.UUID]:
    school_id = await session.scalar(
        sa.select(Program.school_id)
        .join(Course, Course.program_id == Program.id)
        .where(Course.id == course_id)
    )
    return await users_with_role(session, Role.school_admin, school_id=school_id)


def _send_smtp(to: str, subject: str, body: str) -> None:
    s = get_settings()
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = s.email_from, to, subject
    message.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(message)


async def send_pending_emails(limit: int = 200) -> int:
    """Outbox worker (celery beat). Returns how many emails were attempted."""
    settings = get_settings()
    async with session_scope() as session:
        rows = (
            await session.execute(
                sa.select(Notification, User.email)
                .join(User, User.id == Notification.user_id)
                .where(Notification.email_status == "pending")
                .order_by(Notification.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True, of=Notification)
            )
        ).all()
        for notification, email in rows:
            body = f"{notification.body or notification.title}\n\nOpen Syllabus AI: {settings.frontend_url}"
            subject = f"Syllabus AI: {notification.title}"
            try:
                if settings.email_backend == "console":
                    log.info("EMAIL to=%s subject=%s\n%s", email, subject, body)
                else:
                    await asyncio.to_thread(_send_smtp, email, subject, body)
                notification.email_status = "sent"
            except Exception:
                log.exception("failed to email notification %s", notification.id)
                notification.email_status = "failed"
        return len(rows)
