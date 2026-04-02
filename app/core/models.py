"""
Database models for the PhotoBox API.
"""
import os
import uuid
from django.conf import settings

from django.db import models
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)


class UserManager(BaseUserManager):
    """Manager for users."""

    def create_user(self, email, password=None, **extra_fields):
        """Create, save and return a new user."""
        if not email:
            raise ValueError('User must have an email address.')
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self, email, password):
        """Create and return a new superuser."""
        user = self.create_user(email, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)

        return user


class User(AbstractBaseUser, PermissionsMixin):
    """User in the system."""

    # Enforced choices to prevent billing database errors
    class SubscriptionTier(models.TextChoices):
        FREE = 'FREE', 'Free Tier'
        PRO = 'PRO', 'Professional'
        AGENCY = 'AGENCY', 'Agency'

    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # --- PHOTOBOX SAAS FIELDS ---
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    subscription_tier = models.CharField(
        max_length=20,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE
    )
    storage_limit_gb = models.IntegerField(default=5)

    # Audit trails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'


class Workspace(models.Model):
    """Workspace object for isolating photographer environments."""

    # UUID prevents users from guessing workspace URLs (e.g., workspace/1)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    business_name = models.CharField(max_length=255)

    # null=True and unique=True prevents database crashes if two users leave this blank
    custom_domain = models.CharField(max_length=255, blank=True, null=True, unique=True)

    # Audit trails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name

class Gallery(models.Model):
    """Gallery object for grouping a photographer's images."""

    # UUID for secure, unguessable sharing links
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Linked to Workspace (Tenant Isolation)
    workspace = models.ForeignKey(
        'Workspace',
        on_delete=models.CASCADE,
        related_name='galleries'
    )

    # Core Data
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    # SaaS Features: Password protection and visibility toggles
    is_public = models.BooleanField(default=False)
    client_password = models.CharField(max_length=128, blank=True)

    # Audit Trails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

def workspace_image_file_path(instance, filename):
    """
    Generate file path for new image.
    Isolates files by Workspace UUID to prevent tenant data collisions.
    """
    ext = os.path.splitext(filename)[1]
    filename = f'{uuid.uuid4()}{ext}'

    # Creates a folder structure like: uploads/workspace/<workspace_uuid>/<random_uuid>.jpg
    return os.path.join('uploads', 'workspace', str(instance.gallery.workspace.id), filename)


class Image(models.Model):
    """Image object belonging to a gallery."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Locked to the Gallery
    gallery = models.ForeignKey(
        Gallery,
        on_delete=models.CASCADE,
        related_name='images'
    )

    title = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to=workspace_image_file_path)

    # SaaS UI Feature: Photographers need to drag-and-drop reorder their photos
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Automatically sort images by the custom order, then by newest first
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title if self.title else str(self.id)









# """
# Database models.
# """
# import uuid
# import os

# from django.conf import settings
# from django.db import models
# from django.contrib.auth.models import (
#     AbstractBaseUser,
#     BaseUserManager,
#     PermissionsMixin,
# )


# def recipe_image_file_path(instance, filename):
#     """Generate file path for new recipe image."""
#     ext = os.path.splitext(filename)[1]
#     filename = f'{uuid.uuid4()}{ext}'

#     return os.path.join('uploads', 'recipe', filename)


# class UserManager(BaseUserManager):
#     """Manager for users."""

#     def create_user(self, email, password=None, **extra_fields):
#         """Create, save and return a new user."""
#         if not email:
#             raise ValueError('User must have an email address.')
#         user = self.model(email=self.normalize_email(email), **extra_fields)
#         user.set_password(password)
#         user.save(using=self._db)

#         return user

#     def create_superuser(self, email, password):
#         """Create and return a new superuser."""
#         user = self.create_user(email, password)
#         user.is_staff = True
#         user.is_superuser = True
#         user.save(using=self._db)

#         return user


# class User(AbstractBaseUser, PermissionsMixin):
#     """User in the system."""
#     email = models.EmailField(max_length=255, unique=True)
#     name = models.CharField(max_length=255)
#     is_active = models.BooleanField(default=True)
#     is_staff = models.BooleanField(default=False)

#     objects = UserManager()

#     USERNAME_FIELD = 'email'


# class Recipe(models.Model):
#     """Recipe object."""
#     user = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#     )
#     title = models.CharField(max_length=255)
#     description = models.TextField(blank=True)
#     time_minutes = models.IntegerField()
#     price = models.DecimalField(max_digits=5, decimal_places=2)
#     link = models.CharField(max_length=255, blank=True)
#     tags = models.ManyToManyField('Tag')
#     ingredients = models.ManyToManyField('Ingredient')
#     image = models.ImageField(null=True, upload_to=recipe_image_file_path)

#     def __str__(self):
#         return self.title


# class Tag(models.Model):
#     """Tag for filtering recipes."""
#     name = models.CharField(max_length=255)
#     user = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#     )

#     def __str__(self):
#         return self.name


# class Ingredient(models.Model):
#     """Ingredient for recipes."""
#     name = models.CharField(max_length=255)
#     user = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#     )

#     def __str__(self):
#         return self.name
