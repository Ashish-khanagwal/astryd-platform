from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.get("/session")
@jwt_required()
def session():
    """Confirm the current JWT is valid; credential issuance belongs in auth service."""
    return jsonify(status="authenticated")
