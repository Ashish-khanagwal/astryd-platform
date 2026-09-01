"""Validation functions used only at persistence boundaries."""

from re import compile as compile_regex
from uuid import UUID

_EMAIL = compile_regex(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_required_fields(document, fields):
    missing = [name for name in fields if document.get(name) is None]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


def validate_uuid(value, field_name="business_id"):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a UUID string")
    try:
        UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


def validate_email(value, field_name="email"):
    if not isinstance(value, str) or not _EMAIL.fullmatch(value):
        raise ValueError(f"{field_name} must be a valid email address")


def validate_enum(value, allowed_values, field_name):
    if value not in allowed_values:
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed_values))}")


def validate_type(value, expected_type, field_name):
    if not isinstance(value, expected_type):
        raise TypeError(f"{field_name} has an invalid type")
