"""Astryd Flask application factory."""

import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended.exceptions import JWTExtendedException
from pymongo.errors import PyMongoError, DuplicateKeyError
from bson.errors import InvalidId
from werkzeug.exceptions import HTTPException

from app.celery_app import create_celery
from app.common.tenant import register_tenant_error_handler
from app.database.indexes import initialize_indexes
from app.extensions import jwt, mongo
from config import config_by_name


def create_app(config_name=None):
    """Create and configure the Astryd API application."""
    app = Flask(__name__)
    selected_config = config_name or os.getenv("FLASK_ENV", "development")
    try:
        app.config.from_object(config_by_name[selected_config])
    except KeyError as exc:
        raise ValueError(f"Unknown configuration environment: {selected_config}") from exc
    if selected_config == "production":
        _validate_production_secrets(app)

    mongo.init_app(app)
    if app.config["MONGO_CREATE_INDEXES"]:
        initialize_indexes(mongo.db)
    jwt.init_app(app)
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
        allow_headers=["Authorization", "Content-Type", "X-Checkout-Secret"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    _register_jwt_handlers()
    register_tenant_error_handler(app)
    _register_error_handlers(app)
    _register_blueprints(app)
    app.extensions["celery"] = create_celery(app)

    @app.before_request
    def tenant_lifecycle():
        from flask import request
        from bson import ObjectId
        args=request.view_args or {}
        if request.is_json and request.method in {'POST','PUT','PATCH'} and not isinstance(request.get_json(silent=True), dict):
            return jsonify(error='validation_error',message='JSON body must be an object'),400
        sid=next((args[k] for k in ('rid','sid','restaurant_id','business_id') if args.get(k)),None)
        if not sid and request.endpoint in {'orders.checkout','reservations.create','reservations.checkout'}:
            sid=(request.get_json(silent=True) or {}).get('business_id')
        if not sid or request.method=='OPTIONS':return None
        site=mongo.db.restaurants.find_one({'restaurantId':sid})
        if site and site.get('status')=='suspended':
            # Owners can restore/archive Sites through the organization API.
            if request.endpoint in {'platform.site_update','platform.site_archive'}:return None
            return jsonify(error='not_found',message='Site is unavailable'),404
        if site and site.get('organizationId'):
            try:org=mongo.db.organizations.find_one({'_id':ObjectId(site['organizationId'])})
            except (ValueError,TypeError):org=None
            if org and org.get('status')=='suspended':return jsonify(error='not_found',message='Site is unavailable'),404

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.get("/health")
    def health_check():
        return jsonify({"status": "ok", "service": "astryd-api"})

    return app


def _validate_production_secrets(app):
    """Refuse to start production with absent or known development secrets."""
    insecure_values = {
        "",
        "development-only-secret-change-before-production",
        "replace-with-a-long-random-secret",
        "replace-with-a-different-long-random-secret",
    }
    for setting in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = app.config.get(setting, "")
        if value in insecure_values or len(value) < 32:
            raise RuntimeError(f"{setting} must be a unique secret of at least 32 characters.")
    required_finix_settings = (
        "FINIX_API_USERNAME",
        "FINIX_API_PASSWORD",
        "FINIX_MERCHANT_ID",
        "FINIX_WEBHOOK_SIGNING_KEY",
        "FINIX_WEBHOOK_BEARER_TOKEN",
    )
    missing_finix_settings = [
        setting for setting in required_finix_settings if not app.config.get(setting)
    ]
    if missing_finix_settings:
        raise RuntimeError(
            "Production Finix configuration is incomplete: "
            + ", ".join(missing_finix_settings)
        )
    if app.config.get("FINIX_API_URL") != "https://finix.live-payments-api.com":
        raise RuntimeError("Production must use the Finix live API URL.")


def _register_blueprints(app):
    from app.admin.routes import bp as admin_bp
    from app.auth.routes import bp as auth_bp
    from app.availability.routes import bp as availability_bp
    from app.businesses.routes import bp as businesses_bp
    from app.menu.routes import bp as menu_bp
    from app.notifications.routes import bp as notifications_bp
    from app.orders.routes import bp as orders_bp
    from app.payments.routes import bp as payments_bp
    from app.reservations.routes import bp as reservations_bp
    from app.platform.routes import bp as platform_bp
    from app.platform.membership import bp as membership_bp

    for blueprint in (
        auth_bp,
        businesses_bp,
        reservations_bp,
        availability_bp,
        menu_bp,
        orders_bp,
        payments_bp,
        notifications_bp,
        admin_bp,
        platform_bp,
        membership_bp,
    ):
        app.register_blueprint(blueprint, url_prefix=f"/api/v1{blueprint.url_prefix}")


def _register_jwt_handlers():
    @jwt.unauthorized_loader
    def missing_token(reason):
        return jsonify(error="authorization_required", message=reason), 401

    @jwt.invalid_token_loader
    def invalid_token(reason):
        return jsonify(error="invalid_token", message=reason), 422

    @jwt.expired_token_loader
    def expired_token(_jwt_header, _jwt_payload):
        return jsonify(error="token_expired", message="The access token has expired."), 401


def _register_error_handlers(app):
    @app.errorhandler(DuplicateKeyError)
    def duplicate_record(_error):
        return jsonify(error='conflict', message='A record with these values already exists'),409

    @app.errorhandler(InvalidId)
    def invalid_id(_error):
        return jsonify(error='validation_error',message='Invalid record identifier'),400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name.lower().replace(' ','_'),message=error.description),error.code

    @app.errorhandler(JWTExtendedException)
    def handle_jwt_error(error):
        return jsonify(error="authentication_error", message=str(error)), 401

    @app.errorhandler(PyMongoError)
    def handle_database_error(_error):
        app.logger.exception("Database operation failed")
        return jsonify(error="database_error", message="A database error occurred."), 503
