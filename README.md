# Astryd API

Flask foundation for Astryd's multi-tenant Business Experience Platform.

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Keep shared local values in `.env`, sandbox Finix values in `.env.staging`,
   and live Finix values in `.env.production`. Both environment-specific files
   are ignored by Git.
4. Start staging with `ASTRYD_ENV=staging flask --app wsgi:app run` (staging is
   also the default). Production must explicitly set `ASTRYD_ENV=production`.
5. Start asynchronous workers with `celery -A app.celery_app worker --loglevel=info`.

The API blueprints are available beneath `/api/v1`. `/health` is intentionally public for infrastructure checks. All business-owned API routes require a JWT whose claims include an active `business_id`; the backend never trusts a client-supplied tenant identifier.

## Tenant data rule

Every business-owned MongoDB document must include `business_id`. Use `app.common.repository.TenantRepository` (or a module-specific subclass) for all such persistence. Its methods scope every query and insert the authenticated tenant identifier, preventing cross-business reads and writes.

## Environment variables

`PAYMENT_AMOUNT_OVERRIDE_CENTS` is enforced by the backend for both orders and
reservation deposits. Set it to `100` for a one-dollar staging charge. Leave it
empty to use the normally calculated order total or reservation deposit.

| Variable | Purpose |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `SECRET_KEY` | Flask session secret (64 random chars) |
| `JWT_SECRET_KEY` | JWT signing secret (64 random chars, different from above) |
| `CORS_ORIGINS` | Allowed frontend origin e.g. `http://localhost:5173` |
| `CELERY_BROKER_URL` | Redis broker e.g. `redis://localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis result store e.g. `redis://localhost:6379/1` |
| `FINIX_API_USERNAME` / `FINIX_API_PASSWORD` | Server-only Finix API credentials |
| `FINIX_MERCHANT_ID` | Finix merchant (`MU...`) receiving card transfers |
| `FINIX_WEBHOOK_SIGNING_KEY` | HMAC key returned when the Finix webhook is created |
| `FINIX_WEBHOOK_BEARER_TOKEN` | Astryd-generated bearer token configured on the webhook |
| `RESERVATION_DEPOSIT_PER_GUEST_CENTS` | Required reservation deposit per guest; defaults to `2500` |
| `PAYMENT_AMOUNT_OVERRIDE_CENTS` | Optional server-side override for every charge; `100` means `$1.00`, empty means use the normal calculated amount |

## Dependencies

- Python 3.13
- MongoDB Atlas (cluster: astryd-dev, AWS Mumbai)
- Redis (for Celery background tasks — email and SMS notifications)

## API endpoints

### Public

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/v1/availability/<business_id>` | Available time slots |
| POST | `/api/v1/reservations` | Create a reservation only when the configured deposit is zero |
| POST | `/api/v1/reservations/checkout` | Start a paid reservation checkout |
| POST | `/api/v1/orders/checkout` | Start a backend-priced food order checkout |
| POST | `/api/v1/payments/card` | Charge a Finix token for a checkout attempt |
| GET | `/api/v1/payments/<payment_id>` | Poll checkout-secret-protected payment state |
| POST | `/api/v1/payments/<payment_id>/retry` | Retry a failed attempt without recreating its order |
| POST | `/api/v1/payments/webhooks/finix` | Receive signed Finix transfer updates |
| GET | `/api/v1/reservations/<confirmation_code>` | Get reservation |
| PATCH | `/api/v1/reservations/<confirmation_code>` | Modify reservation |
| DELETE | `/api/v1/reservations/<confirmation_code>` | Cancel reservation |

### Admin (JWT required)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/admin/reservations` | List reservations for business |

## Availability query params

```
GET /api/v1/availability/lumiere-mayfair?date=2026-09-10&party_size=2
```

Returns slots grouped by `afternoon` and `evening`. Each slot includes `time` (12hr display), `time_24` (storage format), and `available` flag.

## Reservation request body

```json
{
  "business_id": "lumiere-mayfair",
  "booking": {
    "date": "2026-09-10",
    "time_slot": "18:00",
    "party_size": 2,
    "seating_preference": "Indoor"
  },
  "guest": {
    "full_name": "Ashish Khanagwal",
    "email": "ashish@example.com",
    "phone": "+91 9810215269",
    "special_requests": "Window seat please",
    "newsletter_opt_in": false
  }
}
```

Seating preference must be one of: `Indoor`, `Outdoor`, `The Bar`, `Private`.

When a deposit is configured, start with `/reservations/checkout`, tokenize the card with
Finix.js, then send the `TK...` token to `/payments/card`. The confirmed reservation and
confirmation code are created only after the transfer succeeds and availability is rechecked.
The legacy direct create route returns `HTTP 402` while deposits are enabled.

## Project structure

```
astryd/
├── app/
│   ├── __init__.py          # Application factory
│   ├── extensions.py        # mongo, jwt instances
│   ├── celery_app.py        # Celery setup
│   ├── admin/               # Admin endpoints (JWT protected)
│   ├── auth/                # Auth endpoints
│   ├── availability/        # Slot availability engine
│   ├── businesses/          # Business profile endpoints
│   ├── common/              # Tenant isolation helpers
│   ├── database/            # MongoDB index creation
│   ├── menu/                # Menu endpoints
│   ├── models/              # Schema definitions
│   ├── notifications/       # Celery email/SMS tasks
│   ├── orders/              # Order endpoints
│   ├── payments/            # Payment endpoints
│   └── reservations/        # Reservation CRUD
├── config.py                # Environment-based config
├── wsgi.py                  # Entry point
└── .env                     # Environment variables (not in git)
```

## Test data

Business `lumiere-mayfair` is seeded in Atlas with the following configuration:

- Hours: Mon–Thu 18:00–23:00, Fri–Sat 12:00–00:00, Sun 12:00–21:00
- Slot duration: 90 minutes
- Tables: 5 tables across Indoor, Outdoor, The Bar, and Private seating types
