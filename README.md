# Astryd API

Flask backend for a multi-tenant restaurant, gym, retail, salon and coffee platform. Features
include organization-scoped login, business onboarding, website drafts and
publishing, catalogs, reservations, orders, memberships and Finix card payments.

## Prerequisites

- Python 3.13, the version used for local verification.
- MongoDB Atlas: allow your machine's IP and configure a database user. A replica
  set is required for atomic onboarding and publishing transactions.
- Redis and Celery for asynchronous notifications; SMTP for email delivery.
- Node.js/npm in the separate frontend repository to run the full application.

## Install dependencies

From the backend repository:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
```

The two requirements files serve different purposes:

- `requirements.txt`: runtime dependencies, including Flask, PyMongo, Celery,
  Redis and Gunicorn. Use it for runtime-only installations.
- `requirements-dev.txt`: includes `requirements.txt` and adds `mongomock` for
  isolated tests. Developers install this file only; no separate runtime install
  is necessary.

Runtime-only installation:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## Environment configuration

For a fresh checkout, copy `.env.example` into `.env.staging` and
`.env.production`, then configure each independently. **Do not overwrite existing
configured files.** Example values are placeholders, not working credentials.
Generate separate random Flask and JWT secrets of at least 32 characters.

`wsgi.py` loads the selected profile, then `.env` as a fallback. Existing shell
environment variables take precedence. Set `ASTRYD_ENV` in the startup command,
not inside an env file. Staging is the default.

| Setting | Purpose |
| --- | --- |
| `MONGO_URI` | Server-only Atlas connection string. |
| `MONGO_DATABASE` | Optional database-name override. Keep staging and production separate; local profiles use `astryd_staging` and `astryd`. |
| `SECRET_KEY`, `JWT_SECRET_KEY` | Separate signing secrets. |
| `CORS_ORIGINS` | Comma-separated frontend origins; locally `http://localhost:5173`. |
| `PUBLIC_API_URL` | Reachable backend URL used for uploaded media. |
| `PLATFORM_ADMIN_URL` | Frontend URL used in account links. |
| `PLATFORM_DOMAIN` | Optional platform domain; `/s/:slug` works without custom-domain infrastructure. |
| `REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH` | Defaults to `true`. Set to `false` when deliberately deferring verification, as in the current local profiles. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis connections for background tasks. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Email delivery settings. |

Verification is currently deferred in the local profiles. New accounts remain
marked unverified but can edit and publish without SMTP. Password-reset emails,
staff invitations and private email-link member lookup still need delivery
configuration. Decide the verification policy explicitly before deployment.

### Finix and payment safety

Keep each environment's existing credentials, merchant and webhook settings intact.
Never copy sandbox credentials into production or put server secrets in `VITE_*`
frontend variables.

- Staging: `FINIX_API_URL=https://finix.sandbox-payments-api.com`.
- Production: `FINIX_API_URL=https://finix.live-payments-api.com`. Startup requires
  `FINIX_API_USERNAME`, `FINIX_API_PASSWORD`, `FINIX_MERCHANT_ID`,
  `FINIX_WEBHOOK_SIGNING_KEY` and `FINIX_WEBHOOK_BEARER_TOKEN`.
- Webhook route: `/api/v1/payments/webhooks/finix`. Local delivery requires a
  reachable tunnel such as ngrok and the matching registered Finix webhook URL.
  Starting Flask alone does not expose it publicly.

**Production uses real money even on localhost.** Submit a payment only when a
live charge is intentional.

Prices are server-side integer cents. `PAYMENT_AMOUNT_OVERRIDE_CENTS`, if set,
overrides order, reservation-deposit and membership checkout charges: `50` is
$0.50; `100` is $1.00. Leave it empty for calculated prices. Preserve existing
profile values unless deliberately changing pricing. Normal pricing also uses
`RESERVATION_DEPOSIT_PER_GUEST_CENTS`, `ORDER_TAX_BASIS_POINTS` and
`ORDER_DELIVERY_FEE_CENTS`.

## Run locally

From the backend repository, staging:

```bash
ASTRYD_ENV=staging .venv/bin/flask --app wsgi:app run --host 127.0.0.1 --port 5000
```

Production configuration for local testing:

```bash
ASTRYD_ENV=production .venv/bin/flask --app wsgi:app run --host 127.0.0.1 --port 5000
```

Stop the previous process with Ctrl+C before switching profiles. Restart after
changing environment values. Check health:

```bash
curl http://127.0.0.1:5000/health
```

The Flask development server is not a public deployment server. Deployment needs
Gunicorn or an equivalent production server, HTTPS/reverse proxy, private secrets,
persistent media storage, network controls and backups. AWS deployment is not
configured by these local commands.

### Frontend on localhost:5173

In a separate terminal, change to `Astryd-Lumiere-Reservations` and install
dependencies with `npm install` on first setup.

Staging:

```bash
npm run dev:staging -- --host localhost --port 5173 --strictPort
```

Production configuration:

```bash
npm run build:production
npm run preview -- --host localhost --port 5173 --strictPort
```

The production frontend uses relative API URLs. Its Vite **preview** proxy forwards
`/api` and `/uploads` to `http://127.0.0.1:5000`; the development server does not
have that proxy. Use build plus preview, not `npx vite --mode production`.
Keep the matching backend profile running and rebuild after frontend changes.
Frontend preview is local testing, not production hosting.

### Background notifications

