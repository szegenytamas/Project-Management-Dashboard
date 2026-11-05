# Project Management Dashboard

A REST API to create, update, share and delete projects and their attached
documents (PDF, DOCX). Built with FastAPI, PostgreSQL, Docker and AWS
(EC2, RDS, S3, Lambda), with CI/CD on GitHub Actions.

> Status: **Phase 4 – projects, invitations, permissions.** See the roadmap below.

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/) and Docker Desktop.

```bash
cp .env.example .env         # local settings (DB on localhost:5433)
uv sync                      # create .venv and install everything from uv.lock
docker compose up -d db      # start PostgreSQL
uv run alembic upgrade head  # create / update the tables
uv run tox                   # lint + type-check + tests, exactly like CI
uv run project-dashboard     # run the API locally with auto-reload
```

Or run everything in containers: `docker compose up --build` starts Postgres,
runs the migrations, then starts the API. Open <http://localhost:8000/docs>.

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

To click through the API, open [`requests.http`](requests.http) in VS Code (REST Client
extension) or a JetBrains IDE: register, log in, then every request reuses the token.

## Database

| Table | Purpose |
|---|---|
| `users` | Accounts: unique `login`, `password_hash` |
| `projects` | `name`, `description`, `total_size_bytes` (deliberately denormalized sum of document sizes) |
| `project_members` | Who may access which project, with role `owner` / `participant`; at most one owner per project |
| `documents` | File metadata (`filename`, `content_type`, `size_bytes`); the bytes live in S3 under `storage_key` |

The schema exists twice: as ORM models + Alembic migrations
(`src/project_dashboard/db/`) and as plain SQL without an ORM
(`src/project_dashboard/db/schema.sql`). A test guarantees they are identical.

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
`409` conflict, `413` size limit exceeded, `415` unsupported file type, `422` validation error.

## Roadmap

- [x] **Phase 0** – Git repository, GitHub, conventions
- [x] **Phase 1** – Skeleton: uv, `pyproject.toml`, tox, settings, Dockerfile, Docker Compose (API + Postgres), health check, first CI
- [x] **Phase 2** – SQLAlchemy models, Alembic migrations, raw `schema.sql` (without ORM)
- [x] **Phase 3** – Auth: register, login, JWT
- [x] **Phase 4** – Projects, invites, owner/participant permissions, stronger password policy
- [ ] **Phase 5** – Documents in S3: upload (PDF/DOCX), download, replace, delete, size limit
- [ ] **Phase 6** – CI pushes the image to a registry (GHCR); `docs/`: architecture, data model
  rationale, ORM vs. raw SQL, design decisions
- [ ] **Phase 7** – AWS: S3 bucket, Lambda (project size sum **and** limit enforcement), RDS,
  deployment on EC2, evidence (screenshots / logs)
- [ ] **Phase 8** – Extensions: seed data, documented AI-assisted workflow, continuous deployment,
  security hardening
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
