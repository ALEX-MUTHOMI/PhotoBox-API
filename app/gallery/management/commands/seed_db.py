"""
management/commands/seed_db.py — Production-Grade Database Seeder

Seeds the database with realistic demo data for development, staging,
and investor demos. Simulates the full Photobox EDA lifecycle:

  1. Creates photographer users with workspaces
  2. Creates events with scenes
  3. Creates photos with MOCKED R2 object keys (no real R2 uploads)
  4. Sets delivery_url via Cloudinary Fetch proxy pattern (no real Cloudinary calls)
  5. Marks assets as READY with realistic dimensions

Usage:
    python manage.py seed_db
    python manage.py seed_db --flush  (wipe and reseed)

SECURITY: This command NEVER runs in production. Checks DEBUG flag.
"""
import uuid
import random
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.contrib.auth import get_user_model

from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()


# ==========================================
# MOCK DATA CONSTANTS
# ==========================================

PHOTOGRAPHERS = [
    {
        'email': 'alex@photobox.dev',
        'password': 'PhotoBox2026!',  # nosec B105 - non-production seed data credential.
        'name': 'Alex Muthomi',
        'business_name': 'Muthomi Studios',
        'tier': 'PRO',
    },
    {
        'email': 'jane@photobox.dev',
        'password': 'PhotoBox2026!',  # nosec B105 - non-production seed data credential.
        'name': 'Jane Wanjiru',
        'business_name': 'Wanjiru Photography',
        'tier': 'FREE',
    },
    {
        'email': 'admin@photobox.dev',
        'password': 'PhotoBox2026!',  # nosec B105 - non-production seed data credential.
        'name': 'System Admin',
        'business_name': 'PhotoBox HQ',
        'tier': 'PRO',
        'is_superuser': True,
    },
]

EVENTS = [
    {
        'title': 'The Kamau Wedding',
        'event_type': 'WEDDING',
        'slug': 'kamau-wedding-a1b2c3',
        'client_email': 'bride@example.com',
        'client_name': 'Sarah Kamau',
        'scenes': ['Getting Ready', 'Ceremony', 'Reception', 'First Dance', 'Portraits'],
        'photos_per_scene': 8,
    },
    {
        'title': 'Safaricom Annual Gala',
        'event_type': 'CORPORATE',
        'slug': 'safaricom-gala-d4e5f6',
        'client_email': 'events@safaricom.co.ke',
        'client_name': 'Corporate Events Team',
        'scenes': ['Red Carpet', 'Keynote', 'Networking', 'Awards'],
        'photos_per_scene': 6,
    },
    {
        'title': 'Baby Amara Shoot',
        'event_type': 'STUDIO',
        'slug': 'baby-amara-g7h8i9',
        'client_email': 'mama.amara@example.com',
        'client_name': 'Grace Odhiambo',
        'scenes': ['Studio Portraits', 'Candid'],
        'photos_per_scene': 5,
    },
]

# Realistic filenames for seeded photos
MOCK_FILENAMES = [
    'DSC_0001.jpg', 'DSC_0002.jpg', 'IMG_4521.jpg', 'IMG_4522.jpg',
    'portrait_001.jpg', 'portrait_002.jpg', 'candid_001.jpg', 'candid_002.jpg',
    'detail_ring.jpg', 'detail_bouquet.jpg', 'group_photo_001.jpg',
    'ceremony_wide.jpg', 'first_look.jpg', 'sunset_portrait.jpg',
    'dance_floor_001.jpg', 'cake_cutting.jpg', 'speeches_001.jpg',
    'venue_exterior.jpg', 'decor_001.jpg', 'guests_candid_001.jpg',
]

# Realistic image dimensions for masonry grid testing
MOCK_DIMENSIONS = [
    (4000, 6000),   # Portrait  (2:3)
    (6000, 4000),   # Landscape (3:2)
    (4000, 4000),   # Square    (1:1)
    (6000, 3375),   # Cinematic (16:9)
    (5472, 3648),   # Canon 5D  (3:2)
    (7952, 5304),   # Sony A7R  (3:2)
    (4928, 3264),   # Nikon D7  (3:2)
]


