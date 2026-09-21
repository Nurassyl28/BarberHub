# BarberHub

[![CI](https://github.com/Nurassyl28/BarberHub/actions/workflows/ci.yml/badge.svg)](https://github.com/Nurassyl28/BarberHub/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Coverage](https://img.shields.io/badge/coverage-97%25-brightgreen)

Booking platform for barbershops — customers find shops, browse barbers and
services, see genuinely free time slots, book, cancel, reschedule and review.
Barbers manage their schedule; owners manage staff, services and revenue.

- **Spec:** [`docs/SPEC.md`](docs/SPEC.md) — schema, RBAC, algorithms, and the
  ambiguities in the original brief with how each was resolved
- **Build plan:** [`PLAN.md`](PLAN.md)

FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 16 · Alembic · Celery + Redis · JWT · pytest

## Quickstart

```bash
cp .env.example .env
make install     # uv sync
make up          # postgres + redis + mailhog
make migrate     # alembic upgrade head
make seed        # a demo shop with a month of history
make dev         # http://localhost:8000/docs
```

Seeded logins, all with password `Sup3rSecret!`:

| Role | Email |
| --- | --- |
| Shop owner | `owner@barberhub.example.com` |
| Barber | `aidar@barberhub.example.com` |
| Customer | `dana@example.com` |

| Service | URL |
| --- | --- |
| API docs | http://localhost:8000/docs |
| Postgres | `localhost:5434` (`barberhub` / `barberhub`) |
| Redis | `localhost:6380` |
| MailHog UI | http://localhost:8025 |

Background work needs two more processes: `make worker` and `make beat`.

## Commands

```bash
make check       # ruff + mypy + pytest, the same as CI
make test
make seed
make revision m="add appointments"
```

## Layout

| Path | Holds |
| --- | --- |
| `app/core/` | settings, security, error envelope, pagination, logging, rate limiting |
| `app/db/` | declarative base, async session, sync session for Celery |
| `app/models/` | SQLAlchemy tables |
| `app/schemas/` | Pydantic request/response models |
| `app/api/v1/` | routers, one module per resource |
| `app/services/` | business logic — availability, booking, ratings, analytics |
| `app/workers/` | Celery app, tasks, email templates |
| `scripts/seed.py` | demo data |
| `tests/unit/` | pure logic, no database |
| `tests/integration/` | endpoints against a real PostgreSQL |

## Design notes

### Double-booking is prevented by the database

```sql
EXCLUDE USING gist (barber_id WITH =, tstzrange(start_time, end_time, '[)') WITH &&)
  WHERE (status IN ('PENDING', 'CONFIRMED'))
```

Concurrent overlapping bookings make one transaction fail with SQLSTATE `23P01`,
which the app maps to `409 SLOT_TAKEN`. Unlike `SELECT ... FOR UPDATE` on a
barber row, this does not serialize bookings that do not actually overlap. The
suite fires ten simultaneous bookings at one slot and asserts exactly one wins.

### The availability engine is pure

`app/services/availability.py` imports neither the database nor FastAPI: it
takes intervals and returns intervals, so the hard part is tested exhaustively
without fixtures. `app/services/slots.py` is the thin layer that fetches rows
and renders a response.

Intervals are normalized to UTC on construction, and that is load-bearing:
Python subtracts two aware datetimes sharing a `tzinfo` by wall clock, so on a
DST day `midnight - midnight` reports 24 hours when only 23 elapsed.

### What the API offers is exactly what it accepts

A booking is validated by checking membership in the same slot list the
availability endpoint serves. Grid alignment, lead time, breaks, working hours
and the barber's service assignments are all enforced by construction rather
than re-derived.

### Two kinds of "closed"

A `barber_break` blocks one person's time — lunch, a dentist appointment, a
week off. A `shop_closure` shuts the whole shop for a range of local calendar
days. Expressing a public holiday as one break per barber would mean N rows
that can drift apart; remove one and the shop is half-open on New Year's Day.
Neither can be created over appointments customers already hold.

### Times

Stored as `TIMESTAMPTZ` in UTC. Working hours are wall-clock and are interpreted
against `barbershops.timezone`; conversion happens only at the boundary.
Timezone-naive input is rejected rather than guessed at.

## Testing

```bash
make test
```

- **Unit** — interval arithmetic, geo maths, token handling, email rendering.
  No database.
- **Integration** — every endpoint against a real PostgreSQL. Each test runs in
  a transaction that is rolled back, so tests share a database without sharing
  state, and each creates data in its own namespace so nothing depends on rows
  left behind by another run.
- **Concurrency** — real parallel connections with real commits, for the
  booking race and the rating recomputation.
