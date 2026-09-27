from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, decode_token, jwt_required
from werkzeug.security import check_password_hash, generate_password_hash

from app.common.api import serialize_doc, current_identity
from app.extensions import mongo
from app.platform.accounts import limited, issue_link, consume

bp = Blueprint("auth", __name__, url_prefix="/auth")


def session_response(user, organization_code=None):
    identity = {"userId": str(user["_id"]), "restaurantId": user.get("restaurantId"), "organizationId": user.get("organizationId"), "role": user["role"], "name": user["name"], 'authVersion':user.get('authVersion',0), 'siteAccess':user.get('siteAccess',[user.get('restaurantId')])}
    token = create_access_token(identity=identity)
    result = dict(user=serialize_doc(user), token=token, expiresAt=datetime.fromtimestamp(decode_token(token)["exp"], tz=timezone.utc).isoformat())
    if organization_code:
        result["orgCode"] = organization_code
    return result


@bp.post("/login")
def login():
    if limited('login'):
        return jsonify(error='rate_limited', message='Please try again later'), 429
    payload = request.get_json(silent=True) or {}
    email, password = str(payload.get("email", "")).strip().lower(), payload.get("password")
    query = {"email": email}
    if payload.get("orgId"):
        org = mongo.db.organizations.find_one({"code": str(payload["orgId"]).strip().upper(), "status": "active"})
        if not org:
            return jsonify(error="unauthorized", message="Invalid organization or credentials"), 401
        query["organizationId"] = str(org["_id"])
    user = mongo.db.restaurant_users.find_one(query) if email else None
    if user and user.get("organizationId") and not payload.get("orgId"):
        return jsonify(error="unauthorized", message="Organization ID is required"), 401
    if not user or not user.get("isActive", True) or not isinstance(password,str) or not password or not check_password_hash(user["passwordHash"], password):
        return jsonify(error="unauthorized", message="Invalid email or password"), 401
    return jsonify(session_response(user))


@bp.post("/signup")
def signup():
    if limited('signup', 5):
        return jsonify(error='rate_limited'), 429
    from flask import current_app
    from app.platform.service import provision
    from pymongo.errors import DuplicateKeyError
    try:
        user, code = provision(mongo.db, request.get_json(silent=True) or {}, current_app.config.get("PLATFORM_DOMAIN", ""))
    except ValueError as exc:
        return jsonify(error="validation_error", message=str(exc)), 400
    except DuplicateKeyError:
        return jsonify(error="conflict", message="Organization, site or account already exists"), 409
    required = current_app.config.get('REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH',True)
    if required: issue_link(user, 'verify')
    return jsonify(**session_response(user, code),emailDeliveryConfigured=bool(current_app.config.get('SMTP_HOST')),emailVerificationRequiredForPublish=required), 201


@bp.post('/resend-verification')
@jwt_required()
def resend_verification():
    from flask import current_app
    if limited('resend-verification',5):return jsonify(error='rate_limited'),429
    user=current_identity()
    if not user:return jsonify(error='unauthorized'),401
    if user.get('emailVerified'):return jsonify(message='Email is already verified')
    if not current_app.config.get('SMTP_HOST'):return jsonify(error='email_unavailable',message='Account email delivery is not configured'),503
    if not issue_link(user,'verify'):return jsonify(error='email_unavailable',message='Email could not be queued; please try again later'),503
    return jsonify(message='Verification email requested')


@bp.post("/logout")
@jwt_required()
def logout():
    user = current_identity()
    if user:
        mongo.db.restaurant_users.update_one({'_id':user['_id']},{'$inc':{'authVersion':1}})
    return jsonify(message="Logged out successfully")


@bp.get("/me")
@jwt_required()
def me():
    user = current_identity()
    if not user:
        return jsonify(error="not_found", message="User not found"), 404
    return jsonify(user=serialize_doc(user))


@bp.post("/forgot-password")
def forgot_password():
    if limited('forgot-password', 5):
        return jsonify(error='rate_limited'), 429
    body = request.get_json(silent=True) or {}
    org = mongo.db.organizations.find_one({'code': str(body.get('orgId', '')).upper()})
    if org:
        user = mongo.db.restaurant_users.find_one({'organizationId': str(org['_id']), 'email': str(body.get('email', '')).strip().lower(), 'isActive': True})
        if user:
            issue_link(user, 'reset')
    return jsonify(message="If this email exists, a reset link has been sent")


@bp.post('/reset-password')
def reset_password():
    from app.platform.service import password, now
    if limited('reset-password'):
        return jsonify(error='rate_limited'), 429
    body = request.get_json(silent=True) or {}
    try:
        secret = password(body.get('password'))
        if secret != body.get('passwordConfirmation', secret):
            raise ValueError('Passwords do not match')
    except ValueError as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    token = consume(body.get('token'), ['reset', 'invite'])
    if not token:
        return jsonify(error='invalid_token', message='Link is invalid or expired'), 400
    mongo.db.restaurant_users.update_one({'_id': token['userId'], 'isActive': True}, {'$set': {'passwordHash': generate_password_hash(secret), 'emailVerified': True, 'updatedAt': now()},'$inc':{'authVersion':1}})
    return jsonify(message='Password updated')


@bp.post('/verify-email')
def verify_email():
    if limited('verify-email'):
        return jsonify(error='rate_limited'), 429
    token = consume((request.get_json(silent=True) or {}).get('token'), ['verify'])
    if not token:
        return jsonify(error='invalid_token'), 400
    mongo.db.restaurant_users.update_one({'_id': token['userId']}, {'$set': {'emailVerified': True}})
    return jsonify(message='Email verified')


@bp.post('/super-admin-login')
def super_login():
    if limited('super-login'):
        return jsonify(error='rate_limited'), 429
    body = request.get_json(silent=True) or {}
    user = mongo.db.restaurant_users.find_one({'email': str(body.get('email', '')).lower(), 'role': 'super_admin', 'isActive': True})
    if not user or not check_password_hash(user['passwordHash'], str(body.get('password', ''))):
        return jsonify(error='unauthorized'), 401
    return jsonify(session_response(user))