class Command(BaseCommand):
    help = 'Seeds the database with realistic demo data. Use --flush to wipe first.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete all existing seeded data before re-seeding.',
        )
        parser.add_argument(
            '--workspace-count',
            type=int,
            default=len(PHOTOGRAPHERS),
            help='Number of photographer workspaces to seed.',
        )
        parser.add_argument(
            '--events-per-workspace',
            type=int,
            default=None,
            help='Override the number of events per workspace.',
        )
        parser.add_argument(
            '--scenes-per-event',
            type=int,
            default=None,
            help='Override the number of scenes per event.',
        )
        parser.add_argument(
            '--photos-per-scene',
            type=int,
            default=None,
            help='Override the number of photos per scene.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='bulk_create batch size for seeded photos.',
        )

    def _workspace_seed_specs(self, workspace_count):
        specs = []
        for index in range(workspace_count):
            if index < len(PHOTOGRAPHERS):
                specs.append(PHOTOGRAPHERS[index])
                continue

            specs.append(
                {
                    'email': f'seeded-{index + 1:03d}@photobox.dev',
                    'password': 'PhotoBox2026!',  # nosec B105 - non-production seed data credential.
                    'name': f'Seeded Photographer {index + 1:03d}',
                    'business_name': f'Seeded Studio {index + 1:03d}',
                    'tier': 'PRO' if index % 2 == 0 else 'FREE',
                }
            )
        return specs

    def _event_blueprints(self, events_per_workspace, scenes_per_event, photos_per_scene):
        blueprints = []
        for index in range(events_per_workspace):
            template = EVENTS[index % len(EVENTS)]
            default_scene_titles = template['scenes']
            scene_count = scenes_per_event if scenes_per_event is not None else len(default_scene_titles)

            if scenes_per_event is None and index < len(EVENTS):
                scene_titles = list(default_scene_titles)
            else:
                scene_titles = [
                    f"{template['event_type'].title()} Scene {scene_index + 1:02d}"
                    for scene_index in range(scene_count)
                ]

            blueprints.append(
                {
                    'title': f"{template['title']} #{index + 1:02d}" if index >= len(EVENTS) else template['title'],
                    'event_type': template['event_type'],
                    'slug': f"{template['slug']}-{index + 1:02d}",
                    'client_email': template['client_email'],
                    'client_name': template['client_name'],
                    'scenes': scene_titles,
                    'photos_per_scene': (
                        photos_per_scene
                        if photos_per_scene is not None
                        else template['photos_per_scene']
                    ),
                }
            )
        return blueprints

    def _flush_photo_batch(self, photo_batch, batch_size):
        if not photo_batch:
            return 0, 0

        Photo.objects.bulk_create(photo_batch, batch_size=batch_size)
        bytes_inserted = sum(photo.file_size_bytes for photo in photo_batch)
        inserted_count = len(photo_batch)
        photo_batch.clear()
        return inserted_count, bytes_inserted

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                '🚫 SECURITY: seed_db is DISABLED in production (DEBUG=False). '
                'This command must only run in development/staging environments.'
            )

        if options['flush']:
            self.stdout.write(self.style.WARNING('⚠️  Flushing all gallery data...'))
            Photo.objects.all().delete()
            Scene.objects.all().delete()
            Event.objects.all().delete()
            Workspace.objects.all().delete()
            User.objects.filter(email__endswith='@photobox.dev').delete()
            self.stdout.write(self.style.SUCCESS('   Flushed.'))

        self.stdout.write(self.style.HTTP_INFO('\n🌱 PhotoBox Database Seeder'))
        self.stdout.write(self.style.HTTP_INFO('=' * 50))

        total_photos = 0
        workspace_specs = self._workspace_seed_specs(options['workspace_count'])
        events_per_workspace = (
            options['events_per_workspace']
            if options['events_per_workspace'] is not None
            else len(EVENTS)
        )
        event_blueprints = self._event_blueprints(
            events_per_workspace=events_per_workspace,
            scenes_per_event=options['scenes_per_event'],
            photos_per_scene=options['photos_per_scene'],
        )
        batch_size = max(1, options['batch_size'])

        for photog_data in workspace_specs:
            user, created = User.objects.get_or_create(
                email=photog_data['email'],
                defaults={
                    'name': photog_data['name'],
                    'is_staff': photog_data.get('is_superuser', False),
                    'is_superuser': photog_data.get('is_superuser', False),
                }
            )
            if created:
                user.set_password(photog_data['password'])
                user.save()

            workspace, _ = Workspace.objects.get_or_create(
                user=user,
                defaults={
                    'business_name': photog_data['business_name'],
                    'storage_limit_bytes': 10 * 1024 * 1024 * 1024,  # 10 GB
                    'brand_color': random.choice(['#1a1a2e', '#16213e', '#0f3460', '#533483']),  # nosec B311 - demo seed data only.
                }
            )

            status = '✅ Created' if created else '⏭️  Exists'
            self.stdout.write(f'  {status} User: {user.email} → Workspace: {workspace.business_name}')

            workspace_seeded_bytes = 0

            for event_index, event_data in enumerate(event_blueprints):
                with transaction.atomic():
                    event, ev_created = Event.objects.get_or_create(
                        workspace=workspace,
                        slug=event_data['slug'],
                        defaults={
                            'title': event_data['title'],
                            'event_type': event_data['event_type'],
                            'event_date': (
                                timezone.now() - timedelta(days=random.randint(1, 90))  # nosec B311
                            ).date(),
                            'is_published': True,
                            'client_email': event_data['client_email'],
                            'client_name': event_data['client_name'],
                        }
                    )

                    if ev_created:
                        self.stdout.write(f'    📸 Event: {event.title}')

                    for order, scene_title in enumerate(event_data['scenes']):
                        scene, _ = Scene.objects.get_or_create(
                            event=event,
                            title=scene_title,
                            defaults={'display_order': order}
                        )

                        existing_count = Photo.objects.filter(scene=scene).count()
                        target_count = event_data['photos_per_scene']
                        missing_count = max(0, target_count - existing_count)

                        photo_batch = []
                        scene_seeded_bytes = 0

                        for _ in range(missing_count):
                            filename = random.choice(MOCK_FILENAMES)  # nosec B311 - demo seed data only.
                            w, h = random.choice(MOCK_DIMENSIONS)  # nosec B311 - demo seed data only.
                            file_size = random.randint(800_000, 4_500_000)  # nosec B311 - demo seed data only.
                            photo_uuid = uuid.uuid4()
                            mock_r2_key = (
                                f"fast-lane/tenant_{workspace.id}/"
                                f"{photo_uuid}/{filename}"
                            )

                            photo_batch.append(
                                Photo(
                                    id=photo_uuid,
                                    scene=scene,
                                    original_filename=filename,
                                    file_size_bytes=file_size,
                                    r2_object_key=mock_r2_key,
                                    is_processed=True,
                                    status='READY',
                                    width=w,
                                    height=h,
                                    media_type='IMAGE',
                                )
                            )
                            scene_seeded_bytes += file_size

                            if len(photo_batch) >= batch_size:
                                inserted_count, inserted_bytes = self._flush_photo_batch(photo_batch, batch_size)
                                total_photos += inserted_count
                                workspace_seeded_bytes += inserted_bytes

                        inserted_count, inserted_bytes = self._flush_photo_batch(photo_batch, batch_size)
                        total_photos += inserted_count
                        workspace_seeded_bytes += inserted_bytes

                        if ev_created or missing_count > 0:
                            self.stdout.write(
                                f'      🖼️  Scene: {scene_title} '
                                f'({target_count} target / {missing_count} new)'
                            )

            workspace.storage_used_bytes = (
                Photo.objects.filter(scene__event__workspace=workspace)
                .aggregate(total=Sum('file_size_bytes'))
                .get('total') or 0
            )
            workspace.save(update_fields=['storage_used_bytes'])

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'✅ Seeding complete!'))
        self.stdout.write(f'   📊 Total photos: {total_photos}')
        self.stdout.write(f'   🔑 Login: alex@photobox.dev / PhotoBox2026!')
        self.stdout.write(f'   🔑 Admin: admin@photobox.dev / PhotoBox2026!')
        self.stdout.write('')

        # Verify delivery URLs work with mocked R2 keys
        sample = Photo.objects.filter(r2_object_key__isnull=False).first()
        if sample:
            self.stdout.write(self.style.HTTP_INFO('🔍 Delivery URL Verification:'))
            self.stdout.write(f'   delivery_url: {sample.delivery_url or "⚠️  Set CLOUDINARY_CLOUD_NAME + CLOUDFLARE_R2_DOMAIN in .env"}')
            self.stdout.write(f'   aspect_ratio: {sample.aspect_ratio}')
            self.stdout.write(f'   dimensions:   {sample.width}x{sample.height}')
            self.stdout.write(f'   r2_key:       {sample.r2_object_key}')
