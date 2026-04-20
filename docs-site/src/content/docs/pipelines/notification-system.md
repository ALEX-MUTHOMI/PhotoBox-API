---
title: Notification System
description: Gallery-ready email behavior and the guardrails around publication-triggered notifications.
---

## Gallery ready email

**Trigger:** manual `is_published = True` transition on an event  
**Task:** `gallery.notifications.send_gallery_ready_email`

## Flow

1. `EventViewSet.perform_update()` detects `False -> True` on `is_published`.
2. The API dispatches a Celery task with the event identifier.
3. The worker renders the branded HTML email template.
4. The configured email backend sends the notification.
5. Failures retry up to three times with delays between attempts.

## Security and correctness rules

- The task only fires when `event.client_email` exists.
- The event queryset enforces tenant ownership before publication changes are accepted.
- The worker re-checks `is_published` at execution time to guard against races or reversals.
