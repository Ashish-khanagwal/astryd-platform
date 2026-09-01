from datetime import date as date_type
from datetime import datetime

from flask import Blueprint, jsonify, request

from app.availability.service import NotFoundError, get_available_slots
from app.extensions import mongo

bp = Blueprint("availability", __name__, url_prefix="/availability")


@bp.get("/<business_id>")
def availability(business_id):
    """Public availability lookup for an individual business."""
    date = request.args.get("date")
    party_size = request.args.get("party_size")
    error = _validate_query(date, party_size)
    if error:
        return jsonify(error="validation_error", message=error), 400
    try:
        result = get_available_slots(mongo.db, business_id, date, int(party_size))
    except NotFoundError as exc:
        return jsonify(error="not_found", message=str(exc)), 404
    return jsonify(result), 200


def _validate_query(date, party_size):
    if not date:
        return "date is required"
    try:
        requested_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return "date must use YYYY-MM-DD format"
    if requested_date < date_type.today():
        return "date must not be in the past"
    if party_size is None:
        return "party_size is required"
    try:
        value = int(party_size)
    except ValueError:
        return "party_size must be an integer"
    if not 1 <= value <= 20:
        return "party_size must be between 1 and 20"
    return None
