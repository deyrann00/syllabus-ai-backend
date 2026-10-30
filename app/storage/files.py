"""Syllabus files on local disk: FILES_ROOT/{syllabus_id}/v{n}/{filename}. Paths in the DB are relative.

The app always reads this copy. Drive keeps the durable one, and a file missing here is restored from it.
"""

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import get_settings
from app.storage import drive

if TYPE_CHECKING:
    from app.models import SyllabusVersion

_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)


@dataclass
class StoredFile:
    relative_path: str
    size: int
    sha256: str


def safe_filename(name: str) -> str:
    base = Path(name).name.strip() or "syllabus"
    cleaned = _UNSAFE.sub("_", base)[:150]
    return cleaned or "syllabus"


def root() -> Path:
    return get_settings().files_root.resolve()


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{uuid.uuid4().hex}.part")
    tmp.write_bytes(data)
    tmp.replace(target)


def save_version_file(syllabus_id: uuid.UUID, version: int, filename: str, data: bytes) -> StoredFile:
    relative = Path(str(syllabus_id)) / f"v{version}" / safe_filename(filename)
    _write(root() / relative, data)
    return StoredFile(str(relative), len(data), hashlib.sha256(data).hexdigest())


def absolute(relative_path: str) -> Path:
    path = (root() / relative_path).resolve()
    if not path.is_relative_to(root()):
        raise ValueError("path escapes the files root")
    return path


async def ensure_local(version: "SyllabusVersion") -> Path:
    """The version's file on disk. Raises FileNotFoundError if it is gone and never reached Drive."""
    path = absolute(version.file_path)
    if await asyncio.to_thread(path.exists):
        return path
    if not version.drive_file_id:
        raise FileNotFoundError(version.file_path)
    data = await asyncio.to_thread(drive.get_drive().download, version.drive_file_id)
    if hashlib.sha256(data).hexdigest() != version.sha256:
        raise ValueError(f"Drive returned a different file for version {version.id}")
    await asyncio.to_thread(_write, path, data)
    return path


def tmp_dir() -> Path:
    path = get_settings().tmp_root.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
