from django.db import migrations


def _assert_legacy_tables_empty(apps, schema_editor):
    Gallery = apps.get_model('core', 'Gallery')
    Image = apps.get_model('core', 'Image')
    gallery_count = Gallery.objects.count()
    image_count = Image.objects.count()
    if gallery_count or image_count:
        raise RuntimeError(
            f'Cannot drop legacy core.Gallery/core.Image: '
            f'{gallery_count} galleries and {image_count} images remain.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_softdelete_base_manager'),
    ]

    operations = [
        migrations.RunPython(_assert_legacy_tables_empty, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='Image',
        ),
        migrations.DeleteModel(
            name='Gallery',
        ),
    ]
