"""Tenant authentication and data-access safeguards."""

from functools import wraps

from flask import g, jsonify, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request


class TenantAccessError(PermissionError):
    """Raised when a principal tries to access an unauthorized tenant."""


def require_tenant(roles=None):
    """Require a JWT and derive the active tenant from trusted token claims."""
    allowed_roles = set(roles or [])

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            verify_jwt_in_request()
            from app.common.api import current_identity, require_restaurant
            user = current_identity()
            business_id = request.args.get('siteId') or user.get('restaurantId')
            _, failure = require_restaurant(business_id, roles=allowed_roles or None)
            if failure:
                raise TenantAccessError('Site access denied')
            g.business_id = business_id
            g.current_roles = [user.get('role')]
            return view(*args, **kwargs)

        return wrapped

    return decorator


def register_tenant_error_handler(app):
    @app.errorhandler(TenantAccessError)
    def tenant_access_denied(error):
        return jsonify(error="tenant_access_denied", message=str(error)), 403
