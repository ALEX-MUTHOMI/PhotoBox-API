"""
Contract: PhotoBox is pinned to Django 4.2.

Django 4.2 CheckConstraint only accepts `check=`. The `condition=` kwarg is
Django 5.1+. Models must import and expose `.check` so the app can boot.
"""
from django.db import models
from django.test import SimpleTestCase


class Django42ConstraintBootTests(SimpleTestCase):
    def test_gallery_archive_job_checkconstraint_uses_check(self):
        from gallery.models import GalleryArchiveJob

        constraints = [
            item
            for item in GalleryArchiveJob._meta.constraints
            if isinstance(item, models.CheckConstraint)
        ]
        self.assertTrue(constraints, "GalleryArchiveJob must keep a CheckConstraint.")
        for constraint in constraints:
            self.assertTrue(
                hasattr(constraint, "check"),
                "CheckConstraint must bind the Q object on .check (Django 4.2).",
            )
            self.assertIsNotNone(constraint.check)

    def test_subscription_checkconstraints_use_check(self):
        from billing.models import Subscription

        constraints = [
            item
            for item in Subscription._meta.constraints
            if isinstance(item, models.CheckConstraint)
        ]
        self.assertEqual(len(constraints), 2)
        for constraint in constraints:
            self.assertTrue(hasattr(constraint, "check"))
            self.assertIsNotNone(constraint.check)
