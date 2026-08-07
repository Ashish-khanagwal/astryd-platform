from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="commerce", business_id=g.business_id, status="ready")
