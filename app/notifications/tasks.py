"""Celery tasks for the centralized notification module."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='app.notifications.send_account_email', autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_account_email(recipient, purpose, link, organization_code=None):
    import smtplib
    from email.message import EmailMessage
    from flask import current_app
    cfg = current_app.config
    message = EmailMessage()
    message['From'], message['To'] = cfg['SMTP_FROM'], recipient
    message['Subject'] = f'Astryd: {purpose} your account'
    org_hint = f'Your Organization ID is {organization_code}.\n' if organization_code else ''
    message.set_content(f'{org_hint}Use this single-use link within one hour:\n{link}\nIf you did not request this, ignore this message.')
    with smtplib.SMTP(cfg['SMTP_HOST'], cfg['SMTP_PORT'], timeout=20) as client:
        client.starttls()
        if cfg['SMTP_USERNAME']:
            client.login(cfg['SMTP_USERNAME'], cfg['SMTP_PASSWORD'])
        client.send_message(message)


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
