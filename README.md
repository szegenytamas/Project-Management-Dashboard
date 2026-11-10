# Project Management Dashboard

[![CI](https://github.com/szegenytamas/Project-Management-Dashboard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/szegenytamas/Project-Management-Dashboard/actions/workflows/ci.yml)

A REST API to create, update, share and delete projects and their attached
documents (PDF, DOCX). Built with FastAPI, PostgreSQL, Docker and AWS
(EC2, RDS, S3, Lambda), with CI/CD on GitHub Actions.

> Status: **Phase 7 – deployed on AWS** (eu-north-1). See the roadmap below.

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Components, code layers, request and upload flows, delivery pipeline (with diagrams) |
| [Database design](docs/database.md) | ER diagram, constraints, normalization, the denormalized total, **ORM vs. raw SQL** |
| [Design decisions](docs/decisions.md) | Key choices and their alternatives, **REST conformance** |
| [AWS deployment](docs/aws.md) | Deployed architecture, resources, security, **setup notes**, **evidence**, costs, teardown |
| [Security](docs/security.md) | OWASP API Top 10 checklist, scanning, least privilege, known gaps |
| [AI-assisted workflow](docs/ai-workflow.md) | How Claude Code was used: delegated, reviewed, decided |
| [CLAUDE.md](CLAUDE.md) | Conventions the AI assistant works under |

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/) and Docker Desktop.

```bash
cp .env.example .env         # local settings (DB on localhost:5433, S3 emulator on :5000)
uv sync                      # create .venv and install everything from uv.lock
docker compose up -d db s3   # start PostgreSQL and the S3 emulator
uv run alembic upgrade head  # create / update the tables
uv run python -m project_dashboard.db.seed   # optional: demo users, projects, documents
uv run tox                   # lint + type-check + tests, exactly like CI
uv run project-dashboard     # run the API locally with auto-reload
```

Or run everything in containers: `docker compose up --build` starts Postgres and
the S3 emulator, runs the migrations, then starts the API. Open <http://localhost:8000/docs>.

Locally, S3 is emulated by [moto](https://github.com/getmoto/moto) (`motoserver/moto`):
the same S3 API as AWS, no account needed. Its files live in memory and are gone after
`docker compose down`. The tests use moto in-process, so they need no container for S3.

| Command | What it does |
|---|---|
| `uv run tox -e lint` | `ruff check` + `ruff format --check` |
| `uv run tox -e type` | `mypy` in strict mode |
| `uv run tox -e test` | `pytest` with coverage (uses a separate `<db>_test` database) |
| `uv run ruff format .` | auto-format the code |
| `uv build` | build the wheel and sdist into `dist/` |
| `uv run alembic revision --autogenerate -m "..."` | create a migration from model changes – **always review it** |
| `uv run alembic upgrade head` / `downgrade -1` | apply / undo migrations |
| `python -m project_dashboard.db.migrate` | apply migrations without `alembic.ini` (containers, servers) |
| `python -m project_dashboard.db.seed` | add the demo data (safe to run again) |

### Demo data

The seed creates three users and three projects with small PDF/DOCX documents, using the same
services as the API (hashed passwords, one owner per project, files in S3, correct totals):

| User | Owner of | Participant in |
|---|---|---|
| `alice` | Website relaunch, Mobile app | Research notes |
| `bob` | Research notes | Website relaunch |
| `carol` | – | – (the outsider: sees 404 for every project) |

Locally all three log in with `Demo-Pass-2026!`. With `ENVIRONMENT=production` there is no
default: the password must come from `SEED_PASSWORD`. Running the seed again changes nothing;
existing users keep their passwords. In Compose: `docker compose run --rm api python -m
project_dashboard.db.seed` (it starts the database, migrations and S3 first). The emulator keeps
files in memory only: after restarting the `s3` container, the seeded rows remain but their files
are gone - reset with `docker compose down -v` and seed again.

To click through the API, open [`requests.http`](requests.http) in VS Code (REST Client
extension) or a JetBrains IDE: register, log in, then every request reuses the token.

## Container image

Every merge to `main` publishes the image that passed CI to GitHub Container Registry:

```bash
docker pull ghcr.io/szegenytamas/project-management-dashboard:latest        # newest main
docker pull ghcr.io/szegenytamas/project-management-dashboard:sha-<commit>  # one exact commit
```

After the push, the **Deploy to AWS** job puts that exact image live on the EC2 server: GitHub's
OIDC token is exchanged for a short-lived AWS role (no stored keys), which may only run one SSM
document - `deploy.sh` - on the server. See [AWS deployment](docs/aws.md#continuous-deployment).

The image needs the same environment variables as Docker Compose (`POSTGRES_*`,
`JWT_SECRET_KEY`, `S3_*`); see [`.env.example`](.env.example). Migrations run separately with
`python -m project_dashboard.db.migrate`.

## Size-limit Lambda

[`functions/size_limit/`](functions/size_limit/) is the AWS Lambda triggered by S3
`ObjectCreated` / `ObjectRemoved` events. For each affected project it:

1. **locks the project row** (`SELECT … FOR UPDATE`), the same lock the API holds during an
   upload, so it waits until any upload in progress has committed;
2. **sums the sizes of all objects** under `projects/{id}/` in S3;
3. if the total exceeds `PROJECT_SIZE_LIMIT_BYTES`, **deletes objects that have no document
   row** (uploaded around the API, or orphans), newest first, until the project fits.
   Documents registered by the API are never deleted;
4. **writes the total** to `projects.total_size_bytes`.

It uses **plain SQL without an ORM** (pg8000) and a database user that may only read document
keys and update that one column ([`grants.sql`](functions/size_limit/grants.sql)). Connections to
RDS use verified TLS ([`certs/`](certs/)). Build the deployment zip with
`uv run python functions/size_limit/package.py`; CI publishes it as the `lambda-size-limit`
artifact of every run.

## AWS setup

Deployed in **eu-north-1** (Stockholm): the API in Docker on **EC2** (public subnet, no SSH,
Session Manager), **RDS PostgreSQL 17** in private subnets with verified TLS, documents in **S3**,
and the size-limit **Lambda** on S3 events, all in a custom VPC without a NAT gateway. IAM roles
instead of keys; secrets in SSM Parameter Store.

Setup steps, security design, verification evidence (screenshots, CloudWatch logs), known
limitations, costs and teardown: **[docs/aws.md](docs/aws.md)**. Policies and server scripts:
[`infra/aws/`](infra/aws/).

## Database

| Table | Purpose |
|---|---|
| `users` | Accounts: unique `login`, `password_hash` |
| `projects` | `name`, `description`, `total_size_bytes` (deliberately denormalized sum of document sizes) |
| `project_members` | Who may access which project, with role `owner` / `participant`; at most one owner per project |
| `documents` | File metadata (`filename`, `content_type`, `size_bytes`); the bytes live in S3 under `storage_key` |

## Documents

| Rule | Value |
|---|---|
| Accepted types | PDF and DOCX, detected from the file **content** (magic bytes); the extension must match |
| Max file size | 10 MB (`MAX_FILE_SIZE_BYTES`) |
| Max project size | 50 MB total (`PROJECT_SIZE_LIMIT_BYTES`), checked with the project row locked |
| Files per upload | 10; one invalid file rejects the whole upload |
| Storage | S3 key `projects/{project_id}/{random}.pdf`; the original name is kept as metadata |

Upload / replace store the file in S3 **before** committing the database row (and delete it
again if the commit fails); delete removes the row **before** the file. A database row
therefore never points to a missing file.

The schema exists twice: as ORM models + Alembic migrations
(`src/project_dashboard/db/`) and as plain SQL without an ORM
(`src/project_dashboard/db/schema.sql`). A test guarantees they are identical.
The rationale (normalization, the denormalized total, ORM vs. raw SQL) is in
[docs/database.md](docs/database.md).

## Access model

| Role | How you get it | Can do |
|---|---|---|
| **Owner** | You created the project | Everything: edit, upload, delete documents, invite, delete project |
| **Participant** | The owner invited you | View and edit the project, upload and update documents. **Cannot delete** anything or invite others |

Every business endpoint requires a JWT issued by `POST /login` (valid for 1 hour).
Permissions are resolved from the database on every request, by one function
(`services/permissions.py`) that every endpoint uses:

| Action | Owner | Participant | Not a member |
|---|---|---|---|
| View info, list documents, download | ✅ | ✅ | 404 |
| Update info, upload / replace documents | ✅ | ✅ | 404 |
| Delete documents, invite users, delete the project | ✅ | 403 | 404 |

Non-members get **404, not 403**, so they cannot tell someone else's project
from a project that does not exist.

### Authentication

```bash
# 1. Register
curl -X POST localhost:8000/auth -H "Content-Type: application/json" \
     -d '{"login": "alice", "password": "Correct-Horse-42!", "repeat_password": "Correct-Horse-42!"}'

# 2. Log in -> {"access_token": "...", "token_type": "bearer", "expires_in": 3600}
curl -X POST localhost:8000/login -H "Content-Type: application/json" \
     -d '{"login": "alice", "password": "Correct-Horse-42!"}'

# 3. Send the token with every other request
curl localhost:8000/projects -H "Authorization: Bearer <access_token>"
```

In Swagger UI (`/docs`) click **Authorize** and paste the `access_token`.

- **Login:** 3–64 characters (letters, digits, `_` `.` `-`), case-insensitive.
- **Password policy:** 12–128 characters with an uppercase letter, a lowercase letter, a
  digit and a special character; it must not contain the login (case-insensitive).
- Passwords are stored only as salted **argon2id** hashes.
- Tokens are HS256-signed JWTs (`sub` = user id, `iat`, `exp` = +60 min). The signing key
  `JWT_SECRET_KEY` (≥ 32 characters) must come from the environment.
- A wrong password and an unknown login return the same `401` in the same time.
- `422` validation errors never echo the submitted values (e.g. passwords).
- `POST /auth` and `POST /login` allow 10 requests per minute per client IP
  (`AUTH_RATE_LIMIT_PER_MINUTE`); more get `429` with a `Retry-After` header.
- Every response carries security headers (`nosniff`, no framing, a strict
  Content-Security-Policy, no referrer). Browsers on other origins are refused unless listed in
  `CORS_ALLOWED_ORIGINS`. More in [docs/security.md](docs/security.md).

## API

| Method | Path | Who | Success |
|---|---|---|---|
| POST | `/auth` | anyone – register (`login`, `password`, `repeat_password`) | 201 |
| POST | `/login` | anyone – returns a JWT | 200 |
| POST | `/projects` | logged in – creator becomes owner | 201 |
| GET | `/projects` | logged in – all accessible projects with documents | 200 |
| GET | `/project/{project_id}/info` | owner, participant | 200 |
| PUT | `/project/{project_id}/info` | owner, participant | 200 |
| DELETE | `/project/{project_id}` | owner | 204 |
| GET | `/project/{project_id}/documents` | owner, participant | 200 |
| POST | `/project/{project_id}/documents` | owner, participant | 201 |
| GET | `/document/{document_id}` | owner, participant – file download | 200 |
| PUT | `/document/{document_id}` | owner, participant | 200 |
| DELETE | `/document/{document_id}` | owner | 204 |
| POST | `/project/{project_id}/invite?user=<login>` | owner | 201 |
| GET | `/project/{project_id}/share?with=<email>` | owner – *optional* | 200 |
| GET | `/join?token=<token>` | logged in – *optional* | 200 |

All responses are JSON except file downloads. Errors: `401` not authenticated,
`403` role not allowed, `404` not found **or no access** (existence is not leaked),
`409` conflict, `413` size limit exceeded, `415` unsupported file type, `422` validation error,
`429` too many login/registration attempts, `503` document storage temporarily unavailable.

## Roadmap

- [x] **Phase 0** – Git repository, GitHub, conventions
- [x] **Phase 1** – Skeleton: uv, `pyproject.toml`, tox, settings, Dockerfile, Docker Compose (API + Postgres), health check, first CI
- [x] **Phase 2** – SQLAlchemy models, Alembic migrations, raw `schema.sql` (without ORM)
- [x] **Phase 3** – Auth: register, login, JWT
- [x] **Phase 4** – Projects, invites, owner/participant permissions, stronger password policy
- [x] **Phase 5** – Documents in S3: upload (PDF/DOCX), download, replace, delete, size limit
- [x] **Phase 6** – CI pushes the image to a registry (GHCR); `docs/`: architecture, data model
  rationale, ORM vs. raw SQL, design decisions
- [x] **Phase 7** – AWS: S3 bucket, Lambda (project size sum **and** limit enforcement), RDS,
  deployment on EC2, evidence (screenshots / logs)
- [ ] **Phase 8** – Extensions: ~~seed data~~ (done: seed script + migration stairway test),
  documented AI-assisted workflow, ~~continuous deployment~~ (done: OIDC + SSM Run Command),
  ~~security hardening~~ (done: rate limit, headers, CORS, dependency + secret scanning,
  OWASP checklist)
- [ ] **Phase 9** – *Optional:* web UI, share link via email

## Development workflow

- `main` is always deployable; it only changes through pull requests that pass CI
  (enforced by a branch ruleset).
- One branch per phase/feature, with several small commits that each leave the project
  working. PRs are merged with **"Create a merge commit"**, so every step stays in the history.
  (Phases 1–3 were squash-merged: one commit per phase.)
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `ci:`, `refactor:`.
- AI assistance (Claude Code) is used under the conventions in [`CLAUDE.md`](CLAUDE.md);
  how it was used is documented in [`docs/ai-workflow.md`](docs/ai-workflow.md).
