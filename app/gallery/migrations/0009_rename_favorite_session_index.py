from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0008_merge_20260426_0000"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="favoriteselection",
            name="gallery_favo_session_4f1e2f_idx",
        ),
        migrations.AddIndex(
            model_name="favoriteselection",
            index=models.Index(
                fields=["session", "created_at"],
                name="gal_fav_session_created_idx",
            ),
        ),
    ]
