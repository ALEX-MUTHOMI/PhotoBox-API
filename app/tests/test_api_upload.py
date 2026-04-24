"""
test_api_upload.py — Django REST API layer tests.

Tests every HTTP concern before any external service is involved.
All Cloudinary and Celery calls are mocked — these are pure API tests.

Run with: docker compose run --rm test api

Security coverage (hacker mindset):
  - IDOR: user A cannot read/delete user B's uploads
  - JWT: alg:none, expired tokens, tampered payloads, missing bearer
  - File validation: polyglot, SVG XXE/XSS, null bytes, path traversal,
    JPEG+PHP injection, GIF+script, TIFF, zip bomb, oversized filenames
  - Mass assignment: injecting unexpected JSON fields into the upload body
  - Filename sanitisation: path traversal, null bytes, unicode homographs
  - Response schema: no internal paths, stack traces, or DB IDs in errors
  - Rate limiting: burst detection
"""

import base64
import io
import json
import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from tests.conftest import (
    PhotoFactory,
    ProcessedPhotoFactory,
    make_image_file,
    make_large_image_file,
    make_polyglot_jpeg_zip,
    make_svg_xxe,
    make_svg_xss,
    make_zip_bomb,
    make_path_traversal_filename,
    make_null_byte_filename,
    make_overlong_filename,
    make_jpeg_with_embedded_php,
    make_gif_with_script,
    make_tiff_file,
    PHOTO_CDN_URL_FIELD,
)

pytestmark = pytest.mark.django_db

PHOTO_LIST_URL_NAME = "gallery:fastlane-photo-list"
PHOTO_DETAIL_URL_NAME = "gallery:fastlane-photo-detail"


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _mock_external_calls(mocker):
    """Compatibility shim: the async dispatch boundary is stubbed module-wide."""
    return None


@pytest.fixture(autouse=True)
def _stub_fast_lane_dispatch(mocker):
    """
    Hard guardrail for this module: upload API tests never execute the real
    Celery task, even if a future test forgets to call _mock_external_calls().
    """
    return mocker.patch("gallery.tasks.process_fast_lane_asset.delay")


def _photo_list_url():
    return reverse(PHOTO_LIST_URL_NAME)


def _photo_detail_url(pk):
    return reverse(PHOTO_DETAIL_URL_NAME, kwargs={"pk": str(pk)})


def _upload(client, image_file, extra_data: dict = None, scene_id: str | None = None):
    url = _photo_list_url()
    payload = {"image_file": image_file}
    resolved_scene_id = scene_id or getattr(client, "test_scene_id", None)
    if resolved_scene_id is not None:
        payload["scene"] = resolved_scene_id
    if extra_data:
        payload.update(extra_data)
    return client.post(url, payload, format="multipart")


