"""MongoDB index definitions for Astryd's booking persistence collections."""

from pymongo import ASCENDING, DESCENDING

INDEXES = {
    "website_sync_links": (([("organizationId", ASCENDING)], {"unique": True}),),
    "website_sync_outbox": (([("entityType", ASCENDING), ("entityId", ASCENDING)], {"unique": True}),),
    "reservations": (
        ([ ("business_id", ASCENDING), ("date", ASCENDING), ("status", ASCENDING) ], {}),
        ([ ("confirmation_code", ASCENDING) ], {"unique": True}),
        ([
            ("business_id", ASCENDING),
            ("booking.date", ASCENDING),
            ("booking.time_slot", ASCENDING),
            ("booking.table_id", ASCENDING),
        ], {"unique": True, "partialFilterExpression": {"status": "confirmed"}}),
        ([
            ("business_id", ASCENDING),
            ("booking.date", ASCENDING),
            ("booking.time_slot", ASCENDING),
            ("booking.seating_preference", ASCENDING),
        ], {}),
    ),
    "tables": (
        ([ ("business_id", ASCENDING), ("seating_type", ASCENDING), ("is_active", ASCENDING) ], {}),
    ),
    "orders": (
        ([ ("business_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING) ], {}),
        ([ ("order_number", ASCENDING) ], {"unique": True}),
    ),
    "payments": (
        ([ ("business_id", ASCENDING), ("created_at", DESCENDING) ], {}),
        ([ ("finix.transfer_id", ASCENDING) ], {"sparse": True}),
        ([ ("idempotency_id", ASCENDING) ], {"unique": True}),
        ([ ("context_type", ASCENDING), ("context_id", ASCENDING) ], {}),
    ),
    "payment_webhook_events": (
        ([ ("provider", ASCENDING), ("event_id", ASCENDING) ], {"unique": True}),
    ),
    "restaurants": (([("slug", ASCENDING)], {"unique": True}),),
    "restaurant_users": (
        ([("restaurantId", ASCENDING), ("email", ASCENDING)], {"unique": True}),
        ([("organizationId", ASCENDING), ("email", ASCENDING)], {"unique": True, "partialFilterExpression": {"organizationId": {"$type": "string"}}}),
    ),
    "menu_categories": (([("restaurantId", ASCENDING), ("deletedAt", ASCENDING), ("displayOrder", ASCENDING)], {}),),
    "menu_items": (
        ([("business_id", ASCENDING), ("section_id", ASCENDING), ("is_available", ASCENDING)], {}),
        ([("restaurantId", ASCENDING), ("categoryId", ASCENDING), ("deletedAt", ASCENDING), ("displayOrder", ASCENDING)], {}),
        ([("restaurantId", ASCENDING), ("isAvailable", ASCENDING), ("isFeatured", ASCENDING)], {}),
    ),
    "addons": (([("restaurantId", ASCENDING), ("isAvailable", ASCENDING)], {}),),
    "offers": (([("restaurantId", ASCENDING), ("isActive", ASCENDING), ("endDate", ASCENDING)], {}),),
    "homepage_sections": (([("restaurantId", ASCENDING), ("type", ASCENDING)], {"unique": True}),),
    "brand_settings": (([("restaurantId", ASCENDING)], {"unique": True}),),
    "website_settings": (([("restaurantId", ASCENDING)], {"unique": True}),),
    "media_assets": (([("restaurantId", ASCENDING), ("folder", ASCENDING), ("createdAt", ASCENDING)], {}),),
    "audit_logs": (([("restaurantId", ASCENDING), ("createdAt", DESCENDING)], {}),),
    "organizations": (([("code", ASCENDING)], {"unique": True}),),
    "page_configs": (([("restaurantId", ASCENDING), ("module", ASCENDING)], {"unique": True}),),
    "page_content": (([("restaurantId", ASCENDING)], {"unique": True}),),
    "membership_plans": (([("restaurantId", ASCENDING), ("isActive", ASCENDING), ("order", ASCENDING)], {}),),
    "members": (([("restaurantId", ASCENDING), ("customerEmail", ASCENDING), ("planId", ASCENDING)], {"unique": True, "partialFilterExpression": {"deletedAt": None}}),),
    "member_check_ins": (([("restaurantId", ASCENDING), ("memberId", ASCENDING), ("checkedInAt", DESCENDING)], {}),),
    "account_tokens": (
        ([("tokenHash", ASCENDING)], {"unique": True}),
        ([("expiresAt", ASCENDING)], {"expireAfterSeconds": 0}),
    ),
    "api_rate_limits": (([("expiresAt", ASCENDING)], {"expireAfterSeconds": 0}),),
}


def initialize_indexes(database):
    """Create declared indexes. MongoDB makes this operation idempotent."""
    for collection_name, definitions in INDEXES.items():
        collection = database[collection_name]
        for keys, options in definitions:
            collection.create_index(keys, **options)
