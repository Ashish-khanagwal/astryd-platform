"""Astryd Flask application factory."""

import os

from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended.exceptions import JWTExtendedException
from pymongo.errors import PyMongoError

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
    )

    _register_jwt_handlers()
    register_tenant_error_handler(app)
    _register_error_handlers(app)
    _register_blueprints(app)
    app.extensions["celery"] = create_celery(app)

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
    @app.errorhandler(JWTExtendedException)
    def handle_jwt_error(error):
        return jsonify(error="authentication_error", message=str(error)), 401

    @app.errorhandler(PyMongoError)
    def handle_database_error(_error):
        app.logger.exception("Database operation failed")
        return jsonify(error="database_error", message="A database error occurred."), 503
