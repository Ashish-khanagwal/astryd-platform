from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, decode_token, get_jwt_identity, jwt_required
from werkzeug.security import check_password_hash

from app.common.api import serialize_doc
from app.extensions import mongo

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    email, password = payload.get("email"), payload.get("password")
    user = mongo.db.restaurant_users.find_one({"email": email}) if email else None
    if not user or not user.get("isActive", True) or not password or not check_password_hash(user["passwordHash"], password):
        return jsonify(error="unauthorized", message="Invalid email or password"), 401
    identity = {"userId": str(user["_id"]), "restaurantId": user["restaurantId"], "role": user["role"], "name": user["name"]}
    token = create_access_token(identity=identity)
    expires_at = datetime.fromtimestamp(decode_token(token)["exp"], tz=timezone.utc).isoformat()
    return jsonify(user=serialize_doc(user), token=token, expiresAt=expires_at)


@bp.post("/logout")
def logout():
    return jsonify(message="Logged out successfully")


@bp.get("/me")
@jwt_required()
def me():
    identity = get_jwt_identity()
    user = mongo.db.restaurant_users.find_one({"_id": __import__("bson").ObjectId(identity["userId"])})
    if not user:
        return jsonify(error="not_found", message="User not found"), 404
    return jsonify(serialize_doc(user))


@bp.post("/forgot-password")
def forgot_password():
    return jsonify(message="If this email exists, a reset link has been sent")
