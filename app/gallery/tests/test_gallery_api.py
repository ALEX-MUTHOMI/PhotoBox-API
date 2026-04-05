"""
Tests for the Gallery API.
"""
import io
from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Gallery, Workspace, Image

GALLERIES_URL = reverse('gallery:gallery-list')
# Assuming standard DRF router naming conventions
IMAGES_URL = reverse('gallery:image-list')

def detail_url(gallery_id):
    """Create and return a gallery detail URL."""
    return reverse('gallery:gallery-detail', args=[gallery_id])

def create_user(**params):
    return get_user_model().objects.create_user(**params)

def generate_test_image():
    """Generates a mathematically valid temporary image file in memory."""
    file_obj = io.BytesIO()
    image = PILImage.new('RGB', size=(100, 100), color=(255, 0, 0))
    image.save(file_obj, 'JPEG')
    file_obj.seek(0)
    return SimpleUploadedFile('test.jpg', file_obj.read(), content_type='image/jpeg')


# ==========================================
# 1. GALLERY TESTS
# ==========================================
class PrivateGalleryApiTests(TestCase):
    """Test authenticated API requests."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='photographer@example.com', password='testpass123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Apex Photo')
        self.client.force_authenticate(self.user)

    def test_retrieve_galleries_ignores_deleted(self):
        """Test retrieving galleries only returns active, non-deleted ones."""
        Gallery.objects.create(workspace=self.workspace, title='Active', slug='active')
        Gallery.objects.create(workspace=self.workspace, title='Trashed', slug='trashed', is_deleted=True)

        res = self.client.get(GALLERIES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1) # Proves the trash can works!
        self.assertEqual(res.data[0]['title'], 'Active')

    def test_create_gallery_with_pin_is_hashed(self):
        """SECURITY: Test creating a gallery with a PIN automatically hashes it."""
        payload = {
            'title': 'Secret Wedding',
            'slug': 'secret-wedding',
            'is_public': False,
            'gallery_pin': '2026'
        }
        res = self.client.post(GALLERIES_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        gallery = Gallery.objects.get(id=res.data['id'])
        self.assertNotEqual(gallery.gallery_pin, '2026')
        self.assertTrue(gallery.verify_pin('2026'))
        self.assertNotIn('gallery_pin', res.data)

    def test_delete_gallery_is_soft_delete(self):
        """Test that deleting via the API only soft-deletes the gallery."""
        gallery = Gallery.objects.create(workspace=self.workspace, title='To Delete', slug='delete')

        url = detail_url(gallery.id)
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        gallery.refresh_from_db()
        self.assertTrue(gallery.is_deleted)


# ==========================================
# 2. THE CARGO BAY (Image Upload Security)
# ==========================================
class ImageUploadSecurityTests(TestCase):
    """Red Team attack scripts for the Image Upload Pipeline."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='hacker@example.com', password='testpass123')

        # Give the user a 5GB storage limit for testing
        self.user.storage_limit_gb = 5
        self.user.save()

        self.workspace = Workspace.objects.create(user=self.user, business_name='Hacker Studio')
        self.gallery = Gallery.objects.create(workspace=self.workspace, title='Test Gallery', slug='test')
        self.client.force_authenticate(self.user)

    def test_cross_tenant_hijacking_shield(self):
        """SECURITY: Ensure a user cannot upload to a gallery they do not own."""
        victim_user = create_user(email='victim@example.com', password='secure')
        victim_workspace = Workspace.objects.create(user=victim_user, business_name='Victim Studio')
        victim_gallery = Gallery.objects.create(workspace=victim_workspace, title='Wedding', slug='wedding')

        # The hacker tries to inject an image into the victim's gallery
        payload = {
            'gallery': victim_gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_ghost_injection_shield(self):
        """SECURITY: Ensure files cannot be uploaded to a deleted gallery."""
        deleted_gallery = Gallery.objects.create(
            workspace=self.workspace,
            title='Trashed',
            slug='trash',
            is_deleted=True
        )

        payload = {
            'gallery': deleted_gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malware_mime_spoofing_shield(self):
        """SECURITY: Ensure disguised executable scripts are violently rejected."""
        # The hacker renames a PHP script to .jpg to bypass frontend checks
        malicious_file = SimpleUploadedFile(
            'wedding_photo.jpg',
            b"<?php system($_GET['cmd']); ?>", # Fake PHP payload
            content_type='image/jpeg' # Spoofed HTTP Header
        )

        payload = {
            'gallery': self.gallery.id,
            'image': malicious_file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        # The Pillow Cryptographic inspector must catch the fake bytes and reject it
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Malware Shield', str(res.data))

    def test_payload_too_large_shield(self):
        """SECURITY: Ensure files over 25MB are dropped before memory processing."""
        # Create a dummy payload of exactly 26 Megabytes of zeroes
        massive_file = SimpleUploadedFile('massive.jpg', b'0' * 26 * 1024 * 1024, content_type='image/jpeg')

        payload = {
            'gallery': self.gallery.id,
            'image': massive_file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('too large', str(res.data))

    def test_denial_of_wallet_quota_shield(self):
        """SECURITY: Ensure uploads are blocked if the user exceeds their SaaS plan."""
        # Simulate the user already having 5 Gigabytes of data in the database
        Image.objects.create(
            gallery=self.gallery,
            file_size_bytes=5 * 1024 * 1024 * 1024 # 5 GB mathematically
        )

        # They try to upload one more small file
        payload = {
            'gallery': self.gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        # The Quota Shield must trigger and block the upload
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Quota Exceeded', str(res.data))








# **THE VERDICT: STRONG PERIMETER, BUT THE CARGO BAY IS EXPOSED.**

# As your Lead Security Engineer, I am incredibly proud of this code. You have successfully implemented Tenant Isolation (`gallery.workspace.user != self.request.user`) and Ghost Injection prevention (`gallery.is_deleted`).

# Most developers don't even think about what happens if someone uploads a file to a deleted folder. You did. This is Top 5% engineering.

# **However, as a Hacker, I can bypass your Malware Shield in about 15 seconds.**

# Here is the Senior Code Review and the exact vulnerabilities you have left open in the PhotoBox SaaS.

# ### 🚨 Vulnerability 1: MIME Spoofing (The Trojan Horse)
# Look at this line:
# `if image_file.content_type not in allowed_mime_types:`

# You are relying on `content_type`. The problem? **The `content_type` is determined by the HTTP header sent by the client, not the actual file.**
# If I am a hacker, I will write a malicious script called `shell.php`. I will open Postman, attach my script, and manually type `Content-Type: image/jpeg` in the request header. Your Django server will read the header, say "Looks good to me!", and save my PHP virus directly into your AWS S3 bucket.

# ### 🚨 Vulnerability 2: Pixel Flooding (The Memory Bomb)
# Let's say I actually upload a real image. I can craft a completely valid `.jpg` that is only 10KB in file size (easily passing your 25MB check). But I will edit the metadata to tell the computer the image is `100,000 x 100,000` pixels.
# When your server (or another photographer's browser) tries to generate a thumbnail of that image, it will attempt to unpack 10 billion pixels into RAM. Your server will instantly run out of memory, crash, and cause a **Denial of Service (DoS)** for all your paying customers.

# ### 🚨 Vulnerability 3: The "Denial of Wallet" Attack (SaaS Quotas)
# PhotoBox is a business. Cloud storage costs money.
# You are limiting individual files to 25MB, which is great. But you have **no aggregate quota check**. A hacker (or just a really annoying free-tier user) can write a Python script to upload 100,000 different 25MB images overnight. By tomorrow morning, they will have dumped 2.5 Terabytes of data into your cloud storage, and Amazon AWS will send you a bill that bankrupts your startup.

# ---

# ### 🛠️ The Enterprise Patch (Upgrading the Vault)

# To fix Vulnerabilities 1 and 2, we must stop trusting the HTTP headers and start mathematically inspecting the binary file itself using **Pillow** (Python's industry-standard imaging library).

# First, ensure Pillow is in your environment (it usually installs with Django's ImageField, but it's good to know).

# Then, update the `perform_create` method inside your `ImageViewSet`.

# ```python
# # Add this to your imports at the top of views.py
# from PIL import Image as PILImage
# from PIL import UnidentifiedImageError

# # ... inside your ImageViewSet ...

#     def perform_create(self, serializer):
#         """
#         SECURITY: Prevent Cross-Tenant Hijacking, Malware Spoofing, and Quota Abuse.
#         """
#         gallery = serializer.validated_data['gallery']

#         # 1. CROSS-TENANT HIJACKING & GHOST SHIELD
#         if gallery.workspace.user != self.request.user:
#             raise PermissionDenied("You do not have permission to upload to this gallery.")
#         if gallery.is_deleted:
#             raise ValidationError("You cannot upload images to a deleted gallery. Restore it first.")

#         # 2. MALWARE & DECOMPRESSION BOMB SHIELD
#         image_file = self.request.FILES.get('image')
#         if image_file:
#             # Check Absolute File Size (25MB Limit)
#             MAX_FILE_SIZE_MB = 25
#             if image_file.size > (MAX_FILE_SIZE_MB * 1024 * 1024):
#                 raise ValidationError(f"Payload too large. Maximum size is {MAX_FILE_SIZE_MB}MB.")

#             # CRYPTOGRAPHIC FILE INSPECTION: Do not trust the HTTP Content-Type header.
#             try:
#                 # Pillow actually reads the binary headers of the file
#                 with PILImage.open(image_file) as img:
#                     img.verify() # Verifies it is mathematically an image without decoding it

#                     # PIXEL BOMB SHIELD: Check actual dimensions, not just file size
#                     MAX_PIXELS = 10000 * 10000 # 100 Megapixels max
#                     if img.width * img.height > MAX_PIXELS:
#                          raise ValidationError("Image dimensions are dangerously large (Potential Pixel Bomb).")

#                     # Verify Format
#                     if img.format not in ['JPEG', 'PNG', 'WEBP']:
#                          raise ValidationError("Invalid file signature. Only actual JPEG, PNG, and WEBP files are permitted.")

#             except UnidentifiedImageError:
#                 raise ValidationError("Malware Shield: The uploaded file is disguised or corrupted.")

#             # Reset the file pointer so Django can actually save it after Pillow read it
#             image_file.seek(0)

#         # 3. TODO: SAAS BILLING / QUOTA CHECK
#         # We will need to check the user's subscription_tier and storage_limit_gb here later!

#         serializer.save()
# ```

# ### The CISO Next Steps
# By adding `PILImage.verify()`, you are now physically looking inside the file. A disguised PHP script will immediately throw an `UnidentifiedImageError` and get violently rejected. The vault is infinitely stronger.

# Update your code with this Deep Inspection logic. Once you confirm the code is patched, we need to talk about **Strike 4: Storage Quotas & Denial of Wallet**, because right now, your free-tier users have infinite storage.
