from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("payments", __name__, url_prefix="/payments")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="payments", business_id=g.business_id, status="ready")
