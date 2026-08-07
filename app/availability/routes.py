from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("availability", __name__, url_prefix="/availability")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="availability", business_id=g.business_id, status="ready")
