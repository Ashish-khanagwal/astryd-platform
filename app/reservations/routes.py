from datetime import date as date_type
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app.availability.service import ConflictError
from app.extensions import mongo
from app.notifications.tasks import send_confirmation_email
from app.reservations.service import (
    create_reservation,
    get_reservation_by_code,
    serialize_reservation,
    update_reservation,
)

bp = Blueprint("reservations", __name__, url_prefix="/reservations")


@bp.post("")
def create():
    payload = request.get_json(silent=True)
    error = _validate_create_payload(payload)
    if error:
        return jsonify(error="validation_error", message=error), 400
    try:
        reservation_id, reservation = create_reservation(mongo.db, payload)
    except ConflictError as exc:
        return jsonify(error="conflict", message=str(exc)), 409
    _queue_confirmation_email(reservation_id, reservation)
    booking = reservation["booking"]
    guest = reservation["guest"]
    return jsonify(
        success=True,
        confirmation_code=reservation["confirmation_code"],
        reservation={
            "date": booking["date"],
            "time_slot": booking["time_slot"],
            "time_display": booking["time_display"],
            "party_size": booking["party_size"],
            "seating_preference": booking["seating_preference"],
            "guest_name": guest["full_name"],
            "guest_email": guest["email"],
        },
    ), 201


@bp.get("/<confirmation_code>")
def get_by_code(confirmation_code):
    reservation = get_reservation_by_code(mongo.db, confirmation_code)
    if reservation is None:
        return _not_found()
    return jsonify(serialize_reservation(reservation))


@bp.patch("/<confirmation_code>")
def modify(confirmation_code):
    payload = request.get_json(silent=True)
    error = _validate_modify_payload(payload)
    if error:
        return jsonify(error="validation_error", message=error), 400
    reservation = get_reservation_by_code(mongo.db, confirmation_code)
    if reservation is None:
        return _not_found()
    if reservation["status"] == "cancelled":
        return jsonify(error="invalid_state", message="Cannot modify a cancelled reservation"), 400
    try:
        updated = update_reservation(mongo.db, reservation, payload)
    except ConflictError as exc:
        return jsonify(error="conflict", message=str(exc)), 409
    return jsonify(serialize_reservation(updated))


@bp.delete("/<confirmation_code>")
def cancel(confirmation_code):
    reservation = get_reservation_by_code(mongo.db, confirmation_code)
    if reservation is None:
        return _not_found()
    if reservation["status"] == "cancelled":
        return jsonify(error="invalid_state", message="Reservation is already cancelled"), 400
    mongo.db.reservations.update_one({"_id": reservation["_id"]}, {"$set": {"status": "cancelled"}})
    return jsonify(success=True, message="Reservation cancelled successfully")


def _queue_confirmation_email(reservation_id, reservation):
    booking = reservation["booking"]
    guest = reservation["guest"]
    try:
        send_confirmation_email.delay(
            str(reservation_id), reservation["confirmation_code"], guest["email"],
            guest["full_name"], booking["date"], booking["time_display"],
        )
    except Exception:
        # Delivery is asynchronous and must not affect a confirmed reservation.
        current_app.logger.exception("Unable to enqueue confirmation email")


def _validate_create_payload(payload):
    if not isinstance(payload, dict):
        return "JSON request body is required"
    for field in ("business_id", "booking", "guest"):
        if not payload.get(field):
            return f"{field} is required"
    return _validate_booking(payload["booking"], required=True) or _validate_guest(payload["guest"])


def _validate_modify_payload(payload):
    if not isinstance(payload, dict):
        return "JSON request body is required"
    unexpected = set(payload) - {"date", "time_slot", "party_size", "seating_preference"}
    if unexpected:
        return f"Unsupported fields: {', '.join(sorted(unexpected))}"
    if not payload:
        return "At least one booking field is required"
    return _validate_booking(payload, required=False)


def _validate_booking(booking, required):
    if not isinstance(booking, dict):
        return "booking must be an object"
    fields = ("date", "time_slot", "party_size", "seating_preference")
    if required:
        for field in fields:
            if booking.get(field) is None:
                return f"booking.{field} is required"
    if "date" in booking:
        try:
            requested_date = datetime.strptime(booking["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return "booking.date must use YYYY-MM-DD format"
        if requested_date < date_type.today():
            return "booking.date must not be in the past"
    if "time_slot" in booking:
        try:
            datetime.strptime(booking["time_slot"], "%H:%M")
        except (TypeError, ValueError):
            return "booking.time_slot must use HH:MM format"
    if "party_size" in booking:
        party_size = booking["party_size"]
        if isinstance(party_size, bool) or not isinstance(party_size, int) or not 1 <= party_size <= 20:
            return "booking.party_size must be an integer between 1 and 20"
    if "seating_preference" in booking and booking["seating_preference"] not in {"Indoor", "Outdoor", "The Bar", "Private"}:
        return "booking.seating_preference is invalid"
    return None


def _validate_guest(guest):
    if not isinstance(guest, dict):
        return "guest must be an object"
    for field in ("full_name", "email", "phone"):
        if not isinstance(guest.get(field), str) or not guest[field].strip():
            return f"guest.{field} is required"
    return None


def _not_found():
    return jsonify(error="not_found", message="Reservation not found"), 404
