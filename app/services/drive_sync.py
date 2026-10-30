"""Copies stored versions to Google Drive from celery beat, so an upload never waits on Drive or fails with it."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from app.config import get_settings
from app.db.pg import session_scope
from app.models import Course, Syllabus, SyllabusVersion, UploadFolder
from app.storage import drive, files

log = logging.getLogger(__name__)


def _drive_name(course: str, semester: str, version: SyllabusVersion) -> str:
    return files.safe_filename(f"{course} {semester} v{version.version}{Path(version.filename).suffix}")


async def _sync(version: SyllabusVersion, folder_id: str | None, name: str) -> None:
    version.drive_attempts += 1
    if not folder_id:
        version.drive_error = "No Drive folder is configured"
        return
    try:
        data = await asyncio.to_thread(files.absolute(version.file_path).read_bytes)
        version.drive_file_id = await asyncio.to_thread(
            drive.get_drive().upload, folder_id, name, data, version.content_type, str(version.id)
        )
    except Exception as exc:
        log.exception("failed to copy version %s to Drive", version.id)
        version.drive_error = str(exc)[:500]
        return
    version.drive_error = None
    version.drive_synced_at = datetime.now(UTC)


async def sync_pending_files(limit: int = 50) -> int:
    """Outbox worker (celery beat). Least-tried versions go first, so a broken one cannot starve the rest."""
    default_folder = get_settings().drive_default_folder_id
    async with session_scope() as session:
        rows = (
            await session.execute(
                sa.select(SyllabusVersion, UploadFolder.drive_folder_id, Course.name, Syllabus.semester)
                .join(Syllabus, Syllabus.id == SyllabusVersion.syllabus_id)
                .join(Course, Course.id == Syllabus.course_id)
                .outerjoin(UploadFolder, UploadFolder.id == SyllabusVersion.folder_id)
                .where(SyllabusVersion.drive_file_id.is_(None))
                .order_by(SyllabusVersion.drive_attempts, SyllabusVersion.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True, of=SyllabusVersion)
            )
        ).all()
        for version, folder_id, course, semester in rows:
            await _sync(version, folder_id or default_folder, _drive_name(course, semester, version))
        return len(rows)
