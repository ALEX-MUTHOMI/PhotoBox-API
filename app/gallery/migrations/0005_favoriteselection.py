from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0004_gallery_access_and_archive"),
    ]

    operations = [
        migrations.CreateModel(
            name="FavoriteSelection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "photo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_selections",
                        to="gallery.photo",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_selections",
                        to="gallery.galleryaccesssession",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AddConstraint(
            model_name="favoriteselection",
            constraint=models.UniqueConstraint(
                fields=("session", "photo"),
                name="unique_favorite_per_session_photo",
            ),
        ),
        migrations.AddIndex(
            model_name="favoriteselection",
            index=models.Index(fields=["session", "created_at"], name="gallery_favo_session_4f1e2f_idx"),
        ),
        migrations.AddIndex(
            model_name="favoriteselection",
            index=models.Index(fields=["photo", "created_at"], name="gallery_favo_photo_8930d4_idx"),
        ),
    ]
