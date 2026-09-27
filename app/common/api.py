"""Shared API serialization, tenant access, and auditing helpers."""

from datetime import datetime

from bson import ObjectId
from flask import jsonify
from flask_jwt_extended import get_jwt_identity, get_jwt


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
            if key in {"passwordHash", "tokenHash", "secret", "normalizedEmail"}:
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
    if not isinstance(identity, dict):
        return {}
    from app.extensions import mongo
    try:
        user = mongo.db.restaurant_users.find_one({"_id": ObjectId(identity.get("userId"))})
    except (TypeError, ValueError):
        return {}
    if not user or not user.get("isActive", True):
        return {}
    if identity.get('authVersion',0) != user.get('authVersion',0):
        return {}
    return {**user, "userId": str(user["_id"])}


def require_restaurant(restaurant_id, roles=None, permission=None):
    """Return identity or a tenant/role failure response for a route parameter."""
    identity = current_identity()
    from app.extensions import mongo
    site = mongo.db.restaurants.find_one({"restaurantId": restaurant_id})
    allowed = identity.get("restaurantId") == restaurant_id
    if site and identity.get("organizationId"):
        try:
            org = mongo.db.organizations.find_one({'_id': ObjectId(identity['organizationId']), 'status':'active'})
        except (ValueError,TypeError):
            org = None
        access = identity.get("siteAccess", [])
        allowed = bool(org) and site.get("organizationId") == identity["organizationId"] and (access == "all" or isinstance(access,list) and restaurant_id in access)
    if identity.get("role") == "super_admin":
        allowed = bool(site)
    if not allowed or (site and site.get("status", "active") == "suspended"):
        return None, (jsonify(error="forbidden", message="Restaurant access denied"), 403)
    if roles and identity.get("role") not in roles:
        return None, (jsonify(error="forbidden", message="Insufficient permissions"), 403)
    if permission and identity.get("role") != "super_admin" and not identity.get("permissions", {}).get(permission, identity.get("role") in {"owner", "admin"}):
        return None, (jsonify(error="forbidden", message="Insufficient permissions"), 403)
    return identity, None


def log_audit_event(db, restaurant_id, actor_user_id, actor_name, action, entity_type, entity_id, summary, diff=None):
    db.audit_logs.insert_one({
        "restaurantId": restaurant_id, "actorUserId": actor_user_id,
        "actorName": actor_name, "action": action, "entityType": entity_type,
        "entityId": str(entity_id), "summary": summary, "diff": diff,
        "createdAt": datetime.utcnow(),
    })
