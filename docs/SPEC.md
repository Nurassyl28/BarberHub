# BarberHub — Technical Specification

Booking platform for barbershops. Customers find shops, browse barbers and
services, see real free time slots, book, cancel, reschedule, and review.
Barbers manage their schedule. Shop owners manage staff, services, and revenue.

Source of truth: this document. The original Russian brief is kept verbatim at
[`spec-original.ru.txt`](./spec-original.ru.txt); everything below supersedes it
where they differ (all differences are listed in §11 "Resolved gaps").

---

## 1. Stack

| Concern            | Choice                                        |
| ------------------ | --------------------------------------------- |
| Language           | Python 3.13 (3.14 available; pin 3.13 for C-ext wheel coverage) |
| Framework          | FastAPI + Pydantic v2 (`pydantic-settings`)   |
| ORM                | SQLAlchemy 2.0, **async**, `Mapped[...]` style |
| Driver             | asyncpg                                       |
| DB                 | PostgreSQL 16 (`btree_gist` extension)        |
| Migrations         | Alembic (async env)                           |
| Auth               | JWT access + rotating refresh, argon2 hashing |
| Background jobs    | Celery + Redis + celery-beat (sync session)   |
| Email              | SMTP via `aiosmtplib`, MailHog in dev         |
| Packaging          | `uv`                                          |
| Lint/format        | ruff + mypy                                   |
| Tests              | pytest, pytest-asyncio, httpx `ASGITransport` |
| Local infra        | docker compose: postgres, redis, mailhog      |

---

## 2. Roles & permissions (RBAC)

Four roles on `users.role`: `CUSTOMER`, `BARBER`, `SHOP_OWNER`, `ADMIN`.

Permission is **role + ownership**, never role alone. Every write is checked
against the acting user's relationship to the resource.

| Actor        | Can write                                                                 |
| ------------ | ------------------------------------------------------------------------- |
| `CUSTOMER`   | own appointments (create/cancel/reschedule), reviews for own completed appointments |
| `BARBER`     | own working hours, own breaks, own appointments' `COMPLETED` / `NO_SHOW` transitions |
| `SHOP_OWNER` | shops they own + all barbers/services/schedules/appointments under them    |
| `ADMIN`      | everything, plus user activate/deactivate                                  |

Ownership resolution:
- shop → `barbershops.owner_id == user.id`
- barber → `barbers.user_id == user.id`
- barber, as owner → `barbers.shop_id ∈ shops owned by user`
- appointment → `customer_id == user.id`, or its barber, or that barber's shop owner

A user may hold one role but appear in several shops? **No** — one `barbers` row
per (user, shop), and a user may be a barber at multiple shops. Role stays
`BARBER` regardless of how many shops.

---

## 3. Data model

All ids are UUID v4 (`uuid_generate_v4()` is avoided; generated app-side).
All timestamps are `TIMESTAMPTZ`, stored UTC. All money is `NUMERIC(10,2)`.

### users
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| first_name | varchar(100) not null | |
| last_name | varchar(100) not null | |
| email | citext unique not null | case-insensitive |
| phone | varchar(32) | |
| password_hash | varchar(255) not null | argon2id |
| role | enum `user_role` not null | default `CUSTOMER` |
| is_active | bool not null default true | |
| created_at | timestamptz not null default now() | |
| updated_at | timestamptz not null | |

### refresh_tokens *(added — §11.1)*
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| user_id | uuid fk users on delete cascade | |
| token_hash | varchar(255) unique not null | sha256 of the opaque token |
| expires_at | timestamptz not null | |
| revoked_at | timestamptz null | set on logout / rotation |
| created_at | timestamptz not null | |

Index: `(user_id, revoked_at)`.

### barbershops
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| owner_id | uuid fk users not null | owner must have role `SHOP_OWNER` or `ADMIN` |
| name | varchar(150) not null | |
| description | text | |
| address | varchar(255) not null | |
| city | varchar(100) not null | indexed |
| latitude | numeric(9,6) | |
| longitude | numeric(9,6) | |
| phone | varchar(32) | |
| timezone | varchar(64) not null default `Asia/Almaty` | IANA name *(added — §11.2)* |
| requires_confirmation | bool not null default false | bookings arrive `PENDING` *(§11.8)* |
| is_active | bool not null default true | soft delete |
| created_at / updated_at | timestamptz | |

