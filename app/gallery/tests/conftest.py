"""
gallery/tests/conftest.py — Shared fixtures for gallery test suite.

These fixtures are automatically available to all tests in gallery/tests/.
No import required — pytest discovers conftest.py files automatically.
"""
import io
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image as PILImage
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# PRIMITIVE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_jpeg(
    width: int = 100,
    height: int = 100,
    filename: str = "test.jpg",
) -> SimpleUploadedFile:
    """Creates a real, Pillow-validated JPEG in memory."""
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(30, 80, 150)).save(buf, "JPEG")
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.read(), content_type="image/jpeg")


def _create_user(email: str, password: str = "TestPass#99!") -> "User":
    return User.objects.create_user(email=email, password=password)


def _create_full_tenant(
    user,
    *,
    biz_name: str = "Test Studio",
    event_title: str = "Summer Wedding",
    event_slug: str | None = None,
    scene_title: str = "Ceremony",
) -> tuple:
    """Bootstrap Workspace → Event → Scene for a user. Returns (ws, event, scene)."""
    slug = event_slug or f"event-{uuid.uuid4().hex[:8]}"
    # Workspace is a OneToOneField — get_or_create handles signal-created instances
    ws, _ = Workspace.objects.get_or_create(
        user=user,
        defaults={"business_name": biz_name},
    )
    event = Event.objects.create(workspace=ws, title=event_title, slug=slug)
    scene = Scene.objects.create(event=event, title=scene_title)
    return ws, event, scene



# ─────────────────────────────────────────────────────────────────────────────
# PYTEST FIXTURES — available to every gallery test
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def photographer(db):
    """A fully bootstrapped photographer: User + Workspace + Event + Scene."""
    user = _create_user("photographer@studio.test")
    ws, event, scene = _create_full_tenant(user)
    return {"user": user, "workspace": ws, "event": event, "scene": scene}


@pytest.fixture
def rival_photographer(db):
    """A second photographer with their own isolated tenant hierarchy."""
    user = _create_user("rival@studio.test")
    ws, event, scene = _create_full_tenant(user, biz_name="Rival Studio")
    return {"user": user, "workspace": ws, "event": event, "scene": scene}


@pytest.fixture
def photographer_client(photographer) -> APIClient:
    """Authenticated APIClient for the primary photographer."""
    client = APIClient()
    client.force_authenticate(user=photographer["user"])
    return client


@pytest.fixture
def rival_client(rival_photographer) -> APIClient:
    """Authenticated APIClient for the rival photographer."""
    client = APIClient()
    client.force_authenticate(user=rival_photographer["user"])
    return client


@pytest.fixture
def valid_jpeg():
    """A minimal valid JPEG SimpleUploadedFile that passes Pillow validation."""
    return _make_minimal_jpeg()


@pytest.fixture
def ready_photo(db, photographer):
    """A READY Photo owned by photographer, suitable for delivery/download tests."""
    return Photo.objects.create(
        scene=photographer["scene"],
        original_filename="ceremony_001.jpg",
        file_size_bytes=4_000_000,
        r2_object_key=f"fast-lane/tenant_1/scene_1/{uuid.uuid4().hex}.jpg",
        status="READY",
        is_processed=True,
    )
