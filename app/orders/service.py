"""Authoritative order pricing and persistence."""

import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.payments.exceptions import CheckoutValidationError


def money_to_cents(value):
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CheckoutValidationError("A menu price is invalid") from exc


def create_order_checkout(db, payload, tax_basis_points, delivery_fee_cents):
    business_id = payload.get("business_id")
    service_type = payload.get("service_type")
    customer = payload.get("customer")
    requested_items = payload.get("items")
    if not isinstance(business_id, str) or not business_id.strip():
        raise CheckoutValidationError("business_id is required")
    if service_type not in {"pickup", "delivery"}:
        raise CheckoutValidationError("service_type must be pickup or delivery")
    if not isinstance(customer, dict):
        raise CheckoutValidationError("customer is required")
    for field in ("name", "email", "phone"):
        if not isinstance(customer.get(field), str) or not customer[field].strip():
            raise CheckoutValidationError(f"customer.{field} is required")
    if service_type == "delivery" and not str(customer.get("address", "")).strip():
        raise CheckoutValidationError("customer.address is required for delivery")
    if not isinstance(requested_items, list) or not requested_items:
        raise CheckoutValidationError("items must contain at least one item")

    items = []
    subtotal_cents = 0
    for requested in requested_items:
        if not isinstance(requested, dict):
            raise CheckoutValidationError("each order item must be an object")
        quantity = requested.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 50:
            raise CheckoutValidationError("item quantity must be between 1 and 50")
        item_id = _object_id(requested.get("item_id"), "menu item")
        item = db.menu_items.find_one(
            {"_id": item_id, "restaurantId": business_id, "deletedAt": None, "isAvailable": True}
        )
        if item is None:
            raise CheckoutValidationError("A selected menu item is unavailable")

        allowed_addons = {str(value) for value in item.get("addonIds", [])}
        addon_ids = requested.get("addon_ids", [])
        if not isinstance(addon_ids, list) or len(addon_ids) != len(set(addon_ids)):
            raise CheckoutValidationError("addon_ids must be a unique array")
        addons = []
        addons_cents = 0
        for raw_addon_id in addon_ids:
            if str(raw_addon_id) not in allowed_addons:
                raise CheckoutValidationError("An add-on is not available for the selected item")
            addon_id = _object_id(raw_addon_id, "add-on")
            addon = db.addons.find_one(
                {"_id": addon_id, "restaurantId": business_id, "isAvailable": {"$ne": False}}
            )
            if addon is None:
                raise CheckoutValidationError("A selected add-on is unavailable")
            addon_price_cents = money_to_cents(addon.get("price"))
            addons_cents += addon_price_cents
            addons.append({
                "addon_id": addon_id,
                "name": addon.get("name", ""),
                "price_cents": addon_price_cents,
            })

        base_price_cents = money_to_cents(item.get("price"))
        unit_price_cents = base_price_cents + addons_cents
        line_total_cents = unit_price_cents * quantity
        subtotal_cents += line_total_cents
        note = requested.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise CheckoutValidationError("item note must be at most 1000 characters")
        items.append({
            "item_id": item_id,
            "name": item.get("name", ""),
            "quantity": quantity,
            "base_price_cents": base_price_cents,
            "unit_price_cents": unit_price_cents,
            "line_total_cents": line_total_cents,
            "addons": addons,
            "note": note.strip(),
        })

    tax_cents = (subtotal_cents * tax_basis_points + 5000) // 10000
    delivery_cents = delivery_fee_cents if service_type == "delivery" else 0
    total_cents = subtotal_cents + tax_cents + delivery_cents
    now = datetime.utcnow()
    document = {
        "business_id": business_id,
        "order_number": None,
        "customer": {
            "name": customer["name"].strip(),
            "email": customer["email"].strip(),
            "phone": customer["phone"].strip(),
            "address": str(customer.get("address", "")).strip(),
        },
        "instructions": str(payload.get("instructions", "")).strip()[:2000],
        "items": items,
        "service_type": service_type,
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "delivery_fee_cents": delivery_cents,
        "total_cents": total_cents,
        "currency": "USD",
        "payment_method": "card",
        "payment_status": "created",
        "status": "payment_pending",
        "created_at": now,
        "updated_at": now,
        "status_updated_at": now,
    }
    for _ in range(5):
        document["order_number"] = f"{secrets.randbelow(1000000):06d}"
        try:
            result = db.orders.insert_one(document)
            document["_id"] = result.inserted_id
            return document
        except DuplicateKeyError:
            continue
    raise RuntimeError("Unable to generate a unique order number")


def serialize_order(order):
    return {
        "id": str(order["_id"]),
        "business_id": order["business_id"],
        "order_number": order["order_number"],
        "status": order["status"],
        "payment_status": order.get("payment_status", "created"),
        "payment_id": str(order["payment_id"]) if order.get("payment_id") else None,
        "service_type": order["service_type"],
        "payment_method": order.get("payment_method", "card"),
        "customer": order["customer"],
        "instructions": order.get("instructions", ""),
        "items": [
            {
                "item_id": str(item["item_id"]),
                "name": item["name"],
                "quantity": item["quantity"],
                "unit_price_cents": item["unit_price_cents"],
                "line_total_cents": item["line_total_cents"],
                "addons": [
                    {"id": str(addon["addon_id"]), "name": addon["name"], "price_cents": addon["price_cents"]}
                    for addon in item.get("addons", [])
                ],
                "note": item.get("note", ""),
            }
            for item in order.get("items", [])
        ],
        "subtotal_cents": order["subtotal_cents"],
        "tax_cents": order["tax_cents"],
        "delivery_fee_cents": order["delivery_fee_cents"],
        "total_cents": order["total_cents"],
        "currency": order.get("currency", "USD"),
        "created_at": _iso(order.get("created_at")),
        "updated_at": _iso(order.get("updated_at")),
        "status_updated_at": _iso(order.get("status_updated_at")),
    }


def _object_id(value, label):
    try:
        return ObjectId(str(value))
    except Exception as exc:
        raise CheckoutValidationError(f"{label} id is invalid") from exc


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value
