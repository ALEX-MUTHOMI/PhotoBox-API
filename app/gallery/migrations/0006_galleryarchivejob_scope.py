from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0005_favoriteselection"),
    ]

    operations = [
        migrations.AddField(
            model_name="galleryarchivejob",
            name="access_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="archive_jobs",
                to="gallery.galleryaccesssession",
            ),
        ),
        migrations.AddField(
            model_name="galleryarchivejob",
            name="archive_type",
            field=models.CharField(
                choices=[("FULL", "Full Gallery"), ("FAVORITES", "Favorites Only")],
                db_index=True,
                default="FULL",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="galleryarchivejob",
            constraint=models.CheckConstraint(
                check=models.Q(archive_type="FULL") | models.Q(access_session__isnull=False),
                name="favorites_archives_require_access_session",
            ),
        ),
        migrations.AddIndex(
            model_name="galleryarchivejob",
            index=models.Index(
                fields=["gallery", "archive_type", "status"],
                name="gallery_gal_gallery_e48be8_idx",
            ),
        ),
    ]
