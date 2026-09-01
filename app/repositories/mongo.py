"""Reusable MongoDB CRUD repositories with mandatory tenant scoping."""

import re

from bson import ObjectId

from app.common.tenant import TenantAccessError
from app.extensions import mongo
from app.validators import validate_email, validate_enum, validate_required_fields, validate_uuid


class BaseRepository:
    """Generic CRUD operations for one MongoDB collection."""

    def __init__(self, collection_name, database=None):
        self.collection = (database if database is not None else mongo.db)[collection_name]

    def create(self, document):
        prepared = self._prepare_create(dict(document))
        return self.collection.insert_one(prepared)

    def get_by_id(self, record_id):
        return self.collection.find_one({"_id": self._object_id(record_id)})

    def get_one(self, criteria):
        return self.collection.find_one(dict(criteria))

    def find_many(self, criteria=None, *, sort=None, limit=None, skip=0):
        cursor = self.collection.find(dict(criteria or {}))
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit is not None:
            cursor = cursor.limit(limit)
        return cursor

    def update_by_id(self, record_id, updates):
        return self.collection.update_one(
            {"_id": self._object_id(record_id)}, {"$set": self._prepare_update(dict(updates))}
        )

    def delete_by_id(self, record_id):
        return self.collection.delete_one({"_id": self._object_id(record_id)})

    @staticmethod
    def _object_id(record_id):
        return record_id if isinstance(record_id, ObjectId) else ObjectId(record_id)

    @staticmethod
    def _prepare_create(document):
        return document

    @staticmethod
    def _prepare_update(updates):
        return updates


class TenantRepository(BaseRepository):
    """CRUD repository that derives all data scope from a trusted business ID."""

    def __init__(self, collection_name, business_id, database=None):
        validate_uuid(business_id)
        super().__init__(collection_name, database)
        self.business_id = business_id

    def create(self, document):
        document = dict(document)
        if "business_id" in document and document["business_id"] != self.business_id:
            raise TenantAccessError("Cross-tenant data writes are not permitted.")
        return super().create({**document, "business_id": self.business_id})

    def get_by_id(self, record_id):
        return self.collection.find_one(self._scope({"_id": self._object_id(record_id)}))

    def get_one(self, criteria):
        return self.collection.find_one(self._scope(criteria))

    def find_many(self, criteria=None, **kwargs):
        return super().find_many(self._scope(criteria or {}), **kwargs)

    def update_by_id(self, record_id, updates):
        self._reject_business_id_mutation(updates)
        return self.collection.update_one(
            self._scope({"_id": self._object_id(record_id)}), {"$set": self._prepare_update(dict(updates))}
        )

    def delete_by_id(self, record_id):
        return self.collection.delete_one(self._scope({"_id": self._object_id(record_id)}))

    def _scope(self, criteria):
        criteria = dict(criteria)
        if "business_id" in criteria and criteria["business_id"] != self.business_id:
            raise TenantAccessError("Cross-tenant data access is not permitted.")
        return {**criteria, "business_id": self.business_id}

    def _reject_business_id_mutation(self, updates):
        if "business_id" in updates:
            raise TenantAccessError("business_id cannot be modified.")


class BusinessRepository(TenantRepository):
    def __init__(self, business_id, database=None):
        super().__init__("businesses", business_id, database)

    def _prepare_create(self, document):
        validate_required_fields(document, ("business_id", "name", "type"))
        validate_enum(document["type"], {"restaurant", "yoga", "gym"}, "type")
        if document.get("email") is not None:
            validate_email(document["email"])
        return document


class TableRepository(TenantRepository):
    def __init__(self, business_id, database=None):
        super().__init__("tables", business_id, database)

    def _prepare_create(self, document):
        if document.get("seating_type") is not None:
            validate_enum(
                document["seating_type"],
                {"Indoor", "Outdoor", "The Bar", "Private"},
                "seating_type",
            )
        return document


class ReservationRepository(TenantRepository):
    def __init__(self, business_id, database=None):
        super().__init__("reservations", business_id, database)

    def _prepare_create(self, document):
        document.setdefault("reminder_sent", False)
        self._validate_confirmation_code(document)
        if document.get("status") is not None:
            validate_enum(document["status"], {"confirmed", "cancelled", "no_show", "completed"}, "status")
        return document

    def _prepare_update(self, updates):
        self._validate_confirmation_code(updates)
        return updates

    @staticmethod
    def _validate_confirmation_code(document):
        confirmation_code = document.get("confirmation_code")
        if confirmation_code is not None and not re.fullmatch(r"LUM-\d{5}", confirmation_code):
            raise ValueError("confirmation_code must use the format LUM-#####.")


class MenuRepository(TenantRepository):
    """Accesses the related ``menu_sections`` and ``menu_items`` collections."""

    def __init__(self, business_id, database=None):
        super().__init__("menu_items", business_id, database)
        db = database if database is not None else mongo.db
        self.sections = TenantRepository("menu_sections", business_id, db)
        self.items = self


class OrderRepository(TenantRepository):
    def __init__(self, business_id, database=None):
        super().__init__("orders", business_id, database)

    def _prepare_create(self, document):
        if document.get("service_type") is not None:
            validate_enum(document["service_type"], {"delivery", "pickup"}, "service_type")
        if document.get("status") is not None:
            validate_enum(document["status"], {"pending", "confirmed", "preparing", "ready", "delivered", "cancelled"}, "status")
        return document


class StaffRepository(TenantRepository):
    def __init__(self, business_id, database=None):
        super().__init__("staff", business_id, database)

    def _prepare_create(self, document):
        if document.get("email") is not None:
            validate_email(document["email"])
        if document.get("role") is not None:
            validate_enum(document["role"], {"owner", "manager", "staff"}, "role")
        return document
