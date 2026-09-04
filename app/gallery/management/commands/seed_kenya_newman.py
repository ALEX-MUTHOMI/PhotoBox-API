"""Seed one published Kenya gallery for Newman (DAST/DEBUG compose only)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import Workspace
from gallery.models import Event, Scene, VisibilityChoices


NEWMAN_SHARE_CODE = "AbCdEfGhIj"
NEWMAN_PIN = "secret9"


class Command(BaseCommand):
    help = "Seed a fixed share_code gallery for Newman Kenya contracts."

    def handle(self, *args, **options):
        if not (settings.DEBUG or getattr(settings, "_PHOTBOX_DAST", False)):
            raise CommandError("seed_kenya_newman is only allowed when DEBUG or PHOTBOX_DAST.")

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            email="newman-owner@photobox.invalid",
            defaults={
                "name": "Newman Owner",
                "accepted_terms": True,
            },
        )
        if not user.has_usable_password():
            user.set_password("NewmanOwnerPassword123!")
            user.save(update_fields=["password"])

        workspace, _ = Workspace.objects.get_or_create(
            user=user,
            defaults={"business_name": "Newman Studio"},
        )

        event = Event.objects.filter(share_code=NEWMAN_SHARE_CODE).first()
        if event is None:
            event = Event(
                workspace=workspace,
                title="Newman Wedding",
                slug="newman-wedding",
                share_code=NEWMAN_SHARE_CODE,
                is_published=False,
            )
            event.save()
        event.set_pin(NEWMAN_PIN)
        event.is_published = True
        event.save(update_fields=["is_published"])

        Scene.objects.get_or_create(
            event=event,
            title="Ceremony",
            defaults={"visibility": VisibilityChoices.PUBLIC},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded share_code={NEWMAN_SHARE_CODE} pin={NEWMAN_PIN}"
            )
        )