# ─────────────────────────────────────────────────────────────────────────────
# AUTH ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadAuthEnforcement:

    def test_anonymous_upload_returns_401(self, api_client):
        url = _photo_list_url()
        response = api_client.post(
            url, {"image_file": make_image_file(), "scene": str(uuid.uuid4())}, format="multipart"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_returns_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer totally.invalid.token")
        url = _photo_list_url()
        response = api_client.post(
            url, {"image_file": make_image_file(), "scene": str(uuid.uuid4())}, format="multipart"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_alg_none_jwt_is_rejected(self, api_client):
        """
        The 'alg:none' JWT attack strips the signature entirely.
        A vulnerable server accepts any payload as valid.
        CVE class: CWE-347 (Improper Verification of Cryptographic Signature).
        """
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=")
        payload = base64.urlsafe_b64encode(
            json.dumps({"user_id": 1, "exp": 9999999999}).encode()
        ).rstrip(b"=")
        unsigned_token = f"{header.decode()}.{payload.decode()}."

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {unsigned_token}")
        url = _photo_list_url()
        response = api_client.post(
            url, {"image_file": make_image_file(), "scene": str(uuid.uuid4())}, format="multipart"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            "Server accepted a JWT with alg:none — signature verification is disabled!"
        )

    def test_expired_jwt_is_rejected(self, api_client, photographer_user):
        """An expired access token must be rejected, not silently accepted."""
        from rest_framework_simplejwt.tokens import AccessToken
        from datetime import timedelta
        from django.utils import timezone

        token = AccessToken.for_user(photographer_user)
        # Force the token's expiry into the past
        token.set_exp(lifetime=timedelta(seconds=-1))

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token)}")
        url = _photo_list_url()
        response = api_client.post(
            url, {"image_file": make_image_file(), "scene": str(uuid.uuid4())}, format="multipart"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_bearer_prefix_is_required(self, api_client, photographer_user):
        """
        Token without the 'Bearer ' scheme prefix must not authenticate.
        Some middleware naïvely accepts raw tokens — this catches that.
        """
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(photographer_user)
        api_client.credentials(
            HTTP_AUTHORIZATION=str(refresh.access_token)  # no "Bearer " prefix
        )
        url = _photo_list_url()
        response = api_client.post(
            url, {"image_file": make_image_file(), "scene": str(uuid.uuid4())}, format="multipart"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_request_is_accepted(self, authenticated_client, test_image, mocker):
        """Authenticated POST reaches the view — external calls mocked."""
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, test_image)
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_202_ACCEPTED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# INSECURE DIRECT OBJECT REFERENCE (IDOR)
# ─────────────────────────────────────────────────────────────────────────────

class TestIDOR:
    """
    User A must not be able to read, modify, or delete user B's uploads.
    These are the most common API privilege-escalation bugs and are almost
    always absent from first-draft test suites.
    """

    def test_user_cannot_read_another_users_photo(
        self,
        db,
        authenticated_client,
        second_authenticated_client,
        photographer_user,
    ):
        """
        User B requests the detail URL for a photo owned by User A.
        Must receive 403 or 404 — never 200 with real data.
        """
        photo = ProcessedPhotoFactory(owner=photographer_user)

        url = _photo_detail_url(photo.pk)
        response = second_authenticated_client.get(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"User B received {response.status_code} on User A's photo. "
            "This is an IDOR vulnerability."
        )

    def test_user_cannot_delete_another_users_photo(
        self,
        db,
        authenticated_client,
        second_authenticated_client,
        photographer_user,
    ):
        """User B must not be able to delete User A's photo."""
        photo = ProcessedPhotoFactory(owner=photographer_user)

        url = _photo_detail_url(photo.pk)
        response = second_authenticated_client.delete(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"User B deleted User A's photo ({response.status_code}). "
            "This is an IDOR vulnerability."
        )

    def test_user_cannot_list_another_users_photos(
        self,
        db,
        authenticated_client,
        second_authenticated_client,
        photographer_user,
        second_photographer_user,
    ):
        """
        The photo list endpoint must be scoped to the authenticated user.
        User B must see 0 photos from User A's library.
        """
        ProcessedPhotoFactory.create_batch(3, owner=photographer_user)

        url = _photo_list_url()
        response = second_authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK

        # Depending on response shape (paginated or flat list)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        assert len(results) == 0, (
            f"User B can see {len(results)} photos owned by User A. "
            "Photo list is not scoped to the authenticated user."
        )

    def test_sequential_id_enumeration_is_not_possible(
        self,
        db,
        second_authenticated_client,
        photographer_user,
    ):
        """
        If photos use sequential integer PKs, an attacker can enumerate all
        uploads by incrementing the ID. UUIDs prevent this.
        Verify the detail endpoint returns 404 for a constructed sequential ID.
        """
        photo = ProcessedPhotoFactory(owner=photographer_user)

        # If the PK is an integer, try PK+1; for UUIDs this is a random probe
        try:
            guessed_pk = int(photo.pk) + 1
        except (ValueError, TypeError):
            pytest.skip("Photo PK is a UUID — enumeration not applicable")

        url = _photo_detail_url(guessed_pk)
        response = second_authenticated_client.get(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )


# ─────────────────────────────────────────────────────────────────────────────
# FILE VALIDATION — LEGITIMATE TYPES
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadFileValidation:

    def test_jpeg_is_accepted(self, authenticated_client, mocker):
        _mock_external_calls(mocker)
        response = _upload(
            authenticated_client,
            make_image_file(fmt="JPEG", filename="photo.jpg"),
        )
        assert response.status_code not in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    def test_png_is_accepted(self, authenticated_client, mocker):
        _mock_external_calls(mocker)
        response = _upload(
            authenticated_client,
            make_image_file(fmt="PNG", filename="photo.png"),
        )
        assert response.status_code not in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    def test_pdf_is_rejected(self, authenticated_client):
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake content")
        fake_pdf.name = "document.pdf"
        assert _upload(authenticated_client, fake_pdf).status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_file_is_rejected(self, authenticated_client):
        empty = io.BytesIO(b"")
        empty.name = "empty.jpg"
        assert _upload(authenticated_client, empty).status_code == status.HTTP_400_BAD_REQUEST

    def test_no_file_field_returns_400(self, authenticated_client):
        url = _photo_list_url()
        assert (
            authenticated_client.post(
                url,
                {"scene": authenticated_client.test_scene_id},
                format="multipart",
            ).status_code
            == status.HTTP_400_BAD_REQUEST
        )

    def test_file_too_large_is_rejected(self, authenticated_client):
        huge = make_large_image_file(mb=50)
        assert _upload(authenticated_client, huge).status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    def test_tiff_handling_is_explicit(self, authenticated_client, mocker):
        """
        TIFF is accepted by many image parsers but rarely tested explicitly.
        The policy (accept or reject) must be enforced — not just undefined.
        Update the assertion to match your intended policy.
        """
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, make_tiff_file())
        # POLICY: update to HTTP_200_OK/201/202 if TIFFs are allowed
        # For most photo platforms, TIFFs should be rejected at this layer.
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_202_ACCEPTED,
        ), "TIFF handling is undefined — establish an explicit policy."


