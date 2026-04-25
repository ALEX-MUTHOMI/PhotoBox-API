from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0004_gallery_access_and_archive"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="clientallowlist",
            name="gallery_clie_gallery_1c8b54_idx",
        ),
        migrations.RemoveIndex(
            model_name="galleryaccesssession",
            name="gallery_gal_gallery_1c0719_idx",
        ),
        migrations.RemoveIndex(
            model_name="galleryaccesssession",
            name="gallery_gal_gallery_57cbbe_idx",
        ),
        migrations.RemoveIndex(
            model_name="galleryarchivejob",
            name="gallery_gal_gallery_7fc576_idx",
        ),
        migrations.RemoveIndex(
            model_name="gallerymagiclink",
            name="gallery_gal_gallery_9a00da_idx",
        ),
        migrations.AddIndex(
            model_name="clientallowlist",
            index=models.Index(
                fields=["gallery", "email"],
                name="gal_allow_gallery_email_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="galleryaccesssession",
            index=models.Index(
                fields=["gallery", "email"],
                name="gal_access_gallery_email_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="galleryaccesssession",
            index=models.Index(
                fields=["gallery", "role"],
                name="gal_access_gallery_role_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="galleryarchivejob",
            index=models.Index(
                fields=["gallery", "status"],
                name="gal_archive_gallery_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="gallerymagiclink",
            index=models.Index(
                fields=["gallery", "email"],
                name="gal_magic_gallery_email_idx",
            ),
        ),
    ]
