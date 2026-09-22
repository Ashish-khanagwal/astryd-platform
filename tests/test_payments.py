import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from bson import ObjectId

from app.orders.service import create_order_checkout, money_to_cents
from app import create_app
from app.payments.finix_client import FinixClient
from app.payments.service import apply_transfer_state, create_payment_attempt, get_authorized_payment
from app.payments.webhook import extract_transfer, verify_finix_signature


class FakeCollection:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find_one(self, query):
        return next(
            (document for document in self.documents if all(document.get(key) == value for key, value in query.items())),
            None,
        )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class PaymentTests(unittest.TestCase):
    def test_money_conversion_uses_decimal_rounding(self):
        self.assertEqual(money_to_cents("12.345"), 1235)
        self.assertEqual(money_to_cents(0.1), 10)

    def test_order_totals_are_calculated_from_menu_records(self):
        item_id = ObjectId()
        menu_items = FakeCollection()
        menu_items.documents.append({
            "_id": item_id,
            "restaurantId": "lumiere-mayfair",
            "deletedAt": None,
            "isAvailable": True,
            "name": "Tasting Menu",
            "price": "10.00",
            "addonIds": [],
        })
        db = SimpleNamespace(menu_items=menu_items, addons=FakeCollection(), orders=FakeCollection())
        order = create_order_checkout(
            db,
            {
                "business_id": "lumiere-mayfair",
                "service_type": "delivery",
                "customer": {"name": "Guest", "email": "guest@example.com", "phone": "123", "address": "Mayfair"},
                "items": [{"item_id": str(item_id), "quantity": 2, "addon_ids": []}],
                "total_cents": 1,
            },
            tax_basis_points=850,
            delivery_fee_cents=1500,
        )
        self.assertEqual(order["subtotal_cents"], 2000)
        self.assertEqual(order["tax_cents"], 170)
        self.assertEqual(order["total_cents"], 3670)

    def test_checkout_secret_is_hashed_and_required(self):
        db = SimpleNamespace(payments=FakeCollection())
        payment, checkout_secret = create_payment_attempt(
            db,
            business_id="lumiere-mayfair",
            context_type="reservation",
            context_id=None,
            amount_cents=5000,
            customer={"name": "Guest"},
        )
        self.assertNotEqual(payment["checkout_secret_hash"], checkout_secret)
        loaded = get_authorized_payment(db, str(payment["_id"]), checkout_secret)
        self.assertEqual(loaded["business_id"], "lumiere-mayfair")

    def test_server_side_amount_override_replaces_calculated_amount(self):
        db = SimpleNamespace(payments=FakeCollection())
        payment, _checkout_secret = create_payment_attempt(
            db,
            business_id="lumiere-mayfair",
            context_type="order",
            context_id=ObjectId(),
            amount_cents=2260,
            amount_override_cents=100,
            customer={"name": "Sandbox Guest"},
        )
        self.assertEqual(payment["amount_cents"], 100)

    def test_finix_transfer_uses_server_amount_and_merchant(self):
        captured = {}

        def opener(request, timeout):
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return FakeResponse({"id": "TR123", "state": "SUCCEEDED"})

        client = FinixClient(
            {
                "FINIX_API_URL": "https://example.test",
                "FINIX_API_USERNAME": "user",
                "FINIX_API_PASSWORD": "secret",
                "FINIX_MERCHANT_ID": "MU123",
            },
            opener=opener,
        )
        transfer = client.create_transfer(7250, "PI123", "idem-1", {"business_id": "lumiere"})
        self.assertEqual(transfer["state"], "SUCCEEDED")
        self.assertEqual(captured["body"]["amount"], 7250)
        self.assertEqual(captured["body"]["merchant"], "MU123")
        self.assertEqual(captured["body"]["idempotency_id"], "idem-1")

    def test_pending_transfer_response_cannot_overwrite_terminal_payment(self):
        payment_id = ObjectId()
        payments = MagicMock()
        payments.find_one.return_value = {"_id": payment_id, "status": "succeeded"}
        db = SimpleNamespace(payments=payments)

        payment = apply_transfer_state(
            db,
            payment_id,
            {"id": "TR123", "state": "PENDING", "failure_code": None},
        )

        self.assertEqual(payment["status"], "succeeded")
        update_query = payments.update_one.call_args.args[0]
        self.assertEqual(update_query["status"], {"$nin": ["succeeded", "failed"]})

    def test_webhook_signature_and_transfer_extraction(self):
        body = b'{"id":"EV123"}'
        timestamp = "1000"
        signature = hmac.new(
            b"signing-key", timestamp.encode("utf-8") + b":" + body, hashlib.sha256
        ).hexdigest()
        header = f"t={timestamp},v1={signature}"
        self.assertTrue(verify_finix_signature(body, header, "signing-key", now=1000))
        self.assertTrue(
            verify_finix_signature(body, f"t={timestamp},v1={signature.upper()}", "signing-key", now=1000)
        )
        self.assertFalse(verify_finix_signature(body + b" ", header, "signing-key", now=1000))
        self.assertFalse(verify_finix_signature(body, header, "signing-key", now=2000))
        transfer = extract_transfer(
            {"id": "EV123", "_embedded": {"transfers": [{"id": "TR123", "state": "SUCCEEDED"}]}}
        )
        self.assertEqual(transfer["id"], "TR123")

    def test_finix_empty_webhook_probe_is_accepted(self):
        app = create_app("testing")
        app.config["FINIX_WEBHOOK_BEARER_TOKEN"] = ""
        for body in (b"", b"{}"):
            with self.subTest(body=body):
                response = app.test_client().post(
                    "/api/v1/payments/webhooks/finix", data=body
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["test"])


if __name__ == "__main__":
    unittest.main()
