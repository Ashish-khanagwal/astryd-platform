"""Card-payment and Finix webhook routes."""

import json
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.payments.exceptions import (
    PaymentAccessError,
    PaymentConfigurationError,
    PaymentProviderError,
    PaymentStateError,
)
from app.payments.finix_client import FinixClient
from app.payments.service import (
    create_retry,
    get_authorized_payment,
    reconcile_transfer,
    serialize_payment,
    submit_card_payment,
)
from app.payments.webhook import extract_transfer, verify_finix_signature

bp = Blueprint("payments", __name__, url_prefix="/payments")


@bp.post("/card")
def card():
    payload = request.get_json(silent=True) or {}
    try:
        get_authorized_payment(
            mongo.db,
            payload.get("payment_id", ""),
            request.headers.get("X-Checkout-Secret", ""),
        )
        client = FinixClient(current_app.config)
        payment = submit_card_payment(
            mongo.db,
            payload.get("payment_id", ""),
            request.headers.get("X-Checkout-Secret", ""),
            payload.get("token", ""),
            client,
            payload.get("fraud_session_id"),
        )
    except PaymentAccessError as exc:
        return jsonify(error="not_found", message=str(exc)), 404
    except PaymentStateError as exc:
        return jsonify(error="invalid_state", message=str(exc)), 409
    except PaymentConfigurationError as exc:
        return jsonify(error="payment_not_configured", message=str(exc)), 503
    except PaymentProviderError as exc:
        code = "provider_unknown" if exc.uncertain else "payment_failed"
        return jsonify(error=code, message=str(exc)), 502
    return jsonify(serialize_payment(mongo.db, payment))


@bp.get("/<payment_id>")
def get_payment(payment_id):
    try:
        payment = get_authorized_payment(
            mongo.db, payment_id, request.headers.get("X-Checkout-Secret", "")
        )
    except PaymentAccessError as exc:
        return jsonify(error="not_found", message=str(exc)), 404
    return jsonify(serialize_payment(mongo.db, payment))


@bp.post("/<payment_id>/retry")
def retry(payment_id):
    try:
        payment, checkout_secret = create_retry(
            mongo.db, payment_id, request.headers.get("X-Checkout-Secret", "")
        )
    except PaymentAccessError as exc:
        return jsonify(error="not_found", message=str(exc)), 404
    except PaymentStateError as exc:
        return jsonify(error="invalid_state", message=str(exc)), 409
    response = serialize_payment(mongo.db, payment)
    response["checkout_secret"] = checkout_secret
    return jsonify(response), 201


@bp.post("/webhooks/finix")
def finix_webhook():
    raw_body = request.get_data(cache=True)
    bearer_token = current_app.config.get("FINIX_WEBHOOK_BEARER_TOKEN", "")
    if bearer_token and request.headers.get("Authorization") != f"Bearer {bearer_token}":
        return jsonify(error="unauthorized", message="Webhook authorization failed"), 401
    # Finix sends an empty synchronous probe when a webhook is first created.
    # In practice the Sandbox API currently serializes that probe as `{}`. It
    # cannot be signature-verified before Finix returns the new signing key.
    if raw_body.strip() in (b"", b"{}"):
        return jsonify(received=True, test=True)
    if not verify_finix_signature(
        raw_body,
        request.headers.get("Finix-Signature", ""),
        current_app.config.get("FINIX_WEBHOOK_SIGNING_KEY", ""),
        current_app.config.get("FINIX_WEBHOOK_MAX_AGE_SECONDS", 300),
    ):
        return jsonify(error="invalid_signature", message="Webhook signature is invalid"), 401
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError):
        return jsonify(error="invalid_payload", message="Webhook body must be JSON"), 400
    event_id = payload.get("id")
    if not event_id:
        return jsonify(error="invalid_payload", message="Webhook event id is required"), 400
    try:
        mongo.db.payment_webhook_events.insert_one({
            "provider": "finix", "event_id": event_id, "status": "processing", "received_at": datetime.utcnow()
        })
    except DuplicateKeyError:
        existing = mongo.db.payment_webhook_events.find_one({"provider": "finix", "event_id": event_id})
        if existing and existing.get("status") == "processed":
            return jsonify(received=True, duplicate=True)
    transfer = extract_transfer(payload)
    try:
        payment = reconcile_transfer(mongo.db, transfer) if transfer else None
    except Exception:
        mongo.db.payment_webhook_events.update_one(
            {"provider": "finix", "event_id": event_id},
            {"$set": {"status": "failed", "updated_at": datetime.utcnow()}},
        )
        raise
    mongo.db.payment_webhook_events.update_one(
        {"provider": "finix", "event_id": event_id},
        {"$set": {"status": "processed", "processed_at": datetime.utcnow()}},
    )
    return jsonify(received=True, processed=payment is not None)
