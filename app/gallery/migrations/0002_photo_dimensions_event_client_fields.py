# Generated manually — Phase 3 Delivery Layer fields + Phase 2 Notification fields
# Adds:
#   Photo: width, height (PositiveIntegerField, nullable) for masonry grid
#   Event: client_email, client_name for gallery notification system

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0001_initial'),
    ]

    operations = [
        # Photo: delivery layer dimensions
        migrations.AddField(
            model_name='photo',
            name='width',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Image pixel width. Set by Celery worker post-upload.',
            ),
        ),
        migrations.AddField(
            model_name='photo',
            name='height',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Image pixel height. Set by Celery worker post-upload.',
            ),
        ),
        # Event: client notification fields
        migrations.AddField(
            model_name='event',
            name='client_email',
            field=models.EmailField(
                blank=True,
                null=True,
                help_text="Client's email address. Gallery-ready notification is sent here on publish.",
            ),
        ),
        migrations.AddField(
            model_name='event',
            name='client_name',
            field=models.CharField(
                max_length=255,
                blank=True,
                null=True,
                help_text="Client's display name used in the notification email.",
            ),
        ),
    ]
