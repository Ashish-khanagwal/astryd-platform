"""Single-use account links and database-backed request throttling."""
import hashlib
import secrets
from bson import ObjectId
from datetime import timedelta
from flask import current_app, request
from pymongo import ReturnDocument
from app.extensions import mongo
from app.platform.service import now


def limited(action, maximum=10):
    timestamp = now()
    bucket = int(timestamp.timestamp()) // 600
    key = hashlib.sha256(f'{action}:{request.remote_addr}:{bucket}'.encode()).hexdigest()
    record = mongo.db.api_rate_limits.find_one_and_update({'_id': key}, {'$inc': {'count': 1}, '$setOnInsert': {'expiresAt': timestamp + timedelta(minutes=20)}}, upsert=True, return_document=ReturnDocument.AFTER)
    return record['count'] > maximum


def issue_link(user, purpose):
    token = secrets.token_urlsafe(32)
    mongo.db.account_tokens.insert_one({'tokenHash': hashlib.sha256(token.encode()).hexdigest(), 'userId': user['_id'], 'purpose': purpose, 'expiresAt': now() + timedelta(hours=1)})
    from app.notifications.tasks import send_account_email
    link = current_app.config['PLATFORM_ADMIN_URL'].rstrip('/') + f'/reset-password?token={token}&purpose={purpose}'
    org = mongo.db.organizations.find_one({'_id': ObjectId(user['organizationId'])}) if user.get('organizationId') else None
    if current_app.config.get('SMTP_HOST'):
        try:
            send_account_email.delay(user['email'], purpose, link, org.get('code') if org else None)
            return True
        except Exception:
            # Provisioning has already committed. Do not report signup failure and
            # encourage a duplicate account when the separate delivery queue is down.
            current_app.logger.error('Account email could not be queued; account provisioning was preserved')
    else:
        current_app.logger.warning('Account email delivery is not configured')
    return False


def consume(token, purposes):
    if not isinstance(token, str) or len(token) > 256:
        return None
    return mongo.db.account_tokens.find_one_and_delete({'tokenHash': hashlib.sha256(token.encode()).hexdigest(), 'purpose': {'$in': purposes}, 'expiresAt': {'$gt': now()}})
