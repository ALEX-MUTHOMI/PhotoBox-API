from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0011_photo_scene_uploaded_at_id_idx"),
    ]

    operations = [
        CreateExtension("pg_trgm"),
        migrations.AddIndex(
            model_name="photo",
            index=GinIndex(
                fields=["original_filename"],
                name="gal_photo_orig_fname_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