### barbers
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| user_id | uuid fk users not null | |
| shop_id | uuid fk barbershops not null | |
| bio | text | |
| experience_years | int not null default 0, `>= 0` | |
| rating | numeric(3,2) not null default 0 | denormalized, recomputed on review write |
| reviews_count | int not null default 0 | *(added — §11.3)* |
| is_active | bool not null default true | |

Unique: `(user_id, shop_id)`.

### services
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| shop_id | uuid fk barbershops not null | |
| name | varchar(150) not null | |
| description | text | |
| duration_minutes | int not null, `> 0 and <= 480` | |
| price | numeric(10,2) not null, `>= 0` | |
| is_active | bool not null default true | soft delete — never hard-delete a booked service |

Unique: `(shop_id, lower(name))`.

### barber_services
Composite pk `(barber_id, service_id)`, both FK cascade. DB `CHECK` cannot span
tables, so the app enforces: `service.shop_id == barber.shop_id`.

### working_hours
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| barber_id | uuid fk barbers not null | |
| weekday | smallint not null, `0..6` | 0 = Monday (ISO) |
| start_time | time not null | shop-local wall clock |
| end_time | time not null, `> start_time` | |

Unique: `(barber_id, weekday, start_time)`. Multiple rows per weekday are allowed
(split shifts, e.g. 09:00–13:00 and 14:00–19:00).

### barber_breaks
One-off, absolute-time blocks (vacation, lunch on a specific day, appointments
outside the system).

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| barber_id | uuid fk barbers not null | |
| start_datetime | timestamptz not null | |
| end_datetime | timestamptz not null, `> start_datetime` | |
| reason | varchar(255) | |

Index: `(barber_id, start_datetime)`.

### shop_closures *(added — §11.12)*
Whole-shop shutdowns: public holidays, a refit, the owner's holiday. Distinct
from `barber_breaks`, which block one person; a holiday closes everyone at once,
and one row per barber could drift apart.

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| shop_id | uuid fk barbershops on delete cascade | indexed |
| start_date | date not null | shop-local calendar day |
| end_date | date not null, `>= start_date` | **inclusive** |
| reason | varchar(255) | |
| created_at / updated_at | timestamptz | |

Unique: `(shop_id, start_date, end_date)`. Stored as local dates, not instants:
"closed on 1 January" is a statement about the shop's calendar.

### appointments
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| customer_id | uuid fk users not null | |
| barber_id | uuid fk barbers not null | |
| service_id | uuid fk services not null | |
| start_time | timestamptz not null | |
| end_time | timestamptz not null, `> start_time` | derived from service duration |
| status | enum `appointment_status` not null default `CONFIRMED` | |
| total_price | numeric(10,2) not null | **snapshot** of service price at booking |
| cancelled_by | uuid fk users null | *(added — §11.4)* |
| cancellation_reason | varchar(255) null | |
| created_at / updated_at | timestamptz | |

**Concurrency guard (the core requirement):**
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE appointments ADD CONSTRAINT no_double_booking
  EXCLUDE USING gist (
    barber_id WITH =,
    tstzrange(start_time, end_time, '[)') WITH &&
  ) WHERE (status IN ('PENDING', 'CONFIRMED'));
```
Two concurrent transactions booking the same barber for overlapping ranges: one
commits, the other gets `23P01 exclusion_violation`, which the app maps to
`409 SLOT_TAKEN`. This is the authority; the in-app overlap check is only there
to produce a friendly error on the common (non-racing) path.

Index: `(barber_id, start_time)`, `(customer_id, start_time desc)`.

### reviews
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | *(added — §11.5)* |
| customer_id | uuid fk users not null | |
| barber_id | uuid fk barbers not null | |
| appointment_id | uuid fk appointments **unique** not null | one review per appointment |
| rating | smallint not null, `1..5` | |
| comment | text | |
| created_at | timestamptz not null | |

---

## 4. Status machine

```
                 ┌──────────────► CANCELLED   (customer / barber / owner, before cutoff)
                 │
