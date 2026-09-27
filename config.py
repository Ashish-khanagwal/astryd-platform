"""Runtime configuration for Astryd."""

import os
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit


def _mongo_uri():
    uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/astryd')
    database = os.getenv('MONGO_DATABASE', '').strip()
    if database:
        if not __import__('re').fullmatch(r'[a-zA-Z0-9_-]+', database):
            raise ValueError('Invalid MONGO_DATABASE')
        parts = urlsplit(uri)
        uri = urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, parts.fragment))
    return uri


def _optional_positive_int(name):
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    value = int(raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer number of cents")
    return value


class Config:
    """Base configuration loaded only from environment variables."""

    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-secret-change-before-production")
    MONGO_URI = _mongo_uri()
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_VERIFY_SUB = False
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "30"))
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "30"))
    )
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.getenv(
        "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
    )
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "300"))
    MONGO_CREATE_INDEXES = os.getenv('MONGO_CREATE_INDEXES','true').lower() == 'true'
    PLATFORM_DOMAIN = os.getenv('PLATFORM_DOMAIN', '').strip().lower()
    REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH = os.getenv('REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH', 'true').lower() == 'true'
    PLATFORM_ADMIN_URL = os.getenv('PLATFORM_ADMIN_URL', 'http://localhost:5173')
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', '')
    PUBLIC_API_URL = os.getenv('PUBLIC_API_URL', '').rstrip('/')
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    FINIX_API_URL = os.getenv(
        "FINIX_API_URL", "https://finix.sandbox-payments-api.com"
    ).rstrip("/")
    FINIX_API_USERNAME = os.getenv("FINIX_API_USERNAME", "")
    FINIX_API_PASSWORD = os.getenv("FINIX_API_PASSWORD", "")
    FINIX_MERCHANT_ID = os.getenv("FINIX_MERCHANT_ID", "")
    FINIX_WEBHOOK_SIGNING_KEY = os.getenv("FINIX_WEBHOOK_SIGNING_KEY", "")
    FINIX_WEBHOOK_BEARER_TOKEN = os.getenv("FINIX_WEBHOOK_BEARER_TOKEN", "")
    FINIX_WEBHOOK_MAX_AGE_SECONDS = int(
        os.getenv("FINIX_WEBHOOK_MAX_AGE_SECONDS", "300")
    )
    RESERVATION_DEPOSIT_PER_GUEST_CENTS = int(
        os.getenv("RESERVATION_DEPOSIT_PER_GUEST_CENTS", "2500")
    )
    PAYMENT_AMOUNT_OVERRIDE_CENTS = _optional_positive_int(
        "PAYMENT_AMOUNT_OVERRIDE_CENTS"
    )
    ORDER_TAX_BASIS_POINTS = int(os.getenv("ORDER_TAX_BASIS_POINTS", "850"))
    ORDER_DELIVERY_FEE_CENTS = int(os.getenv("ORDER_DELIVERY_FEE_CENTS", "1500"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    TESTING = True
    MONGO_CREATE_INDEXES = False
    MONGO_URI = os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017/astryd_test")


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
