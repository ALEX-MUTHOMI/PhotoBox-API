import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0012_photo_original_filename_trgm"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhotoBurstCluster",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("member_count", models.PositiveIntegerField(default=0)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                ("phash_version", models.PositiveSmallIntegerField(default=1)),
                ("hamming_threshold", models.PositiveSmallIntegerField(default=8)),
                (
                    "representative_photo",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="gallery.photo",
                    ),
                ),
                (
                    "scene",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="burst_clusters",
                        to="gallery.scene",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["scene", "computed_at"],
                        name="gal_burst_scene_computed_idx",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="photo",
            name="phash",
            field=models.BinaryField(
                blank=True,
                help_text="64-bit perceptual hash (8 bytes). Set offline after READY.",
                max_length=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="phash_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="photo",
            name="burst_cluster",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="photos",
                to="gallery.photoburstcluster",
            ),
        ),
        migrations.AddIndex(
            model_name="photo",
            index=models.Index(
                fields=["scene", "burst_cluster"],
                name="gal_photo_scene_burst_idx",
            ),
        ),
    ]
