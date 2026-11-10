# Key design decisions

The most important choices, each with the alternative that was considered and the reason.
Format: **decision** — *instead of* … — because ….

## Stack and tooling

| Decision | Instead of | Because |
|---|---|---|
| **FastAPI**, async throughout | Django, Flask; sync endpoints | Native async, Pydantic validation and OpenAPI docs built in; the work is I/O-bound (DB, S3) |
| **uv** + lockfile, **tox** (+ tox-uv) | pip + venv, Poetry | One fast tool for environments, Python versions and a cross-platform lockfile; tox runs the same checks locally and in CI |
| **ruff** (lint + format), **mypy --strict** | flake8 + black + isort, pyright | One tool instead of five, includes security and async rules; mypy has an official Pydantic plugin |
| `src/` layout, `uv_build` backend | flat layout, setuptools | Tests run against the installed package, so packaging mistakes surface early |

## Data

| Decision | Instead of | Because |
|---|---|---|
| **PostgreSQL** + **SQLAlchemy 2.0 async** + **asyncpg** | SQLite, MongoDB; SQLModel | Strict relational rules (partial unique index, CHECKs, transactional DDL), managed on RDS; the most mature async ORM |
| **Alembic** migrations as source of truth, plus `schema.sql` | `create_all()` only | Migrations evolve a live database and can be undone; `schema.sql` shows the schema without an ORM, and a test keeps it identical |
| Owner stored as a **membership row** + partial unique index | `projects.owner_id` | One source of truth for access; the database guarantees one owner |
| **Denormalized** `projects.total_size_bytes` | `SUM()` on every upload | The size limit is checked on every upload; kept in sync under a row lock and recalculated by the Lambda — see [database.md](database.md) |
| `BIGINT GENERATED ALWAYS AS IDENTITY` keys | UUIDs | Compact and fast; enumeration is prevented by 404 for non-members |
| Role as `VARCHAR` + `CHECK` | native PostgreSQL `ENUM` | Adding a role is a simple migration; ENUMs are awkward to alter |
| **Seed through the services**, idempotent | SQL inserts in a migration or fixture file | Demo data obeys every API rule (hashes, one owner, S3 objects, totals); data migrations would put demo data into production schema history |
| No default seed password in production | one documented password everywhere | The repository is public; production demo accounts must not have a known password |

## Security

| Decision | Instead of | Because |
|---|---|---|
| **argon2id** (pwdlib), hashing in a thread pool | bcrypt, SHA-256; passlib | Current OWASP recommendation, memory-hard; passlib is unmaintained; hashing must not block the event loop |
| Password policy: 12–128 chars, upper + lower + digit + special, must not contain the login | length only | Satisfies "basic strength" clearly; the login is public, so a password built from it is guessable |
| **JWT HS256**, 1 hour, only `sub`/`iat`/`exp`, **PyJWT** | server sessions; RS256; python-jose | Required by the brief; one service signs and verifies; python-jose is poorly maintained |
| Role **not** in the token; read from the DB per request | roles as JWT claims | An invite or role change takes effect immediately |
| **One permission matrix** (`services/permissions.py`) used through a dependency | role checks inside each endpoint | Rules cannot drift between endpoints; checks run before the endpoint body |
| **404** for non-members (projects and documents) | 403 | 403 would reveal that the id exists |
| Same 401 for unknown user and wrong password, with a dummy hash | different messages | Prevents user enumeration by message or timing |
| 422 responses **without** the submitted values | FastAPI's default handler | The default would echo passwords back |
| Uniqueness decided by **database constraints** | check-then-insert | Atomic; no race between simultaneous requests |
| `extra="forbid"` on request bodies | ignoring unknown fields | Rejects typos and mass assignment (`owner_id`, `role`) |

## Documents and S3

| Decision | Instead of | Because |
|---|---|---|
| Files in **S3**, metadata in PostgreSQL | `BYTEA` in the DB; server disk | Small DB and fast backups; files survive server replacement; S3 events drive the Lambda |
| **boto3** in `run_in_threadpool` | aioboto3 | Official SDK, fully supported by moto; never blocks the event loop |
| File type from **magic bytes** + matching extension | trusting `Content-Type` / file name | Both are client-controlled and trivially faked |
| Random keys `projects/{id}/{uuid}.ext` | the user's file name as key | No collisions or path tricks; the prefix lets the Lambda find a project's files |
| **S3 first** on upload (with compensation), **DB first** on delete | distributed transactions, outbox | A row never points to a missing file; worst case is a harmless orphan object |
| Project row locked (`SELECT … FOR UPDATE`) for the size check | unlocked check | Concurrent uploads cannot together exceed the limit |
| All-or-nothing uploads | partial success (207) | One clear outcome, nothing to clean up |
| IAM role for AWS credentials | keys in configuration | No long-lived AWS key on disk; rotation is automatic |
| **moto** for S3 in tests, Compose and CI | MinIO, LocalStack, a real dev bucket | One tool everywhere, no AWS account needed |

## The size-limit Lambda and AWS

