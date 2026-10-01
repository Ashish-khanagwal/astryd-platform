"""Owner-managed connection between website Sites and Astryd Main."""

from datetime import datetime

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.common.api import current_identity
from app.extensions import mongo
from app.integrations.website_sync import SUPPORTED_VERTICALS, enqueue, owner_candidates, validate_utc_offset

bp = Blueprint("website_sync", __name__, url_prefix="/website-sync")


def _owner(oid):
    user = current_identity()
    if user.get("role") != "owner" or user.get("organizationId") != oid:
        return None, (jsonify(error="forbidden"), 403)
    if not user.get("emailVerified"):
        return None, (jsonify(error="email_verification_required", message="Verify the owner email before linking Astryd Main"), 403)
    return user, None


@bp.get("/organizations/<oid>")
@jwt_required()
def get_link(oid):
    _, failure = _owner(oid)
    if failure:
        return failure
    link = mongo.db.website_sync_links.find_one({"organizationId": oid})
    sites = list(mongo.db.restaurants.find({"organizationId": oid, "vertical": {"$in": list(SUPPORTED_VERTICALS)}},
                                         {"restaurantId": 1, "name": 1, "vertical": 1}))
    for site in sites:
        site.pop("_id", None)
    result = {"mainOrgId": link.get("mainOrgId"), "mainOrgName": link.get("mainOrgName"),
              "sites": link.get("sites", {})} if link else None
    return jsonify(link=result, sites=sites)


@bp.get("/organizations/<oid>/candidates")
@jwt_required()
def candidates(oid):
    user, failure = _owner(oid)
    if failure:
        return failure
    try:
        return jsonify(organizations=owner_candidates(user["email"]))
    except Exception:
        return jsonify(error="main_unavailable", message="Astryd Main is unavailable"), 503


@bp.post("/organizations/<oid>/link")
@jwt_required()
def link(oid):
    user, failure = _owner(oid)
    if failure:
        return failure
    main_id = str((request.get_json(silent=True) or {}).get("mainOrgId") or "")
    try:
        match = next((candidate for candidate in owner_candidates(user["email"]) if candidate["id"] == main_id), None)
    except Exception:
        return jsonify(error="main_unavailable"), 503
    if not match:
        return jsonify(error="invalid_organization"), 400
    old = mongo.db.website_sync_links.find_one({"organizationId": oid})
    if old and old.get("mainOrgId") != main_id:
        return jsonify(error="already_linked", message="Contact the platform team to change an existing link"), 409
    mongo.db.website_sync_links.update_one(
        {"organizationId": oid},
        {"$set": {"mainOrgId": main_id, "mainOrgName": match["name"],
                   "ownerEmail": user["email"].strip().lower(), "updatedAt": datetime.utcnow()},
         "$setOnInsert": {"sites": {}, "createdAt": datetime.utcnow()}}, upsert=True,
    )
    return jsonify(mainOrgId=main_id, mainOrgName=match["name"])


@bp.put("/organizations/<oid>/sites/<sid>")
@jwt_required()
def configure_site(oid, sid):
    user, failure = _owner(oid)
    if failure:
        return failure
    site = mongo.db.restaurants.find_one({"organizationId": oid, "restaurantId": sid,
                                          "vertical": {"$in": list(SUPPORTED_VERTICALS)}})
    link = mongo.db.website_sync_links.find_one({"organizationId": oid})
    if not site or not link:
        return jsonify(error="not_found"), 404
    body = request.get_json(silent=True) or {}
    location_id = str(body.get("locationId") or "")
    offset = body.get("utcOffset")
    if not validate_utc_offset(offset):
        return jsonify(error="invalid_offset", message="Use an offset such as +05:30"), 400
    try:
        match = next((candidate for candidate in owner_candidates(user["email"])
                      if candidate["id"] == link["mainOrgId"]), None)
    except Exception:
        return jsonify(error="main_unavailable"), 503
    if not match or location_id not in {loc["id"] for loc in match["locations"]}:
        return jsonify(error="invalid_location"), 400
    tables = body.get("tableMappings") or {}
    if site["vertical"] == "restaurant":
        known = {str(table["_id"]) for table in mongo.db.tables.find({"business_id": sid, "is_active": True}, {"_id": 1})}
        if not isinstance(tables, dict) or set(tables) != known or any(type(number) is not int or not 1 <= number <= 30 for number in tables.values()):
            return jsonify(error="invalid_tables", message="Map every active website table to a POS table numbered 1–30"), 400
        if len(set(tables.values())) != len(tables):
            return jsonify(error="invalid_tables", message="POS table numbers must be unique within a Site"), 400
    else:
        tables = {}
    duration = body.get("durationMinutes", 120)
    if type(duration) is not int or not 1 <= duration <= 1440:
        return jsonify(error="invalid_duration"), 400
    mapping = {"locationId": location_id, "utcOffset": offset, "tableMappings": tables,
               "durationMinutes": duration, "kdsEnabled": bool(body.get("kdsEnabled", site["vertical"] == "restaurant"))}
    mongo.db.website_sync_links.update_one({"organizationId": oid},
                                            {"$set": {f"sites.{sid}": mapping, "updatedAt": datetime.utcnow()}})
    # Backfill this Site once its mapping becomes active. Upserts make this safe
    # to repeat after editing a mapping.
    for collection, entity_type in ((mongo.db.orders, "order"), (mongo.db.reservations, "booking")):
        for document in collection.find({"business_id": sid}, {"_id": 1}):
            enqueue(entity_type, document["_id"])
    return jsonify(siteId=sid, mapping=mapping)


@bp.get("/organizations/<oid>/sites/<sid>/tables")
@jwt_required()
def site_tables(oid, sid):
    _, failure = _owner(oid)
    if failure:
        return failure
    if not mongo.db.restaurants.find_one({"organizationId": oid, "restaurantId": sid}):
        return jsonify(error="not_found"), 404
    rows = mongo.db.tables.find({"business_id": sid, "is_active": True})
    return jsonify(tables=[{"id": str(row["_id"]), "name": row.get("name", "Table"),
                            "capacity": row.get("capacity")} for row in rows])


@bp.get("/organizations/<oid>/status")
@jwt_required()
def status(oid):
    _, failure = _owner(oid)
    if failure:
        return failure
    rows = mongo.db.website_sync_outbox.aggregate([
        {"$lookup": {"from": "restaurants", "localField": "siteId", "foreignField": "restaurantId", "as": "site"}},
        {"$match": {"site.organizationId": oid}},
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
    ])
    return jsonify(counts={row["_id"]: row["count"] for row in rows})