PENDING ──────► CONFIRMED ─────► COMPLETED    (barber or owner, only after start_time)
   │             │
   │             └──────────────► NO_SHOW      (barber or owner, only after start_time)
   │
   └───────────► CANCELLED
```

- New appointments are created **`CONFIRMED`** by default. A shop with
  `requires_confirmation` set instead creates them `PENDING`, and staff accept
  them via `PATCH /appointments/{id}/confirm`. A `PENDING` booking still holds
  the slot — it is inside the exclusion constraint's filtered set — so a shop
  cannot sell the same time twice while it decides.
- `COMPLETED`, `CANCELLED`, `NO_SHOW` are terminal. Any other transition → `409`.
- Reschedule keeps the same row, moves `start_time`/`end_time`, re-runs the same
  availability + exclusion-constraint path.
- Cancel/reschedule cutoff: `APPOINTMENT_CHANGE_CUTOFF_MINUTES` (default 120).
  Inside the cutoff, only the barber, shop owner, or admin may act.

---

## 5. Availability engine

`GET /barbers/{barber_id}/available-slots?date=2026-09-20&service_id=<uuid>`

`service_id` is **required** — slot width is the service duration *(§11.6)*.

Algorithm:
1. Load barber (active) and service; assert `service.is_active`,
   `service.shop_id == barber.shop_id`, and the pair exists in `barber_services`.
2. Resolve the shop timezone; interpret `date` as a local calendar day, derive
   `[day_start_utc, day_end_utc)`.
3. If a `shop_closures` row covers that local day, return `[]` immediately —
   no working hours matter when the doors are shut.
4. Load `working_hours` for that ISO weekday → local intervals → UTC intervals.
   None → return `[]`.
5. Subtract `barber_breaks` overlapping the day.
6. Subtract `appointments` with status in (`PENDING`, `CONFIRMED`) overlapping the day.
7. Walk each remaining free interval in `SLOT_STEP_MINUTES` (default 15) steps,
   emitting a slot whenever `[t, t + duration)` fits entirely inside it.
8. Drop slots starting before `now + MIN_BOOKING_LEAD_MINUTES` (default 30).
9. Return UTC ISO-8601 instants plus the shop-local rendering.

All interval math lives in one pure, dependency-free module
(`app/services/availability.py`) so it is unit-testable without a database.

Response:
```json
{
  "date": "2026-09-20",
  "timezone": "Asia/Almaty",
  "service": { "id": "...", "name": "Haircut", "duration_minutes": 45 },
  "slots": [
    { "start": "2026-09-20T03:00:00Z", "end": "2026-09-20T03:45:00Z", "start_local": "09:00" }
  ]
}
```

### Booking transaction
```
BEGIN
  SELECT ... FROM services WHERE id = :sid AND is_active   -- price + duration
  end_time := start_time + duration
  validate: lead time, working hours, breaks, barber_services link
  INSERT INTO appointments (...)                            -- exclusion constraint arbitrates
COMMIT
```
No `SELECT ... FOR UPDATE` on a barber row is needed: the exclusion constraint is
the serialization point, and it does not serialize non-overlapping bookings.

---

## 6. API surface

Base path `/api/v1`. Auth via `Authorization: Bearer <access_token>`.

### Auth
| Method | Path | Access | Notes |
| --- | --- | --- | --- |
| POST | `/auth/register` | public | role limited to `CUSTOMER` \| `SHOP_OWNER` |
| POST | `/auth/login` | public | → access + refresh |
| POST | `/auth/refresh` | refresh token | rotates, revokes the old one |
| POST | `/auth/logout` | auth | revokes the presented refresh token |
| GET | `/auth/me` | auth | |

### Barbershops
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbershops` | public, filtered + paginated |
| GET | `/barbershops/{id}` | public |
| POST | `/barbershops` | `SHOP_OWNER`, `ADMIN` |
| PATCH | `/barbershops/{id}` | owner, `ADMIN` |
| DELETE | `/barbershops/{id}` | owner, `ADMIN` — soft delete |

