from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("menu", __name__, url_prefix="/menu")


@bp.get("/status")
@require_tenant()
def status():
    return jsonify(module="catalog", business_id=g.business_id, status="ready")
