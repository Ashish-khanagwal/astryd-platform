"""Public checkout and authenticated restaurant order management."""

from datetime import datetime

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from pymongo import ReturnDocument

from app.common.api import require_restaurant
from app.extensions import mongo
from app.orders.service import create_order_checkout, serialize_order
from app.payments.exceptions import CheckoutValidationError
from app.payments.service import create_payment_attempt, serialize_payment

bp = Blueprint("orders", __name__, url_prefix="/orders")

ALLOWED_TRANSITIONS = {
    "paid": {"confirmed"},
    "confirmed": {"preparing"},
    "preparing": {"ready"},
    "ready": {"completed"},
}


@bp.post("/checkout")
def checkout():
    payload = request.get_json(silent=True) or {}
    try:
        order = create_order_checkout(
            mongo.db,
            payload,
            current_app.config["ORDER_TAX_BASIS_POINTS"],
            current_app.config["ORDER_DELIVERY_FEE_CENTS"],
        )
    except CheckoutValidationError as exc:
        return jsonify(error="validation_error", message=str(exc)), 400
    payment, checkout_secret = create_payment_attempt(
        mongo.db,
        business_id=order["business_id"],
        context_type="order",
        context_id=order["_id"],
        amount_cents=order["total_cents"],
        customer=order["customer"],
        amount_override_cents=current_app.config["PAYMENT_AMOUNT_OVERRIDE_CENTS"],
    )
    mongo.db.orders.update_one(
        {"_id": order["_id"]},
        {"$set": {"payment_id": payment["_id"], "updated_at": datetime.utcnow()}},
    )
    order["payment_id"] = payment["_id"]
    response = serialize_payment(mongo.db, payment)
    response.update({"checkout_secret": checkout_secret, "order": serialize_order(order)})
    return jsonify(response), 201


@bp.get("/restaurant/<restaurant_id>")
@jwt_required()
def list_orders(restaurant_id):
    _, failure = require_restaurant(restaurant_id)
    if failure:
        return failure
    orders = mongo.db.orders.find({"business_id": restaurant_id}).sort("created_at", -1).limit(500)
    return jsonify([serialize_order(order) for order in orders])


@bp.patch("/restaurant/<restaurant_id>/<order_id>/status")
@jwt_required()
def update_status(restaurant_id, order_id):
    _, failure = require_restaurant(restaurant_id)
    if failure:
        return failure
    try:
        object_id = ObjectId(order_id)
    except Exception:
        return jsonify(error="not_found", message="Order not found"), 404
    order = mongo.db.orders.find_one({"_id": object_id, "business_id": restaurant_id})
    if order is None:
        return jsonify(error="not_found", message="Order not found"), 404
    requested = (request.get_json(silent=True) or {}).get("status")
    if requested == "cancelled" and order.get("payment_status") == "succeeded":
        return jsonify(error="refund_required", message="Paid orders cannot be cancelled until refunds are enabled"), 409
    if requested == "cancelled" and order["status"] in {"payment_pending", "payment_failed"}:
        allowed = True
    else:
        allowed = requested in ALLOWED_TRANSITIONS.get(order["status"], set())
    if not allowed:
        return jsonify(error="invalid_state", message="Order status transition is invalid"), 409
    now = datetime.utcnow()
    updated = mongo.db.orders.find_one_and_update(
        {"_id": object_id, "business_id": restaurant_id, "status": order["status"]},
        {"$set": {"status": requested, "status_updated_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        return jsonify(error="conflict", message="Order changed; refresh and try again"), 409
    return jsonify(serialize_order(updated))
