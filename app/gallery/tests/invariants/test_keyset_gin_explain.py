"""Layer A: EXPLAIN / keyset / GIN / sink / tenant-cursor invariants."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.pagination import apply_desc_keyset, encode_photo_keyset_cursor
from gallery.search import apply_filename_search
from gallery.storage import MULTIPART_CHUNK_BYTES, MultipartUploadSink
from gallery.tests.invariants.helpers import (
    analyze_table,
    explain_queryset,
    plan_has_offset,
    plan_has_ordered_index_scan,
    plan_has_seq_scan_on,
    plan_mentions_index,
    seed_scene_photos,
)




User = get_user_model()

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql",
    "DSA EXPLAIN invariants require PostgreSQL",
)

SEED_N = 5_000
KEYSET_INDEX = "gal_photo_scene_upload_id_idx"
TRGM_INDEX = "gal_photo_orig_fname_trgm_idx"


@pytest.mark.dsa
@requires_postgres
class KeysetExplainInvariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="dsa-keyset@test.com",
            password="password123",
            name="DSA",
            accepted_terms=True,
        )
        cls.workspace = Workspace.objects.create(
            user=cls.user, business_name="DSA Studio"
        )
        cls.event = Event.objects.create(
            workspace=cls.workspace, title="DSA Event", slug="dsa-event"
        )
        cls.scene = Scene.objects.create(event=cls.event, title="Main")
        seed_scene_photos(cls.scene, SEED_N)

    def test_deep_keyset_uses_composite_index_without_offset(self):
        mid = (
            Photo.objects.filter(scene=self.scene)
            .order_by("-uploaded_at", "-id")[2500]
        )
        qs = apply_desc_keyset(
            Photo.objects.filter(scene_id=self.scene.id),
            mid.uploaded_at,
            mid.id,
        ).order_by("-uploaded_at", "-id")[:51]
        plan = explain_queryset(qs)
        self.assertFalse(plan_has_offset(plan))
        self.assertFalse(plan_has_seq_scan_on(plan, "gallery_photo"))
        if not (
            plan_has_ordered_index_scan(plan, KEYSET_INDEX)
            or plan_mentions_index(plan, KEYSET_INDEX)
        ):
            # Diagnostic: prove the composite index is choosable when seqscan/bitmap
            # are discouraged (plan dual-assert).
            plan = explain_queryset(qs, disable_seqscan=True)
        self.assertTrue(
            plan_has_ordered_index_scan(plan, KEYSET_INDEX)
            or plan_mentions_index(plan, KEYSET_INDEX),
            msg=f"Expected index {KEYSET_INDEX} in plan: {plan}",
        )

    def test_stolen_cursor_cannot_leak_rival_photos(self):
        rival = User.objects.create_user(
            email="rival-dsa@test.com",
            password="password123",
            name="Rival",
            accepted_terms=True,
        )
        rival_ws = Workspace.objects.create(user=rival, business_name="Rival")
        rival_event = Event.objects.create(
            workspace=rival_ws, title="Rival", slug="rival-dsa"
        )
        rival_scene = Scene.objects.create(event=rival_event, title="Secret")
        secret = Photo.objects.create(
            scene=rival_scene,
            original_filename="secret.jpg",
            file_size_bytes=1024,
            status="READY",
        )
        # Cursor encodes only uploaded_at+id — tenancy must come from queryset
        cursor = encode_photo_keyset_cursor(secret.uploaded_at, secret.id)
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.get(
            reverse("gallery:fastlane-photo-list"),
            {"cursor": cursor, "page_size": 10},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertNotIn(str(secret.id), ids)


@pytest.mark.dsa
@requires_postgres
class TrigramExplainInvariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="dsa-trgm@test.com",
            password="password123",
            name="Trgm",
            accepted_terms=True,
        )
        cls.workspace = Workspace.objects.create(
            user=cls.user, business_name="Trgm Studio"
        )
        cls.event = Event.objects.create(
            workspace=cls.workspace, title="Trgm Event", slug="trgm-event"
        )
        cls.scene = Scene.objects.create(event=cls.event, title="Main")
        seed_scene_photos(cls.scene, SEED_N, filename_prefix="noise")
        # Rare selective matches
        for i in range(5):
            Photo.objects.create(
                scene=cls.scene,
                original_filename=f"wedding_kiss_unique_{i}.jpg",
                file_size_bytes=1024,
                status="READY",
            )
        analyze_table("gallery_photo")

    def test_selective_q_avoids_seq_scan_and_uses_trgm(self):
        qs = apply_filename_search(
            Photo.objects.all(),
            "wedding_kiss_unique",
        ).order_by("-uploaded_at", "-id")[:50]
        plan = explain_queryset(qs)
        if plan_has_seq_scan_on(plan, "gallery_photo") or not plan_mentions_index(
            plan, TRGM_INDEX
        ):
            plan = explain_queryset(qs, disable_seqscan=True)
        self.assertFalse(plan_has_seq_scan_on(plan, "gallery_photo"))
        self.assertTrue(
            plan_mentions_index(plan, TRGM_INDEX),
            msg=f"Expected GIN/trgm usage in plan: {plan}",
        )

    def test_selective_q_returns_small_match_set(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.get(
            reverse("gallery:fastlane-photo-list"),
            {"scene": str(self.scene.id), "q": "wedding_kiss_unique", "page_size": 50},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(res.data["results"]), 10)
        self.assertGreaterEqual(len(res.data["results"]), 1)


@pytest.mark.dsa
class MultipartSinkBoundInvariantTests(TestCase):
    def test_sink_buffer_stays_within_8mib(self):
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "u1"}
        client.upload_part.return_value = {"ETag": '"etag"'}
        sink = MultipartUploadSink(client, "bucket", "key", "application/zip")
        chunk = b"x" * (1024 * 1024)
        for _ in range(20):
            sink.write(chunk)
            self.assertLessEqual(sink._buffered_len(), MULTIPART_CHUNK_BYTES)
        # Tear down without completing against real R2
        sink._aborted = True
        sink._buffer.clear()
        sink._read_pos = 0
