"""Astryd website to main-platform linking and reliable record delivery."""

import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bson import ObjectId
from flask import current_app
from pymongo import ReturnDocument

from app.extensions import mongo

SUPPORTED_VERTICALS = {"restaurant", "gym", "retail"}


def _configuration():
    base = current_app.config.get("ASTRYD_MAIN_API_URL", "").rstrip("/")
    secret = current_app.config.get("ASTRYD_MAIN_SYNC_SECRET", "")
    if not base or not secret:
        raise RuntimeError("Astryd main integration is not configured")
    return base, secret


def _request(path, body):
    base, secret = _configuration()
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    req = Request(base + path, raw, {
        "Content-Type": "application/json", "X-Astryd-Timestamp": stamp,
        "X-Astryd-Signature": signature,
    }, method="POST")
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def owner_candidates(email):
    return _request("/v1/integrations/website/owner-organizations", {"email": email})["organizations"]


def _safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items() if key not in {"_id", "payment_id"}}
    return value


def link_for_site(site_id):
    site = mongo.db.restaurants.find_one({"restaurantId": site_id})
    if not site or site.get("vertical") not in SUPPORTED_VERTICALS:
        return None
    link = mongo.db.website_sync_links.find_one({"organizationId": site.get("organizationId")})
    mapping = (link or {}).get("sites", {}).get(site_id)
    if not link or not mapping or not mapping.get("locationId") or not mapping.get("utcOffset"):
        return None
    return site, link, mapping


def enqueue(entity_type, entity_id):
    """Record a durable pending sync; never make checkout depend on the broker."""
    collection = mongo.db.orders if entity_type == "order" else mongo.db.reservations
    try:
        document = collection.find_one({"_id": ObjectId(str(entity_id))})
        if not document or not link_for_site(document.get("business_id")):
            return
        key = {"entityType": entity_type, "entityId": str(entity_id)}
        pending = mongo.db.website_sync_outbox.find_one_and_update(
            key,
            {"$inc": {"version": 1}, "$set": {"state": "pending", "siteId": document["business_id"], "updatedAt": datetime.utcnow()},
             "$setOnInsert": {"createdAt": datetime.utcnow()}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        try:
            from app.integrations.tasks import deliver_website_record
            deliver_website_record.delay(entity_type, str(entity_id), pending["version"])
        except Exception:
            current_app.logger.exception("Website sync queued locally; Celery dispatch unavailable")
    except Exception:
        current_app.logger.exception("Unable to queue website sync for %s/%s", entity_type, entity_id)


def deliver(entity_type, entity_id, version):
    collection = mongo.db.orders if entity_type == "order" else mongo.db.reservations
    document = collection.find_one({"_id": ObjectId(entity_id)})
    if not document:
        return "missing"
    context = link_for_site(document.get("business_id"))
    if not context:
        return "unlinked"
    site, link, mapping = context
    event = {
        "schema_version": 1, "type": "order" if entity_type == "order" else "booking",
        "source_id": entity_id, "version": version,
        "main_org_id": link["mainOrgId"], "owner_email": link["ownerEmail"],
        "site_id": site["restaurantId"], "vertical": site["vertical"],
        "location_id": mapping["locationId"],
    }
    if entity_type == "order":
        event["data"] = _safe(document)
        event["data"]["kds_enabled"] = bool(mapping.get("kdsEnabled", site["vertical"] == "restaurant"))
    else:
        event["data"] = _safe(document)
        event["data"]["utc_offset"] = mapping["utcOffset"]
        event["data"]["vertical"] = site["vertical"]
        event["data"]["duration_minutes"] = mapping.get("durationMinutes", 120)
        if site["vertical"] == "restaurant":
            table_id = str(document.get("booking", {}).get("table_id") or "")
            table_number = (mapping.get("tableMappings") or {}).get(table_id)
            if not table_number:
                raise ValueError("Website table has no POS mapping")
            event["table_number"] = table_number
    response = _request("/v1/integrations/website/events", event)
    state = "conflict" if response.get("status") == "conflict" else "delivered"
    mongo.db.website_sync_outbox.update_one(
        {"entityType": entity_type, "entityId": entity_id, "version": version},
        {"$set": {"state": state, "deliveredAt": datetime.utcnow(), "lastError": None}},
    )
    return state


def validate_utc_offset(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", value))
