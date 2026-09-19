# BarberHub — Build Plan

Spec: [`docs/SPEC.md`](docs/SPEC.md). Original brief: [`docs/spec-original.ru.txt`](docs/spec-original.ru.txt).

Backend-only, FastAPI + async SQLAlchemy 2.0 + PostgreSQL 16, Celery + Redis for
jobs. Twelve phases; each one ends in a state where the app boots and the test
suite is green, so we can stop at any phase boundary.

---

## Target layout

```
barberhub/
├── docker-compose.yml          # postgres 16, redis 7, mailhog
├── pyproject.toml              # uv-managed
├── alembic.ini
├── .env.example
├── app/
│   ├── main.py                 # app factory, router mount, exception handlers
│   ├── core/
│   │   ├── config.py           # pydantic-settings
│   │   ├── security.py         # argon2 hashing, JWT encode/decode
│   │   ├── errors.py           # AppError hierarchy → error envelope
│   │   └── pagination.py       # Page[T] envelope + params dep
│   ├── db/
│   │   ├── base.py             # DeclarativeBase, UUID/timestamp mixins
│   │   ├── session.py          # async engine + get_db dep
│   │   └── sync_session.py     # sync engine for Celery
│   ├── models/                 # user, barbershop, barber, service, working_hours,
│   │                           # barber_break, appointment, review, refresh_token
│   ├── schemas/                # pydantic request/response per resource
│   ├── api/
│   │   ├── deps.py             # current_user, require_role, ownership guards
│   │   └── v1/                 # auth, barbershops, barbers, services, schedule,
│   │                           # availability, appointments, reviews, dashboard
│   ├── services/               # business logic — the layer that gets unit-tested
│   │   ├── availability.py     # pure interval math
│   │   ├── booking.py          # create/cancel/reschedule/complete
│   │   ├── ratings.py
│   │   └── analytics.py
│   └── workers/                # celery app, tasks, beat schedule, email templates
├── migrations/versions/
├── tests/
│   ├── conftest.py             # db fixture, client fixture, auth factories
│   ├── unit/
│   └── integration/
└── scripts/seed.py
```

---

## Phase 0 — Scaffolding  ✅ **done**
**Goal:** `uv run uvicorn app.main:app` serves `GET /health`; `docker compose up` gives Postgres, Redis, MailHog.

- `uv init`, pin Python 3.13, add deps (fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings, argon2-cffi, pyjwt, celery[redis], aiosmtplib, pytest, pytest-asyncio, httpx, ruff, mypy).
- `docker-compose.yml` with postgres 16 (+ `btree_gist`, `citext`), redis 7, mailhog.
- `app/core/config.py`: DB URL, JWT secrets/TTLs, `SLOT_STEP_MINUTES`, `MIN_BOOKING_LEAD_MINUTES`, `APPOINTMENT_CHANGE_CUTOFF_MINUTES`, SMTP, Redis URL.
- App factory, CORS, `/health`, ruff + mypy config, `.env.example`, `.gitignore`.

**Done when:** health endpoint returns 200; `ruff check` and `mypy app` clean.

## Phase 1 — Data model & migrations  ✅ **done**
**Goal:** the whole schema from spec §3 exists in Postgres.

- Declarative base with UUID pk + `created_at`/`updated_at` mixins.
- All nine models with relationships and every CHECK/UNIQUE from the spec.
- Alembic async env; initial migration creates `btree_gist` + `citext` first.
- Hand-written migration op for the `no_double_booking` exclusion constraint.

**Done when:** `alembic upgrade head` then `downgrade base` round-trips cleanly; a manual `INSERT` of two overlapping appointments raises `23P01`.

## Phase 2 — Auth & RBAC  ✅ **done**
**Goal:** all five auth endpoints, plus the dependency guards every later phase builds on.

- argon2 hashing, JWT access (15 min) + opaque refresh (30 d, hashed at rest).
- Register / login / refresh (with rotation) / logout (revoke) / me.
- `app/api/deps.py`: `get_current_user`, `require_roles(...)`, `require_shop_owner(shop_id)`, `require_barber_self_or_manager(barber_id)`.
- Error envelope + exception handlers wired in.

