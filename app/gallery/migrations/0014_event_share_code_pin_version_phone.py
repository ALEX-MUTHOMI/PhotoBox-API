# Generated manually for Kenya Pixieset roadmap Phase A/C

import secrets
import string

from django.db import migrations, models
from django.db.models import Q


def _generate_share_code(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def backfill_share_codes(apps, schema_editor):
    Event = apps.get_model("gallery", "Event")
    used = set(
        Event.objects.exclude(share_code__isnull=True)
        .exclude(share_code="")
        .values_list("share_code", flat=True)
    )
    for event in Event.objects.filter(Q(share_code__isnull=True) | Q(share_code="")):
        for _ in range(20):
            code = _generate_share_code()
            if code not in used:
                event.share_code = code
                event.save(update_fields=["share_code"])
                used.add(code)
                break
        else:
            raise RuntimeError(f"Could not backfill share_code for event {event.pk}")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0013_photo_phash_burst_cluster"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="share_code",
            field=models.CharField(blank=True, max_length=12, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="event",
            name="pin_version",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="event",
            name="client_phone",
            field=models.CharField(
                blank=True,
                help_text="Booking contact in E.164 (e.g. +2547…). Never exposed on public gallery JSON.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="clientallowlist",
            name="phone",
            field=models.CharField(
                blank=True,
                help_text="Optional E.164 phone for the main client (WhatsApp CRM).",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="galleryaccesssession",
            index=models.Index(
                fields=["gallery", "created_at"],
                name="gal_access_gallery_created_idx",
            ),
        ),
        migrations.RunPython(backfill_share_codes, noop_reverse),
    ]
