"""Base repository with mandatory multi-tenant query scoping."""

from bson import ObjectId

from app.common.tenant import TenantAccessError
from app.extensions import mongo


class TenantRepository:
    """Repository for business-owned collections with enforced ``business_id``."""

    def __init__(self, collection_name, business_id):
        if not business_id:
            raise ValueError("business_id is required for tenant data access")
        self.collection = mongo.db[collection_name]
        self.business_id = business_id

    def find_one(self, criteria):
        return self.collection.find_one(self._scope(criteria))

    def find(self, criteria=None):
        return self.collection.find(self._scope(criteria or {}))

    def insert_one(self, document):
        if "business_id" in document and document["business_id"] != self.business_id:
            raise ValueError("business_id cannot differ from the authenticated tenant")
        return self.collection.insert_one({**document, "business_id": self.business_id})

    def update_one(self, criteria, update):
        return self.collection.update_one(self._scope(criteria), update)

    def delete_one(self, criteria):
        return self.collection.delete_one(self._scope(criteria))

    def by_id(self, record_id):
        return self.find_one({"_id": ObjectId(record_id)})

    def _scope(self, criteria):
        if "business_id" in criteria and criteria["business_id"] != self.business_id:
            raise TenantAccessError("Cross-tenant data access is not permitted.")
        return {**criteria, "business_id": self.business_id}
