"""
Minimal in-memory storage backend for Django test runs.

Why this exists:
  - CI must not depend on a writable /app bind mount (django-user often cannot
    create media_root on the host-mounted tree).
  - Tests need a deterministic, disposable storage backend that never touches
    the repository filesystem.
  - Wired via STORAGES["default"] under Django 5.2 test overrides.
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

    def listdir(self, path):
        prefix = self._normalize_name(path)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        dirs: set[str] = set()
        files: list[str] = []
        with self._lock:
            for name in self._files:
                if prefix and not name.startswith(prefix):
                    continue
                remainder = name[len(prefix):] if prefix else name
                if "/" in remainder:
                    dirs.add(remainder.split("/", 1)[0])
                elif remainder:
                    files.append(remainder)
        return sorted(dirs), sorted(files)

    def size(self, name):
        normalized_name = self._normalize_name(name)
        with self._lock:
            return len(self._files[normalized_name])

    def url(self, name):
        return f"{settings.MEDIA_URL}{filepath_to_uri(self._normalize_name(name))}"
