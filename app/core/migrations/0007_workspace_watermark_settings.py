import core.models
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_user_tos_accepted_at_user_tos_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="watermark_logo",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=core.models.workspace_watermark_file_path,
                validators=[core.models.validate_png_watermark],
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="watermark_opacity",
            field=models.PositiveSmallIntegerField(
                default=35,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
    ]
