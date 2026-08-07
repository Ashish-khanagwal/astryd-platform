# Astryd API

Flask foundation for Astryd's multi-tenant Business Experience Platform.

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and replace every secret before deployment.
4. Start the API with `flask --app wsgi:app run`.
5. Start asynchronous workers with `celery -A celery_worker.celery worker --loglevel=info`.

The API blueprints are available beneath `/api/v1`. `/health` is intentionally
public for infrastructure checks. All business-owned API routes require a JWT
whose claims include an active `business_id`; the backend never trusts a
client-supplied tenant identifier.

## Tenant data rule

Every business-owned MongoDB document must include `business_id`. Use
`app.common.repository.TenantRepository` (or a module-specific subclass) for
all such persistence. Its methods scope every query and insert the authenticated
tenant identifier, preventing cross-business reads and writes.