# ─────────────────────────────────────────────────────────────────────────────
# FILE VALIDATION — ADVERSARIAL / SECURITY
# ─────────────────────────────────────────────────────────────────────────────

class TestAdversarialFileUploads:
    """
    Adversarial inputs that look like images but carry malicious payloads.
    Every one of these has been used in real-world upload bypass attacks.
    """

    def test_executable_disguised_as_jpg_is_rejected(self, authenticated_client):
        """MZ header (Windows PE / EXE) with a .jpg extension."""
        malicious = io.BytesIO(b"\x4d\x5a\x90\x00" * 100)
        malicious.name = "photo.jpg"
        assert _upload(authenticated_client, malicious).status_code == status.HTTP_400_BAD_REQUEST

    def test_polyglot_jpeg_zip_is_rejected(self, authenticated_client):
        """
        File that is simultaneously a valid JPEG and a valid ZIP.
        Passes content-type sniffing but contains an embedded archive.
        Should be rejected by structural validation or size/entropy checks.
        """
        response = _upload(authenticated_client, make_polyglot_jpeg_zip())
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "Polyglot JPEG+ZIP was accepted. "
            "Implement structural validation beyond MIME sniffing."
        )

    def test_svg_with_xxe_is_rejected(self, authenticated_client):
        """
        SVG with XML External Entity injection — could read /etc/passwd
        if processed server-side by an XML parser.
        """
        response = _upload(authenticated_client, make_svg_xxe())
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "SVG with XXE payload was accepted. "
            "SVGs must be sanitised or rejected outright."
        )

    def test_svg_with_xss_is_rejected(self, authenticated_client):
        """
        SVG with onload JavaScript — stored XSS if served without
        Content-Disposition: attachment or proper CSP.
        """
        response = _upload(authenticated_client, make_svg_xss())
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "SVG with inline JavaScript was accepted. "
            "SVGs must be sanitised before storage."
        )

    def test_zip_bomb_is_rejected(self, authenticated_client):
        """
        Highly compressed file that expands to many times its size.
        Should fail either on file-size check or decompression-bomb detection.
        """
        response = _upload(authenticated_client, make_zip_bomb(layers=3))
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        ), "Zip bomb was accepted — implement decompression-bomb detection."

    def test_jpeg_with_embedded_php_is_handled(self, authenticated_client, mocker):
        """
        JPEG with PHP code in the EXIF comment block.
        Accepted images with embedded scripts must have EXIF stripped before
        storage — the raw bytes must not be served back verbatim.
        """
        # This may be accepted (the image is structurally valid JPEG),
        # but the server must strip EXIF on processing.
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, make_jpeg_with_embedded_php())
        # If your API rejects it outright — great. If it accepts it,
        # the Cloudinary processing pipeline must strip metadata.
        if response.status_code in (200, 201, 202):
            # Verify no PHP code leaks back in the response
            assert b"<?php" not in response.content, (
                "PHP code from EXIF comment was reflected in the API response."
            )

    def test_gif_with_script_comment_is_rejected_or_sanitised(self, authenticated_client, mocker):
        """
        GIF89a with a <script> tag in the comment extension.
        Relevant when uploads are served with Content-Type: image/gif
        but without X-Content-Type-Options: nosniff.
        """
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, make_gif_with_script())
        if response.status_code in (200, 201, 202):
            assert b"<script>" not in response.content


# ─────────────────────────────────────────────────────────────────────────────
# FILENAME SECURITY
# ─────────────────────────────────────────────────────────────────────────────

