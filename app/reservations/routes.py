from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("reservations", __name__, url_prefix="/reservations")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="booking", business_id=g.business_id, status="ready")
