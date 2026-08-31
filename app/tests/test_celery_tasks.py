"""
Celery worker contract tests for the R2-backed fast-lane pipeline.

These tests are intentionally pure unit tests:
  - every Cloudflare R2 probe is mocked
  - no live broker is required
  - no test is allowed to reach the network
"""

import datetime
import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from tests.conftest import PhotoFactory, ProcessedPhotoFactory
from gallery.models import Photo
from gallery.tasks import process_fast_lane_asset
from ingestion.tasks import reap_abandoned_uploads

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.celery,
    pytest.mark.unit,
]


@pytest.fixture(autouse=True)
def r2_client_stub(monkeypatch):
    """
    Guardrail: no task in this module may create a real boto3 client.

    Individual tests that expect an R2 probe can override
    ``head_object.return_value`` or ``head_object.side_effect`` on the returned
    mock client.
    """
    from gallery import storage as gallery_storage

    client = MagicMock(name="mock_r2_client")
    client.head_object.side_effect = AssertionError(
        "Unexpected R2 HeadObject call in a unit test. Stub the return value explicitly."
    )
    monkeypatch.setattr(gallery_storage, "get_r2_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def r2_object_exists_stub(monkeypatch):
    """
    Guardrail: the reaper must never perform a live R2 existence probe in tests.
    """
    from gallery import storage as gallery_storage

    exists_mock = MagicMock(
        name="gallery.storage.r2_object_exists",
        side_effect=AssertionError(
            "Unexpected r2_object_exists call in a unit test. Stub it explicitly."
        ),
    )
    monkeypatch.setattr(gallery_storage, "r2_object_exists", exists_mock)
    return exists_mock


class TestProcessFastLaneAsset:
    def test_task_self_heals_when_r2_object_exists(self, r2_client_stub):
        photo = PhotoFactory(
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/hero.jpg",
        )
        r2_client_stub.head_object.side_effect = None
        r2_client_stub.head_object.return_value = {"ContentLength": photo.file_size_bytes}

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful(), f"Task failed unexpectedly:\n{result.traceback}"
        assert result.get()["status"] == "self_healed"

        photo.refresh_from_db()
        assert photo.status == "READY"
        assert photo.is_processed is True
        assert r2_client_stub.head_object.call_count == 1
        assert r2_client_stub.head_object.call_args.kwargs["Key"] == photo.r2_object_key

    def test_task_reconstructs_safe_key_when_db_key_is_missing(self, r2_client_stub):
        photo = PhotoFactory(
            original_filename="hero.jpg",
            r2_object_key="",
        )
        r2_client_stub.head_object.side_effect = None
        r2_client_stub.head_object.return_value = {"ContentLength": photo.file_size_bytes}

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        assert result.get()["status"] == "self_healed"
        expected_key = (
            f"fast-lane/tenant_{photo.scene.event.workspace.id}/{photo.id}/hero.jpg"
        )
        assert r2_client_stub.head_object.call_args.kwargs["Key"] == expected_key

    def test_task_refunds_quota_and_deletes_photo_when_object_is_missing(self, r2_client_stub):
        photo = PhotoFactory(
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/missing.jpg",
            file_size_bytes=4096,
        )
        workspace = photo.scene.event.workspace
        workspace.storage_used_bytes = photo.file_size_bytes + 512
        workspace.save(update_fields=["storage_used_bytes"])

        r2_client_stub.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        outcome = result.get()
        assert outcome["status"] == "abandoned_and_refunded"
        assert outcome["bytes_refunded"] == photo.file_size_bytes
        assert not Photo.objects.filter(pk=photo.pk).exists()

        workspace.refresh_from_db()
        assert workspace.storage_used_bytes == 512

    def test_task_refund_clamps_storage_at_zero(self, r2_client_stub):
        photo = PhotoFactory(
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/overshoot.jpg",
            file_size_bytes=500,
        )
        workspace = photo.scene.event.workspace
        workspace.storage_used_bytes = 100
        workspace.save(update_fields=["storage_used_bytes"])

        r2_client_stub.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        workspace.refresh_from_db()
        assert workspace.storage_used_bytes == 0

    def test_task_marks_failed_after_retry_budget_is_exhausted(self, r2_client_stub):
        photo = PhotoFactory(r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/retry.jpg")
        r2_client_stub.head_object.side_effect = OSError("ssl handshake failure")
        with patch.object(
            process_fast_lane_asset,
            "retry",
            side_effect=MaxRetriesExceededError(),
        ):
            result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        assert result.get()["reason"] == "max_retries_exceeded"
        photo.refresh_from_db()
        assert photo.status == "FAILED"

    def test_task_is_idempotent_for_already_processed_photo(self, r2_client_stub):
        photo = ProcessedPhotoFactory()

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        assert result.get()["status"] == "already_processed"
        r2_client_stub.head_object.assert_not_called()

    def test_task_skips_deleted_photos_without_probing_r2(self, r2_client_stub):
        photo = PhotoFactory()
        deleted_id = str(photo.pk)
        photo.delete()

        result = process_fast_lane_asset.apply(args=[deleted_id])

        assert result.successful()
        assert result.get() == {"status": "skipped", "reason": "not_found"}
        r2_client_stub.head_object.assert_not_called()

    def test_task_rejects_non_uuid_photo_ids_without_touching_r2(self, r2_client_stub):
        invalid_ids = [
            "' OR '1'='1",
            "1; DROP TABLE gallery_photo; --",
            "../../../etc/passwd",
            "",
            "0",
        ]

        for candidate in invalid_ids:
            result = process_fast_lane_asset.apply(args=[candidate])
            assert result.successful()
            assert result.get()["reason"] == "invalid_photo_id"

        r2_client_stub.head_object.assert_not_called()

    def test_task_marks_photo_failed_when_filename_is_unsafe(self, r2_client_stub):
        photo = PhotoFactory(
            original_filename="../../etc/passwd",
            r2_object_key="",
        )

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        assert result.get()["reason"] == "no_safe_r2_key"
        photo.refresh_from_db()
        assert photo.status == "FAILED"
        r2_client_stub.head_object.assert_not_called()

    def test_task_is_bound_to_image_processing_queue(self):
        assert getattr(process_fast_lane_asset, "queue", None) == "image-processing"


class TestReapAbandonedUploads:
    def test_reaper_returns_clean_when_no_assets_are_stale(self):
        result = reap_abandoned_uploads.apply()

        assert result.successful()
        assert result.get()["status"] == "clean"

    def test_reaper_quarantines_phantom_uploads_without_refunding_quota(
        self,
        r2_object_exists_stub,
    ):
        photo = PhotoFactory(
            status="PENDING",
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/phantom.jpg",
            file_size_bytes=2048,
        )
        workspace = photo.scene.event.workspace
        workspace.storage_used_bytes = 10_000
        workspace.save(update_fields=["storage_used_bytes"])
        Photo.objects.filter(pk=photo.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )

        r2_object_exists_stub.side_effect = None
        r2_object_exists_stub.return_value = True

        result = reap_abandoned_uploads.apply()

        assert result.successful()
        assert result.get()["phantom_count"] == 1
        photo.refresh_from_db()
        workspace.refresh_from_db()
        assert photo.status == "QUARANTINED"
        assert workspace.storage_used_bytes == 10_000
        r2_object_exists_stub.assert_called_once_with(photo.r2_object_key)

    def test_reaper_marks_legitimate_abandonments_failed_and_refunds_quota(
        self,
        r2_object_exists_stub,
    ):
        photo = PhotoFactory(
            status="PENDING",
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/abandoned.jpg",
            file_size_bytes=3072,
        )
        workspace = photo.scene.event.workspace
        workspace.storage_used_bytes = 5000
        workspace.save(update_fields=["storage_used_bytes"])
        Photo.objects.filter(pk=photo.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )

        r2_object_exists_stub.side_effect = None
        r2_object_exists_stub.return_value = False

        result = reap_abandoned_uploads.apply()

        assert result.successful()
        assert result.get()["reaped_count"] == 1
        photo.refresh_from_db()
        workspace.refresh_from_db()
        assert photo.status == "FAILED"
        assert workspace.storage_used_bytes == 1928
        r2_object_exists_stub.assert_called_once_with(photo.r2_object_key)

    def test_reaper_defers_without_mutating_state_when_r2_is_unreachable(
        self,
        r2_object_exists_stub,
    ):
        photo = PhotoFactory(
            status="PENDING",
            r2_object_key=f"fast-lane/tenant_1/{uuid.uuid4()}/stuck.jpg",
            file_size_bytes=1024,
        )
        workspace = photo.scene.event.workspace
        workspace.storage_used_bytes = 9000
        workspace.save(update_fields=["storage_used_bytes"])
        Photo.objects.filter(pk=photo.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )

        r2_object_exists_stub.side_effect = OSError("r2 unavailable")

        result = reap_abandoned_uploads.apply()

        assert result.successful()
        assert result.get()["status"] == "deferred"
        photo.refresh_from_db()
        workspace.refresh_from_db()
        assert photo.status == "PENDING"
        assert workspace.storage_used_bytes == 9000
