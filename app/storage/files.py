"""Syllabus files on local disk: FILES_ROOT/{syllabus_id}/v{n}/{filename}. Paths in the DB are relative."""

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

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


def save_version_file(syllabus_id: uuid.UUID, version: int, filename: str, data: bytes) -> StoredFile:
    relative = Path(str(syllabus_id)) / f"v{version}" / safe_filename(filename)
    target = root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return StoredFile(str(relative), len(data), hashlib.sha256(data).hexdigest())


def absolute(relative_path: str) -> Path:
    path = (root() / relative_path).resolve()
    if not path.is_relative_to(root()):
        raise ValueError("path escapes the files root")
    return path


def tmp_dir() -> Path:
    path = get_settings().tmp_root.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
