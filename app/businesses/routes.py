from flask import Blueprint, g, jsonify

from app.common.tenant import require_tenant

bp = Blueprint("businesses", __name__, url_prefix="/businesses")


@bp.get("/current")
@require_tenant()
def current_business():
    return jsonify(business_id=g.business_id)
