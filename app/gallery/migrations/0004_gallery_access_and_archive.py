from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0003_alter_photo_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="scene",
            name="visibility",
            field=models.CharField(
                choices=[("PUBLIC", "Public"), ("CLIENT_ONLY", "Client Only")],
                db_index=True,
                default="PUBLIC",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="visibility",
            field=models.CharField(
                choices=[("PUBLIC", "Public"), ("CLIENT_ONLY", "Client Only")],
                db_index=True,
                default="PUBLIC",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ClientAllowlist",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gallery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="client_allowlist",
                        to="gallery.event",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="GalleryAccessSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("role", models.CharField(choices=[("CLIENT", "Client"), ("GUEST", "Guest")], max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gallery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_sessions",
                        to="gallery.event",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="GalleryArchiveJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("PROCESSING", "Processing"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("r2_zip_key", models.CharField(blank=True, max_length=1024, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "gallery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="archive_jobs",
                        to="gallery.event",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GalleryMagicLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gallery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="magic_links",
                        to="gallery.event",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="clientallowlist",
            index=models.Index(fields=["gallery", "email"], name="gallery_clie_gallery_1c8b54_idx"),
        ),
        migrations.AddConstraint(
            model_name="clientallowlist",
            constraint=models.UniqueConstraint(
                fields=("gallery", "email"),
                name="unique_allowlisted_client_per_gallery",
            ),
        ),
        migrations.AddIndex(
            model_name="galleryaccesssession",
            index=models.Index(fields=["gallery", "email"], name="gallery_gal_gallery_1c0719_idx"),
        ),
        migrations.AddIndex(
            model_name="galleryaccesssession",
            index=models.Index(fields=["gallery", "role"], name="gallery_gal_gallery_57cbbe_idx"),
        ),
        migrations.AddIndex(
            model_name="galleryarchivejob",
            index=models.Index(fields=["gallery", "status"], name="gallery_gal_gallery_7fc576_idx"),
        ),
        migrations.AddIndex(
            model_name="gallerymagiclink",
            index=models.Index(fields=["gallery", "email"], name="gallery_gal_gallery_9a00da_idx"),
        ),
    ]
