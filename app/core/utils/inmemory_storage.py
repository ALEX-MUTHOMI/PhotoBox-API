"""
Minimal in-memory storage backend for Django test runs.

Why this exists:
  - The project currently pins Django < 4.1, so the built-in
    django.core.files.storage.InMemoryStorage is unavailable.
  - CI should not depend on writable project directories under /app.
  - Tests need a deterministic, disposable storage backend that never touches
    the repository filesystem.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from threading import RLock

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.encoding import filepath_to_uri


class InMemoryTestStorage(Storage):
    """Process-local file storage used only while the Django test runner is active."""

    _files: dict[str, bytes] = {}
    _lock = RLock()

    @classmethod
    def clear(cls) -> None:
        """Reset all stored files between test runs."""
        with cls._lock:
            cls._files.clear()

    def _normalize_name(self, name: str) -> str:
        return str(PurePosixPath(name)).lstrip("/")

    def _open(self, name, mode="rb"):
        normalized_name = self._normalize_name(name)
        with self._lock:
            content = self._files[normalized_name]
        return ContentFile(content, name=normalized_name)

    def _save(self, name, content):
        normalized_name = self._normalize_name(name)
        if hasattr(content, "seek"):
            content.seek(0)
        payload = content.read()
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        with self._lock:
            self._files[normalized_name] = payload
        return normalized_name

    def delete(self, name):
        normalized_name = self._normalize_name(name)
        with self._lock:
            self._files.pop(normalized_name, None)

    def exists(self, name):
        normalized_name = self._normalize_name(name)
        with self._lock:
            return normalized_name in self._files

    def size(self, name):
        normalized_name = self._normalize_name(name)
        with self._lock:
            return len(self._files[normalized_name])

    def url(self, name):
        base_url = settings.MEDIA_URL
        if not base_url.endswith("/"):
            base_url = f"{base_url}/"
        return f"{base_url}{filepath_to_uri(self._normalize_name(name))}"
