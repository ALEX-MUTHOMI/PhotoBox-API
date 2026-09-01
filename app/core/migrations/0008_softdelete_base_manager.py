from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_workspace_watermark_settings'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='gallery',
            options={'base_manager_name': 'all_objects'},
        ),
        migrations.AlterModelOptions(
            name='image',
            options={'base_manager_name': 'all_objects'},
        ),
        migrations.AlterModelOptions(
            name='workspace',
            options={'base_manager_name': 'all_objects'},
        ),
    ]
