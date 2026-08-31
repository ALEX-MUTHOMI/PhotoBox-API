import io
import os
from unittest.mock import patch

import django
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image as PILImage
from rest_framework.test import APIClient


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
django.setup()


@pytest.fixture(autouse=True)
def clear_shared_cache():
    cache.clear()
    yield
    cache.clear()


_WEBHOOK_TEST_MARKERS = (
    "webhook",
    "r2_webhook",
    "cloudflare",
    "ingestion/tests/test_r2_webhook.py",
    "ingestion/tests/test_security.py",
    "gallery/tests/test_asset_hardening.py",
    "gallery/tests/test_watermark_engine.py",
)


@pytest.fixture(autouse=True)
def mock_r2_object_size_for_webhook_tests(request):
    """Webhook tests reconcile size via R2 HEAD — stub it unless a test patches explicitly."""
    nodeid = request.node.nodeid
    if "mismatch" in request.node.name:
        yield
        return
    if not any(marker in nodeid for marker in _WEBHOOK_TEST_MARKERS):
        yield
        return

    from gallery.models import Photo

    def _head_size(key):
        photo = Photo.objects.filter(r2_object_key=key).first()
        if photo is not None:
            return photo.file_size_bytes
        return 1024

    with patch("ingestion.views.r2_object_size", side_effect=_head_size) as mock_head:
        yield mock_head


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user_factory(db):
    user_model = get_user_model()

    def create_user(**overrides):
        defaults = {
            "email": "photographer@example.com",
            "password": "StrongPassword123!",  # nosec B105 - deterministic test fixture credential.
            "name": "PhotoBox User",
            "accepted_terms": True,
        }
        defaults.update(overrides)
        password = defaults.pop("password")
        return user_model.objects.create_user(password=password, **defaults)

    return create_user


@pytest.fixture
def tenant_factory(db, user_factory):
    from core.models import Workspace
    from gallery.models import Event, Scene

    def create_tenant(
        email="photographer@example.com",
        business_name="PhotoBox Studio",
        event_title="Launch Event",
        event_slug="launch-event",
        scene_title="Main Scene",
    ):
        user = user_factory(email=email)
        workspace = Workspace.objects.create(user=user, business_name=business_name)
        event = Event.objects.create(workspace=workspace, title=event_title, slug=event_slug)
        scene = Scene.objects.create(event=event, title=scene_title)
        return {
            "user": user,
            "workspace": workspace,
            "event": event,
            "scene": scene,
        }

    return create_tenant


@pytest.fixture
def valid_image_file():
    def build(filename="upload.jpg", size=(64, 64), image_format="JPEG"):
        stream = io.BytesIO()
        PILImage.new("RGB", size, color=(16, 32, 64)).save(stream, format=image_format)
        stream.seek(0)
        content_type = f"image/{image_format.lower()}"
        if image_format.upper() == "JPEG":
            content_type = "image/jpeg"
        return SimpleUploadedFile(filename, stream.read(), content_type=content_type)

    return build
