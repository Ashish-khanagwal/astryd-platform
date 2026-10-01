"""Retryable website sync worker tasks."""

from celery import shared_task


@shared_task(bind=True, name="app.integrations.deliver_website_record", max_retries=8)
def deliver_website_record(self, entity_type, entity_id, version):
    from flask import current_app
    from app.extensions import mongo
    from app.integrations.website_sync import deliver
    try:
        return deliver(entity_type, entity_id, version)
    except Exception as exc:
        mongo.db.website_sync_outbox.update_one(
            {"entityType": entity_type, "entityId": entity_id, "version": version},
            {"$set": {"state": "pending", "lastError": str(exc)[:500]}},
        )
        current_app.logger.exception("Website sync delivery failed")
        raise self.retry(exc=exc, countdown=min(3600, 2 ** self.request.retries * 30))
