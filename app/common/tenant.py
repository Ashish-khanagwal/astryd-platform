"""Tenant authentication and data-access safeguards."""

from functools import wraps

from flask import g, jsonify
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
            claims = get_jwt()
            business_id = claims.get("business_id")
            business_ids = set(claims.get("business_ids", []))
            if not business_id or (business_ids and business_id not in business_ids):
                raise TenantAccessError("The token does not identify an authorized business.")
            if allowed_roles and not allowed_roles.intersection(claims.get("roles", [])):
                raise TenantAccessError("You do not have permission for this action.")
            g.business_id = business_id
            g.current_roles = claims.get("roles", [])
            return view(*args, **kwargs)

        return wrapped

    return decorator


def register_tenant_error_handler(app):
    @app.errorhandler(TenantAccessError)
    def tenant_access_denied(error):
        return jsonify(error="tenant_access_denied", message=str(error)), 403
