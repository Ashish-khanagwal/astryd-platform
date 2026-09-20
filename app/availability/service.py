"""Availability calculations and persisted booking configuration."""

from collections import Counter
from datetime import datetime, timedelta

from pymongo import ReturnDocument


class NotFoundError(Exception):
    """A requested persistence resource does not exist."""


class ConflictError(Exception):
    """A requested booking resource is no longer available."""


_EMPTY_SLOTS = {"afternoon": [], "evening": []}


def get_available_slots(db, business_id, date, party_size):
    """Return available booking slots for one business, date, and party size."""
    business = db.businesses.find_one({"business_id": business_id})
    if business is None:
        raise NotFoundError("Business not found")

    requested_date = datetime.strptime(date, "%Y-%m-%d").date()
    day_of_week = requested_date.weekday()
    operating_hours = _hours_for_day(business.get("operating_hours", []), day_of_week)
    if operating_hours is None or operating_hours.get("is_closed") is True:
        return {"available": False, "reason": "closed", "slots": _empty_slots()}

    if any(blocked_date.get("date") == date for blocked_date in business.get("blocked_dates", [])):
        return {"available": False, "reason": "blocked", "slots": _empty_slots()}

    time_slots = generate_time_slots(
        operating_hours["open_time"],
        operating_hours["close_time"],
        operating_hours["slot_duration_mins"],
    )
    disabled_slots = set(operating_hours.get("disabled_slots", []))
    time_slots = [time_slot for time_slot in time_slots if time_slot not in disabled_slots]
    eligible_tables = list(
        db.tables.find(
            {"business_id": business_id, "capacity": {"$gte": party_size}, "is_active": True},
            {"_id": 1},
        )
    )
    eligible_table_ids = [table["_id"] for table in eligible_tables]
    booked_by_slot = _booked_table_counts(db, business_id, date, eligible_table_ids)
    max_per_slot = operating_hours.get("max_per_slot")

    rendered_slots = _render_slots(time_slots, len(eligible_table_ids), booked_by_slot, max_per_slot)
    return {
        "available": any(slot["available"] for slots in rendered_slots.values() for slot in slots),
        "date": date,
        "party_size": party_size,
        "slots": rendered_slots,
    }


def find_available_table_atomic(db, business_id, date, time_slot, party_size, seating_preference):
    """Select a currently unbooked qualifying table using ``find_one_and_update``.

    The update intentionally assigns ``is_active`` to its current required value,
    so no hold is persisted. Reservation creation must still make its own
    transactional/unique confirmation decision before finalizing a booking.
    """
    booked_table_ids = [
        reservation["booking"]["table_id"]
        for reservation in db.reservations.find(
            {
                "business_id": business_id,
                "booking.date": date,
                "booking.time_slot": time_slot,
                "status": "confirmed",
            },
            {"booking.table_id": 1},
        )
        if reservation.get("booking", {}).get("table_id") is not None
    ]
    table = db.tables.find_one_and_update(
        {
            "business_id": business_id,
            "seating_type": seating_preference,
            "capacity": {"$gte": party_size},
            "is_active": True,
            "_id": {"$nin": booked_table_ids},
        },
        {"$set": {"is_active": True}},
        return_document=ReturnDocument.AFTER,
    )
    if table is None:
        raise ConflictError("This time slot is no longer available")
    return table


def _hours_for_day(operating_hours, day_of_week):
    """Find the operating-hours document whose stored day is the weekday integer."""
    return next((hours for hours in operating_hours if hours.get("day_of_week") == day_of_week), None)


def generate_time_slots(open_time, close_time, slot_duration_mins):
    """Generate HH:MM slots, treating a close time at/before open as next day."""
    start = datetime.strptime(open_time, "%H:%M")
    end = datetime.strptime(close_time, "%H:%M")
    if end <= start:
        end += timedelta(days=1)
    interval = timedelta(minutes=slot_duration_mins)
    slots = []
    while start < end:
        slots.append(start.strftime("%H:%M"))
        start += interval
    return slots


def get_availability_config(db, business_id):
    """Return the persisted availability configuration for one business."""
    business = db.businesses.find_one({"business_id": business_id})
    if business is None:
        raise NotFoundError("Business not found")
    return _serialize_availability_config(business)


def update_availability_config(db, business_id, operating_hours, blocked_dates):
    """Replace the real availability fields on the tenant's business document."""
    business = db.businesses.find_one_and_update(
        {"business_id": business_id},
        {
            "$set": {
                "operating_hours": operating_hours,
                "blocked_dates": blocked_dates,
                "updated_at": datetime.utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if business is None:
        raise NotFoundError("Business not found")
    return _serialize_availability_config(business)


def _serialize_availability_config(business):
    operating_hours = []
    stored_hours = business.get("operating_hours", [])
    for day_of_week in range(7):
        hours = _hours_for_day(stored_hours, day_of_week) or {
            "day_of_week": day_of_week,
            "open_time": "12:00",
            "close_time": "22:00",
            "slot_duration_mins": 60,
            "max_per_slot": 1,
            "is_closed": True,
            "disabled_slots": [],
        }
        disabled_slots = set(hours.get("disabled_slots", []))
        slots = generate_time_slots(
            hours["open_time"], hours["close_time"], hours["slot_duration_mins"]
        )
        operating_hours.append(
            {
                "day_of_week": day_of_week,
                "open_time": hours["open_time"],
                "close_time": hours["close_time"],
                "slot_duration_mins": hours["slot_duration_mins"],
                "max_per_slot": hours.get("max_per_slot") or 100,
                "is_closed": hours.get("is_closed", False),
                "disabled_slots": sorted(disabled_slots),
                "slots": [
                    {"time": time_slot, "is_open": time_slot not in disabled_slots}
                    for time_slot in slots
                ],
            }
        )
    return {
        "business_id": business["business_id"],
        "operating_hours": operating_hours,
        "blocked_dates": business.get("blocked_dates", []),
    }


def _booked_table_counts(db, business_id, date, eligible_table_ids):
    if not eligible_table_ids:
        return Counter()
    reservations = db.reservations.find(
        {
            "business_id": business_id,
            "booking.date": date,
            "status": {"$in": ["confirmed"]},
            "booking.table_id": {"$in": eligible_table_ids},
        },
        {"booking.time_slot": 1},
    )
    return Counter(
        reservation["booking"]["time_slot"]
        for reservation in reservations
        if reservation.get("booking", {}).get("time_slot") is not None
    )


def _render_slots(time_slots, eligible_table_count, booked_by_slot, max_per_slot):
    groups = _empty_slots()
    for time_slot in time_slots:
        booked_count = booked_by_slot[time_slot]
        available = eligible_table_count - booked_count > 0
        if max_per_slot is not None:
            available = available and booked_count < max_per_slot
        if time_slot < "12:00":
            continue
        group = "afternoon" if time_slot < "17:00" else "evening"
        groups[group].append(
            {
                "time": datetime.strptime(time_slot, "%H:%M").strftime("%I:%M %p").lstrip("0"),
                "time_24": time_slot,
                "available": available,
            }
        )
    return groups


def _empty_slots():
    return {key: list(value) for key, value in _EMPTY_SLOTS.items()}
