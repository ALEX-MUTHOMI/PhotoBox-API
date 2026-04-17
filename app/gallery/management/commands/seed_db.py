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
        'password': 'PhotoBox2026!',
        'name': 'Alex Muthomi',
        'business_name': 'Muthomi Studios',
        'tier': 'PRO',
    },
    {
        'email': 'jane@photobox.dev',
        'password': 'PhotoBox2026!',
        'name': 'Jane Wanjiru',
        'business_name': 'Wanjiru Photography',
        'tier': 'FREE',
    },
    {
        'email': 'admin@photobox.dev',
        'password': 'PhotoBox2026!',
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

        for photog_data in PHOTOGRAPHERS:
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
                    'brand_color': random.choice(['#1a1a2e', '#16213e', '#0f3460', '#533483']),
                }
            )

            status = '✅ Created' if created else '⏭️  Exists'
            self.stdout.write(f'  {status} User: {user.email} → Workspace: {workspace.business_name}')

            # Only the first photographer gets events (to keep it focused)
            if photog_data['email'] == 'alex@photobox.dev':
                for event_data in EVENTS:
                    event, ev_created = Event.objects.get_or_create(
                        workspace=workspace,
                        slug=event_data['slug'],
                        defaults={
                            'title': event_data['title'],
                            'event_type': event_data['event_type'],
                            'event_date': (timezone.now() - timedelta(days=random.randint(1, 90))).date(),
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

                        # Create mocked photos with fake R2 keys and Cloudinary URLs
                        for i in range(event_data['photos_per_scene']):
                            filename = random.choice(MOCK_FILENAMES)
                            w, h = random.choice(MOCK_DIMENSIONS)
                            file_size = random.randint(800_000, 4_500_000)  # 0.8-4.5 MB
                            photo_uuid = uuid.uuid4()

                            # MOCK R2 OBJECT KEY — simulates what process_fast_lane_asset creates
                            mock_r2_key = (
                                f"fast-lane/tenant_{workspace.id}/"
                                f"{photo_uuid}/{filename}"
                            )

                            Photo.objects.get_or_create(
                                id=photo_uuid,
                                defaults={
                                    'scene': scene,
                                    'original_filename': filename,
                                    'file_size_bytes': file_size,
                                    # MOCK: Simulates R2 upload completion
                                    'r2_object_key': mock_r2_key,
                                    'is_processed': True,
                                    'status': 'READY',
                                    # MOCK: Delivery layer dimensions for masonry grid
                                    'width': w,
                                    'height': h,
                                    'media_type': 'IMAGE',
                                }
                            )
                            total_photos += 1

                        if ev_created:
                            self.stdout.write(
                                f'      🖼️  Scene: {scene_title} '
                                f'({event_data["photos_per_scene"]} photos seeded)'
                            )

                    # Update workspace storage ledger with seeded bytes
                    total_bytes = sum(
                        p.file_size_bytes
                        for p in Photo.objects.filter(scene__event__workspace=workspace)
                    )
                    workspace.storage_used_bytes = total_bytes
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