**Done when:** integration tests cover expired token, revoked refresh, reuse of a rotated refresh, inactive user, and wrong-role 403s.

## Phase 3 — Barbershops  ✅ **done**
CRUD with owner enforcement, soft delete, `GET` list without filters yet.
**Done when:** a non-owner gets 403 on PATCH/DELETE; deleted shops vanish from the list.

## Phase 4 — Barbers & services  ✅ **done**
- Add barber by existing user email (promotes role to `BARBER`), unique per shop.
- Service CRUD scoped to the shop; `PUT /barbers/{id}/services` assignment with the cross-shop validation.

**Done when:** assigning a service from another shop is rejected; deleting a service with future appointments is refused.

## Phase 5 — Working hours & breaks  ✅ **done**
- Weekly `PUT` replace with overlap validation inside the payload itself.
- Break create/delete, refusing breaks that collide with booked appointments.

**Done when:** split shifts persist and round-trip; a colliding break returns 409.

## Phase 6 — Availability engine ← *the core*  ✅ **done**
- `app/services/availability.py` as pure functions over `(working_intervals, busy_intervals, duration, step, now)` — no DB, no FastAPI.
- Timezone handling at the boundary; the repository layer feeds it UTC intervals.
- `GET /barbers/{id}/available-slots`.

**Done when:** the full unit matrix from spec §10 passes, including a DST-shifting timezone and a break landing exactly on a slot boundary.

## Phase 7 — Appointments  ✅ **done**
- `POST /appointments` inside one transaction; `23P01` mapped to `409 SLOT_TAKEN`.
- Cancel / reschedule (cutoff-aware) / complete / no-show with the §4 state machine.
- `GET /appointments/me`, `/appointments/{id}`, `/barbershops/{id}/appointments`.

**Done when:** the concurrency test fires 10 parallel identical bookings and gets exactly one 201; every illegal transition returns 409.

## Phase 8 — Reviews & ratings  ✅ **done**
- One review per completed appointment, by its customer.
- Rating + `reviews_count` recomputed in the same transaction as the insert.

**Done when:** a second review on the same appointment is 409; the barber's rating matches a recomputation from scratch.

## Phase 9 — Search, filters, pagination  ✅ **done**
- City / text / service / `min_rating` / geo-radius filters, sorting, `Page[T]` envelope applied to every list endpoint.
- Indexes to back the hot filters.

**Done when:** filter combinations are covered by tests and `EXPLAIN` shows index use on the city + rating path.

## Phase 10 — Dashboard analytics  ✅ **done**
- Aggregate queries for the §8 payload — one round trip per section, no N+1.
- `GET /dashboard/barber/me` for the barber's own stats.

**Done when:** numbers match a seeded fixture computed independently in the test.

## Phase 11 — Background jobs & email  ✅ **done**
- Celery app + sync session, the seven tasks from spec §9, beat schedule.
- `appointment_reminders` idempotency table + migration.
- Jinja email templates; MailHog verification in dev.

**Done when:** tasks run eagerly under test and assert on rendered payloads; a double beat tick sends exactly one reminder.

## Phase 12 — Hardening & polish  ✅ **done**
- Seed script with a realistic shop, 3 barbers, 5 services, a month of appointments.
- Structured logging with a request id; rate limit on auth endpoints.
- OpenAPI descriptions and examples; README with quickstart + architecture notes.
- GitHub Actions: ruff, mypy, pytest with a Postgres service, coverage gate.

**Done when:** a fresh clone reaches a seeded, documented, green-CI app with `docker compose up && make dev`.

---

## Sequencing notes

- Phases 3→5 are mostly mechanical CRUD; they can be compressed into one working session.
- Phases 6 and 7 are where the real design risk sits — budget the most time there and write the tests first.
- Phase 11 can be deferred without blocking anything else; nothing in phases 0–10 depends on Celery.
