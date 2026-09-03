"""Fast Lane photo list uses keyset pagination — never OFFSET."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.pagination import decode_photo_keyset_cursor, encode_photo_keyset_cursor


User = get_user_model()


class PaginationSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="paginator@test.com",
            password="password123",
            name="Paginator",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Pagination Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Big Event",
            slug="big-event",
        )
        self.scene = Scene.objects.create(event=self.event, title="Main Scene")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.list_url = reverse("gallery:fastlane-photo-list")

        base = timezone.now()
        photos = []
        for i in range(150):
            photos.append(
                Photo(
                    scene=self.scene,
                    original_filename=f"photo_{i:04d}.jpg",
                    r2_object_key=f"raw/tenant_1/scene_1/photo_{i:04d}.jpg",
                    file_size_bytes=1024,
                    uploaded_at=base - timedelta(seconds=i),
                )
            )
        Photo.objects.bulk_create(photos)
        # bulk_create skips auto_now_add; force distinct uploaded_at values.
        for i, photo in enumerate(Photo.objects.filter(scene=self.scene).order_by("id")):
            Photo.objects.filter(pk=photo.pk).update(
                uploaded_at=base - timedelta(seconds=i)
            )

    def test_photo_list_is_keyset_paginated(self):
        res = self.client.get(self.list_url, {"page_size": 50})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)
        self.assertIn("next_cursor", res.data)
        self.assertIn("has_more", res.data)
        self.assertNotIn("count", res.data)
        self.assertEqual(len(res.data["results"]), 50)
        self.assertTrue(res.data["has_more"])
        self.assertTrue(res.data["next_cursor"])

    def test_page_size_cap_prevents_dos(self):
        res = self.client.get(self.list_url, {"page_size": 999999})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(res.data["results"]), 100)

    def test_keyset_second_page_has_no_overlap_or_gap(self):
        first = self.client.get(self.list_url, {"page_size": 50})
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        cursor = first.data["next_cursor"]
        first_ids = [row["id"] for row in first.data["results"]]

        second = self.client.get(
            self.list_url,
            {"page_size": 50, "cursor": cursor},
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_ids = [row["id"] for row in second.data["results"]]

        self.assertEqual(len(second_ids), 50)
        self.assertFalse(set(first_ids) & set(second_ids))

        # Newest-first: last of page 1 is strictly newer-or-equal than first of page 2.
        last_first = Photo.objects.get(pk=first_ids[-1])
        first_second = Photo.objects.get(pk=second_ids[0])
        self.assertGreaterEqual(
            (last_first.uploaded_at, last_first.id),
            (first_second.uploaded_at, first_second.id),
        )

    def test_keyset_stable_when_uploaded_at_ties(self):
        tied_at = timezone.now() - timedelta(days=1)
        Photo.objects.filter(scene=self.scene).delete()
        a = Photo.objects.create(
            scene=self.scene,
            original_filename="a.jpg",
            r2_object_key="raw/a.jpg",
            file_size_bytes=10,
        )
        b = Photo.objects.create(
            scene=self.scene,
            original_filename="b.jpg",
            r2_object_key="raw/b.jpg",
            file_size_bytes=10,
        )
        c = Photo.objects.create(
            scene=self.scene,
            original_filename="c.jpg",
            r2_object_key="raw/c.jpg",
            file_size_bytes=10,
        )
        Photo.objects.filter(pk__in=[a.pk, b.pk, c.pk]).update(uploaded_at=tied_at)
        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()

        ordered = sorted([a, b, c], key=lambda p: (p.uploaded_at, p.id), reverse=True)
        page1 = self.client.get(self.list_url, {"page_size": 2})
        self.assertEqual(page1.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in page1.data["results"]],
            [str(ordered[0].id), str(ordered[1].id)],
        )
        page2 = self.client.get(
            self.list_url,
            {"page_size": 2, "cursor": page1.data["next_cursor"]},
        )
        self.assertEqual(
            [row["id"] for row in page2.data["results"]],
            [str(ordered[2].id)],
        )
        self.assertFalse(page2.data["has_more"])
        self.assertIsNone(page2.data["next_cursor"])

    def test_invalid_cursor_returns_400(self):
        res = self.client.get(self.list_url, {"cursor": "not-a-real-cursor"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_keyset_query_avoids_offset(self):
        first = self.client.get(self.list_url, {"page_size": 10})
        cursor = first.data["next_cursor"]
        with CaptureQueriesContext(connection) as ctx:
            res = self.client.get(
                self.list_url,
                {"page_size": 10, "cursor": cursor},
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        photo_selects = [
            q["sql"]
            for q in ctx.captured_queries
            if 'FROM "gallery_photo"' in q["sql"].replace("`", '"')
            or "FROM gallery_photo" in q["sql"].lower()
        ]
        self.assertTrue(photo_selects)
        for sql in photo_selects:
            self.assertNotIn("OFFSET", sql.upper())

    def test_cursor_round_trip_helpers(self):
        photo = Photo.objects.filter(scene=self.scene).first()
        encoded = encode_photo_keyset_cursor(photo.uploaded_at, photo.id)
        ts, pk = decode_photo_keyset_cursor(encoded)
        self.assertEqual(pk, photo.id)
        self.assertEqual(ts, photo.uploaded_at)