With Redis and delivery settings configured, run a matching worker:

```bash
ASTRYD_ENV=staging .venv/bin/celery -A celery_worker.celery worker --loglevel=info
```

Use `ASTRYD_ENV=production` only when the worker should use production settings.

## Tenants, accounts and publishing

Signup creates the organization, hashed-password owner and initial business in
one transaction. New passwords require 10–128 characters including letters and
numbers. Login uses the readable organization code, email and chosen password,
not the organization's MongoDB ObjectId.

Private routes check current organization membership, role, permissions and site
access. Public storefronts resolve dynamically at `/s/:slug`. Legacy routes and
IDs are preserved; existing documents use a mix of `business_id`, `restaurantId`
and `organizationId`. Do not rename those fields blindly.

Website edits save as drafts; authenticated preview shows them. Publish waits
for pending saves, atomically stores published CMS/configuration snapshots and
sets `publishStatus=published` and `publishedAt`. Later draft edits do not replace
the published snapshot until publishing again. Publish does not configure DNS,
custom domains, TLS or hosting.

Signup accepts `restaurant`, `gym`, `retail`, `salon` and `coffee`. All use the
same four configurable modules; their labels, enabled states and layout choices
are defaults, not hard-coded feature restrictions. Module, homepage-section,
header and footer layout edits stay in draft until Publish. Existing homepage
sections without a stored layout use layout `a`.

### Platform administrator

The Platform Admin login is separate from organization login and requires a
real operator-provisioned account in **each** database. Frontend mock demo
credentials do not work against the backend. Use the existing provisioning
script with an operator-owned email; it prompts privately for a strong password:

```bash
.venv/bin/python scripts/create_platform_admin.py --environment staging --email YOUR_EMAIL --name "Platform Admin"
.venv/bin/python scripts/create_platform_admin.py --environment staging --email YOUR_EMAIL --name "Platform Admin" --apply
.venv/bin/python scripts/create_platform_admin.py --environment production --email YOUR_EMAIL --name "Platform Admin"
.venv/bin/python scripts/create_platform_admin.py --environment production --email YOUR_EMAIL --name "Platform Admin" --apply
```

Use separate passwords for staging and production, never put them in the shell
command or repository, and do not use the frontend's mock `password123`. The
current Platform Admin UI lists all sites and controls the Astryd badge; it
does not provide cross-tenant dashboard editing.

Paid bookings and public paid memberships are confirmed/activated only after
server-verified payment success. Membership checkout is one-time enrollment,
not recurring billing. Existing global Finix merchant routing is retained; this
does not provision independent merchants or payouts for each business.

## Tests and database scripts

Isolated tests do not access Atlas or charge cards:

```bash
.venv/bin/python -m unittest discover -s tests
```

Optional demo provisioning is restricted to `astryd_staging`:

```bash
.venv/bin/python scripts/seed_multitenant.py
.venv/bin/python scripts/seed_multitenant.py --apply
.venv/bin/python scripts/verify_multitenant.py
```

The seed defaults to dry-run and preserves existing edits. Never seed production
or copy staging into production. The staging verifier checks demo accounts and
tenant contracts; login checks may update rate-limit counters.

Atlas onboarding/publishing probes for all five verticals run inside explicitly
aborted transactions:

```bash
.venv/bin/python scripts/verify_parity.py --environment staging
.venv/bin/python scripts/verify_parity.py --environment production
```

These require `REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=false`, make no Finix calls,
and persist no probe tenants or payments.

### Existing-database migration and backup

The local-test production database has already been migrated. For another
existing database, inspect the dry-run before applying:

```bash
.venv/bin/python scripts/migrate_multitenant.py --environment production
```

Migration checks tenant IDs and email collisions, backfills organization access
and CMS defaults, and replaces global email uniqueness with scoped uniqueness.
It preserves passwords, site IDs and financial records. It is idempotent but not
one database-wide transaction; resolve interruptions and rerun before accepting
writes.

Before applying, pause writes and use a **new unique backup directory**:

```bash
.venv/bin/python scripts/backup_database.py --environment production --output .local-backups/pre-migration-UNIQUE
.venv/bin/python scripts/migrate_multitenant.py --environment production --apply --backup-dir .local-backups/pre-migration-UNIQUE --maintenance-confirmed
```

The backup exports snapshot BSON data and index metadata and verifies checksums
and BSON decoding. This is not a restore rehearsal. Before real deployment,
require an Atlas snapshot and tested recovery procedure. Never restore over
newer transactions without a reviewed recovery plan.

## Repository hygiene

Environment files, `.local-backups/`, new files under `uploads/`, virtual
environments and build output are ignored. Older tracked uploads remain tracked;
ignoring a directory does not remove Git history or delete local media. Never
commit credentials, database dumps or customer uploads. Media needs its own
backup and persistent-storage plan for deployment.

## Main modules

- `app/auth`, `app/platform`: accounts, organizations, sites and memberships.
- `app/businesses`, `app/menu`: CMS, media and catalogs.
- `app/availability`, `app/reservations`, `app/orders`: availability and booking/order flows.
- `app/payments`: Finix checkout and webhook reconciliation.
- `app/common`, `app/database`: access helpers and indexes.
- `app/notifications`: asynchronous delivery.
- `config.py`, `wsgi.py`, `celery_worker.py`: environment-aware entry points.

API routes are under `/api/v1`; `/health` and `/uploads/<filename>` are separate.