### Barbers
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbershops/{id}/barbers` | public |
| GET | `/barbers/{id}` | public |
| POST | `/barbershops/{id}/barbers` | shop owner, `ADMIN` — by existing user email; promotes role to `BARBER` |
| PATCH | `/barbers/{id}` | the barber, shop owner, `ADMIN` |
| DELETE | `/barbers/{id}` | shop owner, `ADMIN` — soft delete, refuses with future appointments |
| PUT | `/barbers/{id}/services` | shop owner, `ADMIN` — set the assigned service ids |

### Services
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbershops/{id}/services` | public |
| POST | `/barbershops/{id}/services` | shop owner, `ADMIN` |
| PATCH | `/services/{id}` | shop owner, `ADMIN` |
| DELETE | `/services/{id}` | shop owner, `ADMIN` — soft delete |

### Schedule
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbers/{id}/working-hours` | public |
| PUT | `/barbers/{id}/working-hours` | the barber, shop owner, `ADMIN` — full weekly replace |
| GET | `/barbers/{id}/breaks` | the barber, shop owner, `ADMIN` |
| POST | `/barbers/{id}/breaks` | the barber, shop owner, `ADMIN` — refuses if it collides with a booked appointment |
| DELETE | `/breaks/{id}` | the barber, shop owner, `ADMIN` |

### Closures
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbershops/{id}/closures` | public |
| POST | `/barbershops/{id}/closures` | shop owner, `ADMIN` |
| DELETE | `/closures/{id}` | shop owner, `ADMIN` |

### Availability
| Method | Path | Access |
| --- | --- | --- |
| GET | `/barbers/{id}/available-slots?date=&service_id=` | public |

### Appointments
| Method | Path | Access |
| --- | --- | --- |
| POST | `/appointments` | `CUSTOMER` |
| GET | `/appointments/me` | auth — customer's own, or barber's own schedule |
| GET | `/appointments/{id}` | participant (customer / barber / shop owner / admin) |
| GET | `/barbershops/{id}/appointments` | shop owner, `ADMIN` — filter by date range, status, barber |
| PATCH | `/appointments/{id}/cancel` | customer (before cutoff), barber, owner, admin |
| PATCH | `/appointments/{id}/reschedule` | customer (before cutoff), barber, owner, admin |
| PATCH | `/appointments/{id}/confirm` | barber, owner, admin — `PENDING` → `CONFIRMED` |
| PATCH | `/appointments/{id}/complete` | barber, owner, admin |
| PATCH | `/appointments/{id}/no-show` | barber, owner, admin |

### Reviews
| Method | Path | Access |
| --- | --- | --- |
| POST | `/appointments/{id}/review` | the customer, only if `COMPLETED`, once |
| GET | `/barbers/{id}/reviews` | public, paginated |

### Dashboard
| Method | Path | Access |
| --- | --- | --- |
| GET | `/dashboard/barbershop/{shop_id}` | shop owner, `ADMIN` |
| GET | `/dashboard/barber/me` | `BARBER` |

---

## 7. Search, filtering, pagination

`GET /barbershops` query params:

| Param | Type | Behaviour |
| --- | --- | --- |
| `city` | str | case-insensitive exact |
| `q` | str | ILIKE over name + description |
| `service` | str | shops having an active service whose name matches |
| `min_rating` | float 0–5 | shops whose average barber rating ≥ value |
| `lat`,`lng`,`radius_km` | float | haversine bounding-box prefilter, then exact distance |
| `sort` | enum | `rating` \| `-rating` \| `name` \| `created_at` \| `distance` |
| `page` | int ≥ 1, default 1 | |
| `limit` | int 1–100, default 20 | |

Every list response uses one envelope:
```json
{ "items": [], "total": 0, "page": 1, "limit": 20, "pages": 0 }
```

Errors use one envelope:
```json
{ "error": { "code": "SLOT_TAKEN", "message": "...", "details": {} } }
```
Codes: `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`,
`SLOT_TAKEN`, `OUTSIDE_WORKING_HOURS`, `INVALID_TRANSITION`, `CUTOFF_PASSED`,
`ALREADY_REVIEWED`, `CONFLICT`, `RATE_LIMITED`, `INTERNAL_ERROR`.

---

## 8. Dashboard payload

