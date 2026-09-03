from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0010_event_allow_downloads"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="photo",
            index=models.Index(
                fields=["scene", "uploaded_at", "id"],
                name="gal_photo_scene_upload_id_idx",
            ),
        ),
    ]
