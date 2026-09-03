"""Fast Lane ?q= filename search (Postgres pg_trgm)."""
import unittest
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene


User = get_user_model()

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql",
    "pg_trgm filename search requires PostgreSQL",
)


@requires_postgres
class FilenameSearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="searcher@test.com",
            password="password123",
            name="Searcher",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Search Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Search Event",
            slug="search-event",
        )
        self.scene_a = Scene.objects.create(event=self.event, title="Scene A")
        self.scene_b = Scene.objects.create(event=self.event, title="Scene B")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.list_url = reverse("gallery:fastlane-photo-list")

        base = timezone.now()
        self.hero = Photo.objects.create(
            scene=self.scene_a,
            original_filename="Ceremony_Hero.jpg",
            file_size_bytes=1024,
        )
        self.typo_target = Photo.objects.create(
            scene=self.scene_a,
            original_filename="ceremony_001.jpg",
            file_size_bytes=1024,
        )
        self.other = Photo.objects.create(
            scene=self.scene_a,
            original_filename="reception_toast.jpg",
            file_size_bytes=1024,
        )
        self.scene_b_hero = Photo.objects.create(
            scene=self.scene_b,
            original_filename="Ceremony_Hero_B.jpg",
            file_size_bytes=1024,
        )
        Photo.objects.filter(pk=self.hero.pk).update(uploaded_at=base - timedelta(seconds=1))
        Photo.objects.filter(pk=self.typo_target.pk).update(
            uploaded_at=base - timedelta(seconds=2)
        )
        Photo.objects.filter(pk=self.other.pk).update(uploaded_at=base - timedelta(seconds=3))
        Photo.objects.filter(pk=self.scene_b_hero.pk).update(
            uploaded_at=base - timedelta(seconds=4)
        )

    def _ids(self, res):
        return {row["id"] for row in res.data["results"]}

    def test_q_substring_match_case_insensitive(self):
        res = self.client.get(self.list_url, {"q": "ceremony", "scene": str(self.scene_a.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = self._ids(res)
        self.assertIn(str(self.hero.id), ids)
        self.assertIn(str(self.typo_target.id), ids)
        self.assertNotIn(str(self.other.id), ids)

    def test_q_fuzzy_typo_match(self):
        res = self.client.get(self.list_url, {"q": "ceremny", "scene": str(self.scene_a.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = self._ids(res)
        self.assertTrue(
            str(self.hero.id) in ids or str(self.typo_target.id) in ids,
            msg="Expected fuzzy match on ceremony* filenames",
        )
        self.assertNotIn(str(self.other.id), ids)

    def test_empty_q_is_noop(self):
        plain = self.client.get(self.list_url, {"scene": str(self.scene_a.id)})
        empty = self.client.get(self.list_url, {"scene": str(self.scene_a.id), "q": ""})
        spaces = self.client.get(self.list_url, {"scene": str(self.scene_a.id), "q": "   "})
        self.assertEqual(plain.status_code, status.HTTP_200_OK)
        self.assertEqual(self._ids(plain), self._ids(empty))
        self.assertEqual(self._ids(plain), self._ids(spaces))

    def test_q_respects_tenant_isolation(self):
        rival = User.objects.create_user(
            email="rival-search@test.com",
            password="password123",
            name="Rival",
            accepted_terms=True,
        )
        rival_ws = Workspace.objects.create(user=rival, business_name="Rival")
        rival_event = Event.objects.create(
            workspace=rival_ws, title="Rival", slug="rival-search"
        )
        rival_scene = Scene.objects.create(event=rival_event, title="Rival Scene")
        rival_photo = Photo.objects.create(
            scene=rival_scene,
            original_filename="ceremony_secret.jpg",
            file_size_bytes=1024,
        )

        res = self.client.get(self.list_url, {"q": "ceremony"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = self._ids(res)
        self.assertNotIn(str(rival_photo.id), ids)
        self.assertIn(str(self.hero.id), ids)

    def test_q_composes_with_scene_filter(self):
        res = self.client.get(
            self.list_url,
            {"scene": str(self.scene_a.id), "q": "Hero"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = self._ids(res)
        self.assertIn(str(self.hero.id), ids)
        self.assertNotIn(str(self.scene_b_hero.id), ids)

    def test_q_plus_cursor_no_overlap(self):
        base = timezone.now()
        for i in range(25):
            p = Photo.objects.create(
                scene=self.scene_a,
                original_filename=f"photo_match_{i:02d}.jpg",
                file_size_bytes=1024,
            )
            Photo.objects.filter(pk=p.pk).update(uploaded_at=base - timedelta(seconds=i + 10))

        first = self.client.get(
            self.list_url,
            {"scene": str(self.scene_a.id), "q": "photo_match", "page_size": 10},
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first.data["results"]), 10)
        self.assertTrue(first.data["has_more"])
        first_ids = [row["id"] for row in first.data["results"]]

        second = self.client.get(
            self.list_url,
            {
                "scene": str(self.scene_a.id),
                "q": "photo_match",
                "page_size": 10,
                "cursor": first.data["next_cursor"],
            },
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_ids = [row["id"] for row in second.data["results"]]
        self.assertFalse(set(first_ids) & set(second_ids))

    def test_q_like_metacharacters_do_not_widen(self):
        underscore = Photo.objects.create(
            scene=self.scene_a,
            original_filename="a_b.jpg",
            file_size_bytes=1024,
        )
        Photo.objects.create(
            scene=self.scene_a,
            original_filename="axb.jpg",
            file_size_bytes=1024,
        )
        res = self.client.get(self.list_url, {"scene": str(self.scene_a.id), "q": "a_b"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = self._ids(res)
        self.assertIn(str(underscore.id), ids)

        percent = self.client.get(self.list_url, {"scene": str(self.scene_a.id), "q": "%"})
        self.assertEqual(percent.status_code, status.HTTP_200_OK)
        # Escaped '%' must not match every filename via LIKE wildcards.
        self.assertLessEqual(len(percent.data["results"]), 1)

    def test_q_too_long_returns_400(self):
        res = self.client.get(self.list_url, {"q": "x" * 65})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_q_still_blocked(self):
        anon = APIClient()
        res = anon.get(self.list_url, {"q": "ceremony"})
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
