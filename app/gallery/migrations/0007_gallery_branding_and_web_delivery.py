from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0006_galleryarchivejob_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="color_theme",
            field=models.CharField(default="linen-ink", max_length=64),
        ),
        migrations.AddField(
            model_name="event",
            name="cover_photo",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="event",
            name="typography_theme",
            field=models.CharField(default="editorial-serif", max_length=64),
        ),
        migrations.AddField(
            model_name="photo",
            name="web_r2_object_key",
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
    ]
