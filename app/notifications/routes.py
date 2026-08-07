from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="notifications", business_id=g.business_id, status="ready")
