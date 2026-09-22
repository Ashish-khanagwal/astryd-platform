"""Provider-independent Astryd payment orchestration."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument

from app.availability.service import ConflictError, find_available_table_atomic, get_available_slots
from app.orders.service import serialize_order
from app.payments.exceptions import PaymentAccessError, PaymentProviderError, PaymentStateError
from app.reservations.service import create_reservation
from app.notifications.tasks import send_confirmation_email


FINIX_SUCCESS_STATES = {"SUCCEEDED"}
FINIX_FAILED_STATES = {"FAILED", "CANCELED", "CANCELLED"}


def create_payment_attempt(
    db,
    *,
    business_id,
    context_type,
    context_id,
    amount_cents,
    customer,
    snapshot=None,
    amount_override_cents=None,
):
    effective_amount_cents = (
        amount_override_cents if amount_override_cents is not None else amount_cents
    )
    if effective_amount_cents <= 0:
        raise PaymentStateError("Payment amount must be greater than zero")
    raw_secret = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    document = {
        "business_id": business_id,
        "context_type": context_type,
        "context_id": context_id,
        "amount_cents": effective_amount_cents,
        "currency": "USD",
        "status": "created",
        "fulfillment_status": "pending",
        "idempotency_id": str(uuid.uuid4()),
        "checkout_secret_hash": _hash_secret(raw_secret),
        "customer": customer,
        "context_snapshot": snapshot or {},
        "finix": {},
        "failure": {},
        "created_at": now,
        "updated_at": now,
    }
    result = db.payments.insert_one(document)
    document["_id"] = result.inserted_id
    return document, raw_secret


def create_reservation_payment(
    db, payload, deposit_per_guest_cents, amount_override_cents=None
):
    booking = payload["booking"]
    slots = get_available_slots(db, payload["business_id"], booking["date"], booking["party_size"])
    requested = [slot for group in slots.get("slots", {}).values() for slot in group if slot.get("time_24") == booking["time_slot"]]
    if not requested or not requested[0].get("available"):
        raise ConflictError("This time slot is no longer available")
    find_available_table_atomic(
        db,
        payload["business_id"],
        booking["date"],
        booking["time_slot"],
        booking["party_size"],
        booking["seating_preference"],
    )
    amount_cents = booking["party_size"] * deposit_per_guest_cents
    return create_payment_attempt(
        db,
        business_id=payload["business_id"],
        context_type="reservation",
        context_id=None,
        amount_cents=amount_cents,
        customer={
            "name": payload["guest"]["full_name"],
            "email": payload["guest"]["email"],
            "phone": payload["guest"]["phone"],
        },
        snapshot=payload,
        amount_override_cents=amount_override_cents,
    )


def submit_card_payment(db, payment_id, checkout_secret, token, client, fraud_session_id=None):
    payment = get_authorized_payment(db, payment_id, checkout_secret)
    if payment["status"] == "succeeded":
        return payment
    if payment["status"] == "pending":
        return payment
    if payment["status"] not in {"created"}:
        raise PaymentStateError("This payment attempt cannot accept a card token")
    if not isinstance(token, str) or not token.startswith("TK"):
        raise PaymentStateError("A valid Finix card token is required")

    claimed = db.payments.find_one_and_update(
        {"_id": payment["_id"], "status": "created"},
        {"$set": {"status": "submitting", "updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        latest = db.payments.find_one({"_id": payment["_id"]})
        if latest and latest.get("status") in {"pending", "succeeded"}:
            return latest
        raise PaymentStateError("This payment attempt is already being processed")
    payment = claimed

    tags = {
        "astryd_payment_id": str(payment["_id"]),
        "business_id": payment["business_id"],
        "context_type": payment["context_type"],
    }
    try:
        identity_id = payment.get("finix", {}).get("identity_id")
        if not identity_id:
            identity = client.create_identity(tags)
            identity_id = identity["id"]
            _set_finix(db, payment["_id"], "identity_id", identity_id)
        instrument = client.create_payment_instrument(identity_id, token)
        instrument_id = instrument["id"]
        _set_finix(db, payment["_id"], "payment_instrument_id", instrument_id)
        transfer = client.create_transfer(
            payment["amount_cents"],
            instrument_id,
            payment["idempotency_id"],
            tags,
            fraud_session_id,
        )
    except PaymentProviderError as exc:
        status = "provider_unknown" if exc.uncertain else "failed"
        db.payments.update_one(
            {"_id": payment["_id"]},
            {"$set": {"status": status, "failure": {"message": str(exc), "details": exc.details}, "updated_at": datetime.utcnow()}},
        )
        if not exc.uncertain:
            _mark_context_failed(db, payment["_id"])
        raise

    payment = apply_transfer_state(db, payment["_id"], transfer)
    return payment


def apply_transfer_state(db, payment_id, transfer):
    provider_state = str(transfer.get("state", "PENDING")).upper()
    if provider_state in FINIX_SUCCESS_STATES:
        status = "succeeded"
    elif provider_state in FINIX_FAILED_STATES:
        status = "failed"
    else:
        status = "pending"
    updates = {
        "status": status,
        "finix.transfer_id": transfer.get("id"),
        "finix.transfer_state": provider_state,
        "finix.raw_failure_code": transfer.get("failure_code"),
        "updated_at": datetime.utcnow(),
    }
    # Finix can deliver the terminal webhook before the create-transfer HTTP
    # response reaches us. Never let that older PENDING response overwrite a
    # terminal state that the webhook has already persisted.
    db.payments.update_one(
        {"_id": payment_id, "status": {"$nin": ["succeeded", "failed"]}},
        {"$set": updates},
    )
    payment = db.payments.find_one({"_id": payment_id})
    if status == "succeeded" and payment and payment.get("status") == "succeeded":
        fulfill_succeeded_payment(db, payment_id)
    elif status == "failed" and payment and payment.get("status") == "failed":
        _mark_context_failed(db, payment_id)
    return db.payments.find_one({"_id": payment_id})


def reconcile_transfer(db, transfer):
    transfer_id = transfer.get("id")
    if not transfer_id:
        return None
    payment = db.payments.find_one({"finix.transfer_id": transfer_id})
    if payment is None:
        tagged_payment_id = (transfer.get("tags") or {}).get("astryd_payment_id")
        try:
            payment = db.payments.find_one({"_id": ObjectId(tagged_payment_id)}) if tagged_payment_id else None
        except Exception:
            payment = None
    if payment is None:
        return None
    return apply_transfer_state(db, payment["_id"], transfer)


def fulfill_succeeded_payment(db, payment_id):
    payment = db.payments.find_one_and_update(
        {"_id": payment_id, "status": "succeeded", "fulfillment_status": "pending"},
        {"$set": {"fulfillment_status": "processing", "updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if payment is None:
        return db.payments.find_one({"_id": payment_id})
    try:
        if payment["context_type"] == "order":
            db.orders.update_one(
                {"_id": payment["context_id"], "business_id": payment["business_id"]},
                {"$set": {
                    "status": "paid", "payment_status": "succeeded", "payment_id": payment["_id"],
                    "updated_at": datetime.utcnow(), "status_updated_at": datetime.utcnow(),
                }},
            )
            context_id = payment["context_id"]
        elif payment["context_type"] == "reservation":
            snapshot_booking = payment["context_snapshot"]["booking"]
            availability = get_available_slots(
                db,
                payment["business_id"],
                snapshot_booking["date"],
                snapshot_booking["party_size"],
            )
            matching_slots = [
                slot
                for group in availability.get("slots", {}).values()
                for slot in group
                if slot.get("time_24") == snapshot_booking["time_slot"] and slot.get("available")
            ]
            if not matching_slots:
                raise ConflictError("This time slot is no longer available")
            reservation_id, reservation = create_reservation(
                db,
                payment["context_snapshot"],
                payment={"payment_id": payment["_id"], "amount_cents": payment["amount_cents"], "currency": payment["currency"]},
            )
            context_id = reservation_id
            _queue_reservation_confirmation(reservation_id, reservation)
        else:
            raise PaymentStateError("Unsupported payment context")
    except ConflictError:
        db.payments.update_one(
            {"_id": payment_id},
            {"$set": {"fulfillment_status": "action_required", "failure": {"code": "slot_unavailable", "message": "The reservation slot became unavailable after payment"}, "updated_at": datetime.utcnow()}},
        )
        return db.payments.find_one({"_id": payment_id})
    except Exception as exc:
        db.payments.update_one(
            {"_id": payment_id},
            {"$set": {"fulfillment_status": "action_required", "failure": {"code": "fulfillment_error", "message": str(exc)}, "updated_at": datetime.utcnow()}},
        )
        raise
    db.payments.update_one(
        {"_id": payment_id},
        {"$set": {"context_id": context_id, "fulfillment_status": "completed", "updated_at": datetime.utcnow()}},
    )
    return db.payments.find_one({"_id": payment_id})


def create_retry(db, payment_id, checkout_secret):
    original = get_authorized_payment(db, payment_id, checkout_secret)
    if original["status"] != "failed":
        raise PaymentStateError("Only failed payments can be retried")
    if original["context_type"] == "reservation":
        booking = original["context_snapshot"]["booking"]
        availability = get_available_slots(
            db, original["business_id"], booking["date"], booking["party_size"]
        )
        if not any(
            slot.get("time_24") == booking["time_slot"] and slot.get("available")
            for group in availability.get("slots", {}).values()
            for slot in group
        ):
            raise PaymentStateError("The reservation slot is no longer available")
        try:
            find_available_table_atomic(
                db,
                original["business_id"],
                booking["date"],
                booking["time_slot"],
                booking["party_size"],
                booking["seating_preference"],
            )
        except ConflictError as exc:
            raise PaymentStateError(str(exc)) from exc
    payment, secret = create_payment_attempt(
        db,
        business_id=original["business_id"],
        context_type=original["context_type"],
        context_id=original.get("context_id"),
        amount_cents=original["amount_cents"],
        customer=original["customer"],
        snapshot=original.get("context_snapshot", {}),
    )
    if original["context_type"] == "order" and original.get("context_id"):
        db.orders.update_one(
            {"_id": original["context_id"], "business_id": original["business_id"]},
            {"$set": {"status": "payment_pending", "payment_status": "created", "payment_id": payment["_id"], "updated_at": datetime.utcnow()}},
        )
    return payment, secret


def get_authorized_payment(db, payment_id, checkout_secret):
    try:
        object_id = ObjectId(payment_id)
    except Exception as exc:
        raise PaymentAccessError("Payment not found") from exc
    payment = db.payments.find_one({"_id": object_id})
    if payment is None or not checkout_secret or not hmac.compare_digest(
        payment.get("checkout_secret_hash", ""), _hash_secret(checkout_secret)
    ):
        raise PaymentAccessError("Payment not found")
    return payment


def serialize_payment(db, payment):
    result = {
        "id": str(payment["_id"]),
        "context_type": payment["context_type"],
        "context_id": str(payment["context_id"]) if payment.get("context_id") else None,
        "amount_cents": payment["amount_cents"],
        "currency": payment["currency"],
        "status": payment["status"],
        "fulfillment_status": payment.get("fulfillment_status", "pending"),
        "failure": payment.get("failure") or None,
    }
    if payment.get("context_id") and payment["context_type"] == "order":
        order = db.orders.find_one({"_id": payment["context_id"], "business_id": payment["business_id"]})
        result["order"] = serialize_order(order) if order else None
    elif payment.get("context_id") and payment["context_type"] == "reservation":
        reservation = db.reservations.find_one({"_id": payment["context_id"], "business_id": payment["business_id"]})
        if reservation:
            result["reservation"] = {
                "confirmation_code": reservation["confirmation_code"],
                "date": reservation["booking"]["date"],
                "time_slot": reservation["booking"]["time_slot"],
                "time_display": reservation["booking"]["time_display"],
                "party_size": reservation["booking"]["party_size"],
                "seating_preference": reservation["booking"]["seating_preference"],
                "guest_name": reservation["guest"]["full_name"],
                "guest_email": reservation["guest"]["email"],
            }
    return result


def _mark_context_failed(db, payment_id):
    payment = db.payments.find_one({"_id": payment_id})
    if payment and payment["context_type"] == "order" and payment.get("context_id"):
        db.orders.update_one(
            {"_id": payment["context_id"], "business_id": payment["business_id"]},
            {"$set": {"status": "payment_failed", "payment_status": "failed", "updated_at": datetime.utcnow()}},
        )


def _set_finix(db, payment_id, field, value):
    db.payments.update_one({"_id": payment_id}, {"$set": {f"finix.{field}": value, "updated_at": datetime.utcnow()}})


def _queue_reservation_confirmation(reservation_id, reservation):
    booking = reservation["booking"]
    guest = reservation["guest"]
    try:
        send_confirmation_email.delay(
            str(reservation_id), reservation["confirmation_code"], guest["email"],
            guest["full_name"], booking["date"], booking["time_display"],
        )
    except Exception:
        # Notification delivery must never roll back a successful payment/booking.
        pass


def _hash_secret(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
