"""
gallery/notifications.py — EDA Client Notification System

Celery tasks for photographer-to-client gallery delivery notifications.

TRIGGER: Manual only. Fires when a Workspace owner toggles is_published=True
         on an Event. Never auto-fires based on photo processing state.

EMAIL POLICY:
  - Only sends if Event.client_email is set.
  - Idempotent: repeated publishes will re-send (photographers may re-notify).
  - Uses Django's email backend (configured in settings.py).
  - Renders a branded HTML template with photographer logo + gallery URL.
"""
import logging
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='gallery.notifications.send_gallery_ready_email',
)
def send_gallery_ready_email(self, event_id: str):
    """
    Send a branded 'Your gallery is ready' email to the photographer's client.

    Invoked by EventViewSet.perform_update() when is_published transitions
    from False → True. The photographer must have set client_email on the Event.

    Security:
      - client_email is set by the photographer (authenticated workspace owner).
      - Gallery URL uses the event slug (non-guessable due to crypto suffix).
      - Expiry date is computed from the workspace subscription tier's TTL.
    """
    from gallery.models import Event

    try:
        event = Event.objects.select_related('workspace__user').get(id=event_id)
    except Event.DoesNotExist:
        logger.warning(f"[NOTIFY] Event {event_id} not found. Aborting email send.")
        return

    if not event.client_email:
        logger.info(f"[NOTIFY] Event {event_id} has no client_email. Skipping.")
        return

    if not event.is_published:
        # Race condition guard: event was un-published between task enqueue and execution
        logger.info(f"[NOTIFY] Event {event_id} is no longer published. Skipping.")
        return

    workspace = event.workspace
    frontend_url = getattr(settings, 'FRONTEND_URL', 'https://app.photobox.app')
    gallery_url = f"{frontend_url}/gallery/{event.slug}"

    from gallery.ttl import gallery_ttl_days_for

    if event.expires_at:
        expiry_date = event.expires_at.strftime('%B %d, %Y')
        expiry_text = f"Your gallery will be available until {expiry_date}."
    else:
        ttl_days = gallery_ttl_days_for(workspace)
        if ttl_days > 0:
            expiry_date = (timezone.now() + timedelta(days=ttl_days)).strftime('%B %d, %Y')
            expiry_text = f"Your gallery will be available until {expiry_date}."
        else:
            expiry_text = "Your gallery is available indefinitely."

    context = {
        'client_name':        event.client_name or 'Valued Client',
        'photographer_name':  workspace.business_name,
        'event_title':        event.title,
        'gallery_url':        gallery_url,
        'expiry_text':        expiry_text,
        'photographer_logo':  getattr(workspace, 'logo_url', None),
        'support_email':      getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@photobox.app'),
    }

    try:
        html_content = render_to_string('email/gallery_ready.html', context)
        text_content = strip_tags(html_content)  # Plain-text fallback for accessibility

        subject = f"📸 Your photos from {event.title} are ready — {workspace.business_name}"

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[event.client_email],
            reply_to=[workspace.user.email] if workspace.user.email else None,
        )
        email.attach_alternative(html_content, 'text/html')
        email.send(fail_silently=False)

        logger.info(
            f"[NOTIFY] ✅ Gallery ready email sent for Event {event_id} "
            f"to {event.client_email}"
        )

    except Exception as exc:
        logger.error(
            f"[NOTIFY] ❌ Email send failed for Event {event_id} "
            f"(attempt {self.request.retries + 1}): {exc}"
        )
        raise self.retry(exc=exc)
