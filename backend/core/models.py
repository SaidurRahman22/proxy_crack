"""Core models: the custom User and operator-managed Content."""

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.managers import UserManager


class TimeStampedModel(models.Model):
    """Abstract base giving every row a creation and modification stamp."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Django's user, identified by email instead of username.

    `username` is removed rather than left unused so there is exactly one
    identity field and no second, unvalidated login path.
    """

    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ("-date_joined",)

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)


class ContentKind(models.TextChoices):
    ANNOUNCEMENT = "announcement", "Announcement"
    NOTICE = "notice", "Server notice"
    ONBOARDING = "onboarding", "Onboarding copy"


class Content(TimeStampedModel):
    """Operator-authored copy served to the app: announcements, server
    notices and onboarding text. Addressed by a stable `key` so the client
    can reference a string without hardcoding it."""

    key = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Stable identifier the client references, e.g. 'welcome-hero'.",
    )
    kind = models.CharField(
        max_length=20,
        choices=ContentKind.choices,
        default=ContentKind.ANNOUNCEMENT,
        db_index=True,
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional display date; falls back to created_at.",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher sorts first within a kind.",
    )

    class Meta:
        ordering = ("-priority", "-created_at")
        indexes = [
            models.Index(fields=["kind", "is_published"], name="content_kind_pub_idx"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"
