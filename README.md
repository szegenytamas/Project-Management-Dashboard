# Project Management Dashboard

A REST API to create, update, share and delete projects and their attached
documents (PDF, DOCX). Built with FastAPI, PostgreSQL, Docker and AWS
(EC2, RDS, S3, Lambda), with CI/CD on GitHub Actions.

> Status: **Phase 3 – authentication.** See the roadmap below.

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
Permissions are resolved from the database on every request.

### Authentication

```bash
# 1. Register (login: 3-64 chars, letters/digits/_ . -, case-insensitive; password: 8-128 chars)
curl -X POST localhost:8000/auth -H "Content-Type: application/json" \
     -d '{"login": "alice", "password": "correct-horse-42", "repeat_password": "correct-horse-42"}'

# 2. Log in -> {"access_token": "...", "token_type": "bearer", "expires_in": 3600}
curl -X POST localhost:8000/login -H "Content-Type: application/json" \
     -d '{"login": "alice", "password": "correct-horse-42"}'

# 3. Send the token with every other request
curl localhost:8000/projects -H "Authorization: Bearer <access_token>"
```

In Swagger UI (`/docs`) click **Authorize** and paste the `access_token`.

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
- [ ] **Phase 4** – Projects, invites, permissions
- [ ] **Phase 5** – Documents, S3 storage, file validation, project size limit
- [ ] **Phase 6** – Full CI: lint, type-check, tests, build, push to ECR
- [ ] **Phase 7** – AWS: RDS, S3, EC2, Lambda (size calculation), CD on merge
- [ ] **Phase 8** – *Optional:* share link via email (SES), image resize
- [ ] **Phase 9** – *Optional:* web UI – static HTML/JS client of the JSON API
  (login, project list, project details & documents, invite, share-link landing page)

## Development workflow

- `main` is always deployable; it only changes through pull requests.
- One branch and one pull request per phase/feature, e.g. `feat/auth`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `ci:`, `refactor:`.
