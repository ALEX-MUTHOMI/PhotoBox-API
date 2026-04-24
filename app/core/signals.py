from django.db import transaction
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from core.models import User
from core.tasks import purge_deleted_photographer_assets
from gallery.models import GalleryArchiveJob, Photo


@receiver(pre_delete, sender=User)
def collect_photographer_asset_keys(sender, instance, **kwargs):
    photo_keys = list(
        Photo.objects.filter(scene__event__workspace__user=instance)
        .exclude(r2_object_key__isnull=True)
        .exclude(r2_object_key="")
        .values_list("r2_object_key", flat=True)
    )
    archive_keys = list(
        GalleryArchiveJob.objects.filter(gallery__workspace__user=instance)
        .exclude(r2_zip_key__isnull=True)
        .exclude(r2_zip_key="")
        .values_list("r2_zip_key", flat=True)
    )
    instance._gdpr_r2_keys = sorted(set(photo_keys + archive_keys))


@receiver(post_delete, sender=User)
def enqueue_photographer_asset_purge(sender, instance, **kwargs):
    keys = list(getattr(instance, "_gdpr_r2_keys", []))
    transaction.on_commit(
        lambda: purge_deleted_photographer_assets.delay(str(instance.id), keys)
    )
