"""Google Drive holds the durable copy of every syllabus version (see services/drive_sync.py).

There is no Google account connected yet, so DRIVE_PROVIDER=stub is the default: StubDrive keeps files under
DRIVE_STUB_ROOT, which behaves like a remote store for the whole flow. To connect Drive with the shared school
mailbox, implement GoogleDrive, set DRIVE_PROVIDER=google and put the folder ids on the School folders.
"""

import re
from pathlib import Path
from typing import Protocol

from app.config import get_settings

_FOLDER_ID = re.compile(r"[\w-]+")


class DriveClient(Protocol):
    """Blocking calls, like the Google client library: run them with asyncio.to_thread."""

    def upload(self, folder_id: str, name: str, data: bytes, content_type: str, key: str) -> str:
        """Store a file and return its id. `key` makes this idempotent: the same key gives the same file."""
        ...

    def download(self, file_id: str) -> bytes: ...

    def folder_accessible(self, folder_id: str) -> bool: ...


class StubDrive:
    def _root(self) -> Path:
        return get_settings().drive_stub_root.resolve()

    def upload(self, folder_id: str, name: str, data: bytes, content_type: str, key: str) -> str:
        file_id = f"{folder_id}/{key}_{name}"
        target = self._root() / file_id
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return file_id

    def download(self, file_id: str) -> bytes:
        return (self._root() / file_id).read_bytes()

    def folder_accessible(self, folder_id: str) -> bool:
        return _FOLDER_ID.fullmatch(folder_id) is not None


class GoogleDrive:
    """Not connected. What the implementation needs:

    - Credentials of the shared school mailbox, from a secret (an OAuth refresh token with the `drive.file`
      or `drive` scope). A service account would instead need a Shared Drive, it has no quota of its own.
    - google-api-python-client added to pyproject.toml.
    - upload: look for `appProperties has { key='syllabus_version' and value='<key>' }` first and return that
      file if it exists, otherwise `files.create` with `parents=[folder_id]`, that appProperties entry and a
      resumable media upload.
    - download: `files.get_media`. folder_accessible: `files.get`, False on 403 and 404.
    """

    def upload(self, folder_id: str, name: str, data: bytes, content_type: str, key: str) -> str:
        raise NotImplementedError("GoogleDrive is not connected yet")

    def download(self, file_id: str) -> bytes:
        raise NotImplementedError("GoogleDrive is not connected yet")

    def folder_accessible(self, folder_id: str) -> bool:
        raise NotImplementedError("GoogleDrive is not connected yet")


def get_drive() -> DriveClient:
    if get_settings().drive_provider == "stub":
        return StubDrive()
    return GoogleDrive()