`GET /dashboard/barbershop/{shop_id}?date_from=&date_to=` (defaults to the
current month, shop-local):

```json
{
  "total_appointments": 412,
  "today_appointments": 9,
  "completed_appointments": 350,
  "cancelled_appointments": 41,
  "no_show_appointments": 21,
  "monthly_revenue": "1750000.00",
  "average_ticket": "5000.00",
  "most_popular_service": { "id": "...", "name": "Haircut", "count": 190 },
  "top_barber": { "id": "...", "name": "Aidar S.", "completed": 140, "revenue": "700000.00" }
}
```
Revenue counts `COMPLETED` appointments only, summing `total_price`.

---

## 9. Background jobs (Celery + Redis)

| Task | Trigger | Does |
| --- | --- | --- |
| `send_email` | called by others | SMTP send, retry 3× with backoff |
| `notify_appointment_created` | on booking | confirmation to customer, heads-up to barber |
| `notify_appointment_cancelled` | on cancel | both parties |
| `notify_appointment_rescheduled` | on reschedule | both parties |
| `send_appointment_reminders` | beat, every 15 min | appointments starting in 24h ±window and 2h ±window, not yet reminded |
| `expire_stale_pending` | beat, hourly | `PENDING` older than 30 min → `CANCELLED` |
| `recompute_barber_ratings` | beat, nightly | drift repair for the denormalized rating |

Reminder idempotency: `appointment_reminders(appointment_id, kind)` unique table
— never "send if not sent" against a timestamp window alone.

Celery workers use a **separate sync engine** (`psycopg`), since Celery is not
async. Both engines read the same models.

---

## 10. Testing

- **Unit** — availability interval math (pure, no DB): empty day, split shifts,
  break exactly on a boundary, appointment spanning the whole day, DST-shifting
  timezone, service longer than any gap, lead-time filtering.
- **Integration** — every endpoint against a real Postgres (session-scoped
  container / test DB, transaction-rollback per test), with RBAC negative cases
  for each route.
- **Concurrency** — N parallel `POST /appointments` for the same slot from
  separate connections; assert exactly one 201 and N−1 × 409.
- **Target** ≥ 85% line coverage on `app/services` and `app/api`.

---

## 11. Resolved gaps

Ambiguities and omissions in the original brief, and how we resolve them:

1. **Logout with stateless JWT is a no-op.** Added a `refresh_tokens` table with
   hashing, rotation on refresh, and revocation on logout.
2. **No timezone anywhere**, but `working_hours` are wall-clock and appointments
   are instants. Added `barbershops.timezone` (IANA); all conversion happens at
   the availability/booking boundary.
3. **`barbers.rating` is denormalized** with no defined source. Defined as the
   mean of that barber's review ratings, recomputed transactionally on review
   insert, with `reviews_count` and a nightly repair job.
4. **Cancellation has no audit trail.** Added `cancelled_by`, `cancellation_reason`.
5. **`reviews` has no primary key** and no uniqueness. Added `id`, plus a unique
   constraint on `appointment_id`.
6. **`available-slots` takes only `date`**, but step 2 of the brief's own
   algorithm needs a service duration. Made `service_id` a required query param.
7. **`min_rating` filters shops, but shops have no rating.** Computed as the
   average over the shop's active barbers, via a subquery (no third denormalized
   column).
8. **`PENDING` vs `CONFIRMED` is undefined.** Bookings are `CONFIRMED` on
   creation unless the shop sets `requires_confirmation`, in which case they
   arrive `PENDING` and staff confirm them. Pending bookings hold their slot.
9. **No delete semantics.** Shops, barbers, and services soft-delete
   (`is_active=false`); deleting anything with future appointments is refused.
10. **No cancellation policy.** Configurable cutoff, default 120 minutes, with
    staff able to override.
11. **Slot granularity unspecified.** `SLOT_STEP_MINUTES`, default 15.
12. **Day-off / holiday closures.** Modelled as `shop_closures`, a range of
    local calendar days closing the whole shop. A closed day short-circuits the
    availability engine entirely, and a closure cannot be created over
    appointments customers already hold.
13. **Payments** are out of scope. `total_price` is a record, not a charge.