class TestFilenameSecurityValidation:
    """
    Filename-based attacks. The original_filename field gets stored in the
    database and may be reflected in the API response — making it a vector
    for path traversal, null-byte injection, and stored XSS.
    """

    @pytest.mark.parametrize("traversal_payload", [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "/etc/cron.d/backdoor.jpg",
        "....//....//etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ])
    def test_path_traversal_filename_is_sanitised(
        self, authenticated_client, mocker, traversal_payload
    ):
        """
        Path traversal sequences in filenames must be stripped or rejected.
        If accepted, the stored filename must not contain '../'.
        """
        _mock_external_calls(mocker)
        f = make_image_file()
        f.name = traversal_payload
        response = _upload(authenticated_client, f)

        if response.status_code in (200, 201, 202):
            body = response.json()
            stored_name = body.get("filename") or body.get("original_filename") or ""
            assert ".." not in stored_name, (
                f"Path traversal sequence persisted in filename: {stored_name!r}"
            )
            assert stored_name != traversal_payload, (
                f"Raw traversal filename stored verbatim: {stored_name!r}"
            )

    def test_null_byte_in_filename_is_rejected_or_sanitised(
        self, authenticated_client, mocker
    ):
        """
        Null bytes terminate strings in C/PHP. 'photo\x00.php.jpg' may be
        stored as 'photo' + '.php' on vulnerable systems.
        """
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, make_null_byte_filename())
        if response.status_code in (200, 201, 202):
            body = response.json()
            stored_name = body.get("filename") or body.get("original_filename") or ""
            assert "\x00" not in stored_name, (
                "Null byte persisted in stored filename — C-string truncation risk."
            )
            assert ".php" not in stored_name, (
                f"PHP extension survived null-byte injection: {stored_name!r}"
            )

    def test_overlong_filename_is_rejected(self, authenticated_client):
        """
        4096-char filename should be rejected before hitting OS/DB limits.
        Most filesystems cap at 255 bytes; DBs may silently truncate.
        """
        response = _upload(authenticated_client, make_overlong_filename(length=4096))
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "4096-char filename was accepted — impose a max filename length."
        )

    def test_unicode_filename_with_homograph_chars_is_sanitised(
        self, authenticated_client, mocker
    ):
        """
        Unicode homograph attack: Cyrillic 'а' (U+0430) looks identical to
        Latin 'a' but is a different byte — can confuse logging, deduplication,
        and human reviewers. Must be stored as-is or normalised, never crashed.
        """
        _mock_external_calls(mocker)
        f = make_image_file()
        f.name = "phоtо.jpg"  # 'о' is Cyrillic U+043E, not Latin 'o'
        response = _upload(authenticated_client, f)
        # Must not 500 — policy on acceptance is secondary
        assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.parametrize("xss_name", [
        '<script>alert(1)</script>.jpg',
        '"><img src=x onerror=alert(1)>.jpg',
        "javascript:alert(1).jpg",
    ])
    def test_xss_in_filename_is_sanitised_in_response(
        self, authenticated_client, mocker, xss_name
    ):
        """
        If the filename is reflected back in the JSON response, it must be
        escaped. Raw <script> tags in filenames are stored-XSS in API consumers
        that render HTML without proper escaping.
        """
        _mock_external_calls(mocker)
        f = make_image_file()
        f.name = xss_name
        response = _upload(authenticated_client, f)

        if response.status_code in (200, 201, 202):
            # DRF serialises to JSON — the raw bytes of the response
            # must not contain unescaped script tags
            assert b"<script>" not in response.content, (
                f"XSS payload in filename survived into API response: {xss_name!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# MASS ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

class TestMassAssignment:
    """
    Attempt to inject privileged fields via the multipart upload body.
    A misconfigured serializer may silently accept and persist these.
    """

    @pytest.mark.parametrize("injected_field,injected_value", [
        ("status", "processed"),
        ("is_featured", "true"),
        ("owner", "99999"),
        ("file_size_bytes", "-1"),
        ("created_at", "2000-01-01T00:00:00Z"),
        ("cloudinary_url", "https://attacker.example/evil.jpg"),
        (PHOTO_CDN_URL_FIELD, "https://attacker.example/evil.jpg"),
    ])
    def test_cannot_inject_privileged_fields_via_upload(
        self, authenticated_client, mocker, injected_field, injected_value
    ):
        """
        POST /upload with extra fields must either ignore them or reject them.
        Must never persist attacker-controlled values for privileged columns.
        """
        _mock_external_calls(mocker)
        url = _photo_list_url()
        response = authenticated_client.post(
            url,
            {
                "image_file": make_image_file(),
                "scene": authenticated_client.test_scene_id,
                injected_field: injected_value,
            },
            format="multipart",
        )

        # If the upload succeeds, verify the injected field was NOT applied
        if response.status_code in (200, 201, 202):
            body = response.json()
            upload_id = body.get("photo_id") or body.get("upload_id") or body.get("id")

            if upload_id:
                from gallery.models import Photo
                try:
                    photo = Photo.objects.get(pk=upload_id)
                    if injected_field == "status":
                        assert photo.status != "processed", (
                            "Mass assignment: 'status' was set to 'processed' via upload body."
                        )
                    if injected_field in ("cloudinary_url", PHOTO_CDN_URL_FIELD):
                        cdn_val = getattr(photo, PHOTO_CDN_URL_FIELD, None)
                        assert cdn_val != "https://attacker.example/evil.jpg", (
                            f"Mass assignment: CDN URL was overwritten via upload body."
                        )
                except Photo.DoesNotExist:
                    pass  # Upload may be async — cannot check immediately


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadResponseSchema:

    REQUIRED_FIELDS = {"photo_id", "status", "message"}

    def test_successful_upload_contains_required_fields(
        self, authenticated_client, test_image, mocker
    ):
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, test_image)
        assert response.status_code in (200, 201, 202)
        data = response.json()
        missing = self.REQUIRED_FIELDS - set(data.keys())
        assert not missing, f"Response missing fields: {missing}"

    def test_error_response_contains_errors_key(self, authenticated_client):
        url = _photo_list_url()
        empty = io.BytesIO(b"")
        empty.name = "bad.jpg"
        response = authenticated_client.post(
            url,
            {"image_file": empty, "scene": authenticated_client.test_scene_id},
            format="multipart",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "errors" in data or "detail" in data

    def test_error_response_does_not_leak_stack_trace(self, authenticated_client):
        """
        Error responses must never contain a Django stack trace, internal file
        paths, or ORM query details. These are information disclosure vectors
        that help attackers map internal structure.
        """
        url = _photo_list_url()
        empty = io.BytesIO(b"")
        empty.name = "bad.jpg"
        response = authenticated_client.post(
            url,
            {"image_file": empty, "scene": authenticated_client.test_scene_id},
            format="multipart",
        )

        body = response.content
        assert b"Traceback" not in body, "Stack trace leaked in error response."
        assert b"/app/" not in body, "Internal file path leaked in error response."
        assert b"psycopg2" not in body, "DB driver name leaked in error response."
        assert b"gallery_photo" not in body, "DB table name leaked in error response."

    def test_error_response_does_not_leak_server_header(self, authenticated_client):
        """
        The Server header must not reveal Django/gunicorn version strings
        that aid fingerprinting.
        """
        url = _photo_list_url()
        response = authenticated_client.post(
            url,
            {"scene": authenticated_client.test_scene_id},
            format="multipart",
        )
        server_header = response.get("Server", "")
        assert "django" not in server_header.lower(), (
            f"Server header reveals Django: {server_header!r}"
        )
        assert "gunicorn" not in server_header.lower(), (
            f"Server header reveals gunicorn: {server_header!r}"
        )

    def test_successful_response_does_not_include_internal_paths(
        self, authenticated_client, mocker
    ):
        """
        The success response must not include filesystem paths, internal IPs,
        or any data that maps application internals.
        """
        _mock_external_calls(mocker)
        response = _upload(authenticated_client, make_image_file())
        if response.status_code in (200, 201, 202):
            body_str = response.content.decode("utf-8", errors="ignore")
            assert "/app/" not in body_str, "Internal filesystem path leaked."
            assert "127.0.0.1" not in body_str, "Localhost IP leaked in response."
            assert "10.0." not in body_str, "Internal network IP leaked in response."


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMITING
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadRateLimiting:

    @pytest.mark.slow
    def test_burst_uploads_are_throttled(self, authenticated_client, mocker):
        mocker.patch("gallery.tasks.process_fast_lane_asset.delay")
        statuses = [
            _upload(authenticated_client, make_image_file()).status_code
            for _ in range(20)
        ]
        assert status.HTTP_429_TOO_MANY_REQUESTS in statuses, (
            "Rate limiter did not trigger after 20 rapid requests. "
            "Configure DRF throttling or a reverse-proxy rate limit."
        )

    @pytest.mark.slow
    def test_rate_limit_is_per_user_not_global(
        self,
        authenticated_client,
        second_authenticated_client,
        mocker,
    ):
        """
        Rate limits must be scoped per user, not shared globally.
        User A exhausting their quota must not deny service to User B.
        """
        _mock_external_calls(mocker)

        # Exhaust User A's quota
        for _ in range(20):
            _upload(authenticated_client, make_image_file())

        # User B should still get through (or at least not be rate-limited
        # due to User A's activity)
        response = _upload(second_authenticated_client, make_image_file())
        assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
            "User B was rate-limited because of User A's activity. "
            "Rate limits must be per-user, not global."
        )
