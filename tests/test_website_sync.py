"""Website sync outbox and event contract tests without external services."""

import unittest
from unittest.mock import patch

import mongomock
from bson import ObjectId

from app import create_app
from app.extensions import mongo
from app.integrations.website_sync import deliver, enqueue


class WebsiteSyncTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.db = mongomock.MongoClient().astryd_sync_test
        db_patch = patch.object(mongo, "db", self.db)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.site_id = "restaurant-test"
        self.db.restaurants.insert_one({
            "restaurantId": self.site_id, "organizationId": "website-org",
            "vertical": "restaurant",
        })
        self.db.website_sync_links.insert_one({
            "organizationId": "website-org", "mainOrgId": "main-org",
            "ownerEmail": "owner@example.com", "sites": {self.site_id: {
                "locationId": "main-location", "utcOffset": "+05:30",
                "tableMappings": {"website-table": 3}, "durationMinutes": 90,
            }},
        })

    def test_order_delivery_is_versioned_and_idempotent(self):
        order_id = self.db.orders.insert_one({
            "business_id": self.site_id, "status": "confirmed",
            "payment_status": "succeeded", "total_cents": 1200,
        }).inserted_id
        with patch("app.integrations.tasks.deliver_website_record.delay") as task:
            enqueue("order", order_id)
            enqueue("order", order_id)
        self.assertEqual(task.call_count, 2)
        pending = self.db.website_sync_outbox.find_one({"entityId": str(order_id)})
        self.assertEqual(pending["version"], 2)
        with patch("app.integrations.website_sync._request", return_value={"status": "imported"}) as request:
            self.assertEqual(deliver("order", str(order_id), 2), "delivered")
        event = request.call_args.args[1]
        self.assertEqual((event["main_org_id"], event["location_id"], event["version"]),
                         ("main-org", "main-location", 2))
        self.assertEqual(event["data"]["total_cents"], 1200)
        self.assertNotIn("_id", event["data"])
        self.assertEqual(self.db.website_sync_outbox.find_one({"entityId": str(order_id)})["state"], "delivered")

    def test_booking_uses_configured_table_and_offset(self):
        booking_id = self.db.reservations.insert_one({
            "business_id": self.site_id, "status": "confirmed",
            "booking": {"date": "2026-10-10", "time_slot": "19:00", "table_id": "website-table"},
            "guest": {"full_name": "Guest"},
        }).inserted_id
        with patch("app.integrations.website_sync._request", return_value={"status": "conflict"}) as request:
            self.assertEqual(deliver("booking", str(booking_id), 1), "conflict")
        event = request.call_args.args[1]
        self.assertEqual(event["table_number"], 3)
        self.assertEqual(event["data"]["utc_offset"], "+05:30")
        self.assertEqual(event["data"]["duration_minutes"], 90)


if __name__ == "__main__":
    unittest.main()
