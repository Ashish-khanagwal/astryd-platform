from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/status")
@require_tenant(roles={"owner", "admin"})
def status():
    return jsonify(module="admin", business_id=g.business_id, status="ready")
