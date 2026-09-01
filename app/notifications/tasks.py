"""Celery tasks for the centralized notification module."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.notifications.send_confirmation_email")
def send_confirmation_email(
    reservation_id, confirmation_code, guest_email, guest_name, date, time_display
):
    """Queue placeholder confirmation delivery until an email provider is configured."""
    try:
        logger.info("Confirmation email queued for %s, code: %s", guest_email, confirmation_code)
    except Exception:
        logger.exception("Failed to queue confirmation email for reservation %s", reservation_id)
        raise