| Decision | Instead of | Because |
|---|---|---|
| Lambda takes **the API's project lock** before counting | waiting a fixed time; no coordination | It waits exactly as long as an upload is in progress, so it never mistakes an in-flight file for a stray one |
| Delete only **unregistered** objects when over the limit, newest first | delete the newest object, whatever it is | API-registered documents were already limit-checked under the lock; deleting one would leave a row pointing to a missing file |
| Recompute the total from **S3** on every event | trust the stored total | S3 is the source of truth for stored bytes; drift heals itself; runs are idempotent |
| **Plain SQL via pg8000** in the Lambda | SQLAlchemy; psycopg (binary) | Four statements need no ORM; pure Python means no compiled code to match Lambda's Linux |
| Lambda zip built with **`uv.lock` as constraints** | installing only the pinned top-level package | Every indirect dependency (scramp, asn1crypto, …) gets the tested version; an unpinned new scramp release once broke pg8000 on TLS in production |
| A test that connects **over TLS** to a TLS-enabled PostgreSQL | testing only plaintext connections | The bug above lived only on the TLS code path; the test reproduces it with the old versions and passes with the fixed ones |
| Dedicated DB user with **column-level grants** | the application's user | The Lambda can lock projects, read document keys and write one column, nothing else; tested |
| **Verified TLS** (`verify-full`) to RDS with the bundled RDS CA | `sslmode=require` (encrypt only), no TLS | RDS enforces TLS; verification also proves the server is really RDS (no man-in-the-middle) |
| **Custom VPC**: public subnets for EC2, private subnets for RDS and Lambda, S3 gateway endpoint, **no NAT** | the default VPC; a NAT gateway | Private resources have no internet route at all; the gateway endpoint is free while a NAT costs ~35 USD/month |
| Security groups reference **other security groups** | IP ranges | Rules follow the component, not its address; nothing else in the subnet gets in |
| **SSM Session Manager** for server access | SSH with port 22 and a key pair | No open port, no key file; access is controlled by IAM and logged |
| Continuous deployment via **GitHub OIDC** + a role | an IAM user's access keys stored as GitHub secrets | Nothing long-lived to leak or rotate; credentials last one hour and only this repository's `production` environment gets them |
| **SSM Run Command** with a **custom document** that only runs `deploy.sh` | SSH from CI; `AWS-RunShellScript` | No open port, no key; the CI role cannot run arbitrary commands as root, and the image tag is validated by a pattern |
| Deploy **`sha-<commit>`**, serialized, never cancelled on `main` | deploy `:latest`; cancel older runs | Each merge deploys exactly the image it tested; a deployment is never interrupted half-way |
| Trust policy matches GitHub's **immutable subject** (owner and repository IDs) | the name-only `repo:<owner>/<repo>` subject | IDs are never reused: a re-created or renamed repository with the same name cannot obtain the role |
| Trust the GitHub **environment** `production` (branch rule: `main`) | trust `ref:refs/heads/main` | Adds deployment history and an optional approval step; other workflows on `main` cannot assume the role |
| **HTTP only** for the demo | HTTPS via a load balancer + certificate | HTTPS needs a domain and ~18 USD/month for a load balancer; documented as a known limitation (tokens travel unencrypted) |

## Testing and delivery

| Decision | Instead of | Because |
|---|---|---|
| Tests against a **real PostgreSQL**, one rolled-back transaction per test | SQLite, mocks, truncating tables | Tests the real constraints and migrations; fast and isolated |
| Authorization matrix test (9 endpoints × 3 roles) that also checks state | status codes only | A broken implementation could act and then return 403 |
| Query-count test for `GET /projects` | code review only | Turns the N+1 guard into a failing test |
| Migration **stairway test** (up → down → up per revision) | only `upgrade head` | A broken `downgrade()` is found before it is needed for a rollback |
| Smoke test of the **built image** in CI | unit tests only | Catches packaging, configuration and integration errors |
| Push the **tested** image to **GHCR** with the built-in token | rebuild for release; Docker Hub; ECR with stored keys | What is deployed is what was tested; no stored secret; ECR needs AWS credentials in CI |
| Feature branches, merge commits, Conventional Commits | squash merges (Phases 1–3), direct pushes | Incremental, readable history; `main` only changes through green PRs |
| `respect-gitignore = false` for ruff | ruff's default | Local lint must check exactly what CI checks |

## REST conformance

The API follows REST at **level 2 of the Richardson Maturity Model**: resources have their
own URIs, HTTP methods are used with their standard meaning (GET is safe, PUT replaces
and is idempotent, DELETE answers 204, POST creates with 201), and status codes are
precise (401 / 403 / 404 / 409 / 413 / 415 / 422 / 503). It is stateless: every request
carries its JWT. Like most JSON APIs it does not use hypermedia links (level 3, HATEOAS).

Some paths are prescribed by the brief and kept as the agreed contract, although
conventional REST would name them differently:

| Path (brief) | Deviation | Conventional REST |
|---|---|---|
| `/projects` vs `/project/{id}` | singular and plural mixed for one collection | `/projects/{id}` |
| `GET/PUT /project/{id}/info` | `/info` names a view, not a resource | `GET/PUT /projects/{id}` |
| `POST /auth` | registers a user, but the path is not a resource | `POST /users` |
| `POST /login` | an action (RPC style), which is common and acceptable | `POST /auth/token` |
| `POST /project/{id}/invite?user=` | verb in the path; data in the query string | `POST /projects/{id}/members` with a JSON body |
| `GET /project/{id}/share?with=` (optional) | **GET with a side effect** (sends an email) | `POST /projects/{id}/share-links` |
| `GET /join?token=` (optional) | GET that changes state | `GET` shows a confirmation, `POST` performs the join |

Changing the paths would break the graded contract, so they stay. If the optional share
feature is built, `GET /join` will be designed to be safe (show, then `POST` to join), so
link-preview bots cannot join projects by fetching the URL.

More detailed reasoning, with trade-offs and "when the alternative would win", is kept in
the study material that accompanies the project.
