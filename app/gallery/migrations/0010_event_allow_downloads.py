from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0009_rename_favorite_session_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='allow_downloads',
            field=models.BooleanField(
                default=True,
                help_text='When False, clients and guests may view but not download or export.',
            ),
        ),
    ]
