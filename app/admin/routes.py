from math import ceil

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import mongo
from app.reservations.service import serialize_reservation

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/reservations")
@jwt_required()
def list_reservations():
    """List reservations for the business represented by the JWT identity."""
    business_id = get_jwt_identity()
    date = request.args.get("date")
    status = request.args.get("status")
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return jsonify(error="validation_error", message="page and limit must be integers"), 400
    criteria = {"business_id": business_id}
    if date:
        criteria["booking.date"] = date
    if status:
        criteria["status"] = status
    total = mongo.db.reservations.count_documents(criteria)
    cursor = mongo.db.reservations.find(criteria).sort("created_at", -1).skip((page - 1) * limit).limit(limit)
    return jsonify(
        reservations=[serialize_reservation(reservation) for reservation in cursor],
        total=total,
        page=page,
        limit=limit,
        pages=ceil(total / limit) if total else 0,
    )
