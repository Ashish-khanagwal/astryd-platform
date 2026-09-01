"""Reservation persistence operations and response serialization."""

import secrets
from datetime import datetime

from pymongo.errors import DuplicateKeyError

from app.availability.service import ConflictError, find_available_table_atomic


def create_reservation(db, payload):
    """Persist a confirmed reservation after selecting a qualifying table."""
    booking = payload["booking"]
    guest = payload["guest"]
    table = find_available_table_atomic(
        db,
        payload["business_id"],
        booking["date"],
        booking["time_slot"],
        booking["party_size"],
        booking["seating_preference"],
    )
    time_display = to_time_display(booking["time_slot"])
    document = {
        "business_id": payload["business_id"],
        "confirmation_code": None,
        "booking": {
            "date": booking["date"],
            "time_slot": booking["time_slot"],
            "time_display": time_display,
            "party_size": booking["party_size"],
            "seating_preference": booking["seating_preference"],
            "table_id": table["_id"],
        },
        "guest": {
            "full_name": guest["full_name"],
            "email": guest["email"],
            "phone": guest["phone"],
            "special_requests": guest.get("special_requests", ""),
            "newsletter_opt_in": guest.get("newsletter_opt_in", False),
        },
        "status": "confirmed",
        "reminder_sent": False,
        "created_at": datetime.utcnow(),
    }
    for _ in range(5):
        document["confirmation_code"] = generate_confirmation_code()
        try:
            result = db.reservations.insert_one(document)
            return result.inserted_id, document
        except DuplicateKeyError:
            continue
    raise RuntimeError("Unable to generate a unique confirmation code")


def get_reservation_by_code(db, confirmation_code):
    return db.reservations.find_one({"confirmation_code": confirmation_code})


def update_reservation(db, reservation, changes):
    """Persist supplied booking fields, selecting a replacement table when needed."""
    booking = reservation["booking"]
    next_booking = {
        "date": changes.get("date", booking["date"]),
        "time_slot": changes.get("time_slot", booking["time_slot"]),
        "party_size": changes.get("party_size", booking["party_size"]),
        "seating_preference": changes.get("seating_preference", booking["seating_preference"]),
    }
    updates = {f"booking.{field}": value for field, value in changes.items()}
    if {"date", "time_slot"}.intersection(changes):
        table = find_available_table_atomic(
            db,
            reservation["business_id"],
            next_booking["date"],
            next_booking["time_slot"],
            next_booking["party_size"],
            next_booking["seating_preference"],
        )
        updates["booking.table_id"] = table["_id"]
    if "time_slot" in changes:
        updates["booking.time_display"] = to_time_display(changes["time_slot"])
    if updates:
        db.reservations.update_one({"_id": reservation["_id"]}, {"$set": updates})
        reservation = db.reservations.find_one({"_id": reservation["_id"]})
    return reservation


def serialize_reservation(reservation):
    """Return the public reservation representation, without internal fields."""
    booking = reservation["booking"]
    guest = reservation["guest"]
    created_at = reservation.get("created_at")
    return {
        "confirmation_code": reservation["confirmation_code"],
        "status": reservation["status"],
        "booking": {
            "date": booking["date"],
            "time_display": booking["time_display"],
            "party_size": booking["party_size"],
            "seating_preference": booking["seating_preference"],
        },
        "guest": {
            "full_name": guest["full_name"],
            "email": guest["email"],
            "phone": guest["phone"],
        },
        "created_at": created_at.isoformat() if created_at else None,
    }


def to_time_display(time_slot):
    return datetime.strptime(time_slot, "%H:%M").strftime("%I:%M %p").lstrip("0")


def generate_confirmation_code():
    return f"LUM-{secrets.randbelow(100000):05d}"
