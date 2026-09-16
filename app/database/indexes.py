"""MongoDB index definitions for Astryd's booking persistence collections."""

from pymongo import ASCENDING, DESCENDING

INDEXES = {
    "reservations": (
        ([ ("business_id", ASCENDING), ("date", ASCENDING), ("status", ASCENDING) ], {}),
        ([ ("confirmation_code", ASCENDING) ], {"unique": True}),
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
    "menu_items": (
        ([ ("business_id", ASCENDING), ("section_id", ASCENDING), ("is_available", ASCENDING) ], {}),
    ),
    "orders": (
        ([ ("business_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING) ], {}),
        ([ ("order_number", ASCENDING) ], {"unique": True}),
    ),
    "restaurants": (([("slug", ASCENDING)], {"unique": True}),),
    "restaurant_users": (
        ([("restaurantId", ASCENDING), ("email", ASCENDING)], {"unique": True}),
        ([("email", ASCENDING)], {"unique": True}),
    ),
    "menu_categories": (([("restaurantId", ASCENDING), ("deletedAt", ASCENDING), ("displayOrder", ASCENDING)], {}),),
    "menu_items": (
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
}


def initialize_indexes(database):
    """Create declared indexes. MongoDB makes this operation idempotent."""
    for collection_name, definitions in INDEXES.items():
        collection = database[collection_name]
        for keys, options in definitions:
            collection.create_index(keys, **options)
