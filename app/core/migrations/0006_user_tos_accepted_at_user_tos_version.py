from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_workspace_storage_limit_bytes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="tos_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="tos_version",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
