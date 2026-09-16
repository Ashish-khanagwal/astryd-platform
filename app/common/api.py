"""Shared API serialization, tenant access, and auditing helpers."""

from datetime import datetime

from bson import ObjectId
from flask import jsonify
from flask_jwt_extended import get_jwt_identity


def camel_case(value):
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


def serialize_doc(value):
    """Make MongoDB values safe and conventionally shaped for TypeScript clients."""
    if isinstance(value, list):
        return [serialize_doc(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "passwordHash":
                continue
            result["id" if key == "_id" else camel_case(key)] = serialize_doc(item)
        return result
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    return value


def current_identity():
    identity = get_jwt_identity()
    return identity if isinstance(identity, dict) else {}


def require_restaurant(restaurant_id, roles=None):
    """Return identity or a tenant/role failure response for a route parameter."""
    identity = current_identity()
    if identity.get("restaurantId") != restaurant_id:
        return None, (jsonify(error="forbidden", message="Restaurant access denied"), 403)
    if roles and identity.get("role") not in roles:
        return None, (jsonify(error="forbidden", message="Insufficient permissions"), 403)
    return identity, None


def log_audit_event(db, restaurant_id, actor_user_id, actor_name, action, entity_type, entity_id, summary, diff=None):
    db.audit_logs.insert_one({
        "restaurantId": restaurant_id, "actorUserId": actor_user_id,
        "actorName": actor_name, "action": action, "entityType": entity_type,
        "entityId": str(entity_id), "summary": summary, "diff": diff,
        "createdAt": datetime.utcnow(),
    })
