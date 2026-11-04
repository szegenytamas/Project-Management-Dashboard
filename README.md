# Project Management Dashboard

A REST API to create, update, share and delete projects and their attached
documents (PDF, DOCX). Built with FastAPI, PostgreSQL, Docker and AWS
(EC2, RDS, S3, Lambda), with CI/CD on GitHub Actions.

> Status: **Phase 1 – project skeleton.** See the roadmap below.

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/) and Docker Desktop.

```bash
uv sync                      # create .venv and install everything from uv.lock
uv run tox                   # lint + type-check + tests, exactly like CI
uv run project-dashboard     # run the API locally with auto-reload
docker compose up --build    # or: run API + Postgres in containers
```

Then open <http://localhost:8000/docs>. Copy `.env.example` to `.env` to change settings.

| Command | What it does |
|---|---|
| `uv run tox -e lint` | `ruff check` + `ruff format --check` |
| `uv run tox -e type` | `mypy` in strict mode |
| `uv run tox -e test` | `pytest` with coverage |
| `uv run ruff format .` | auto-format the code |
| `uv build` | build the wheel and sdist into `dist/` |

## Access model

| Role | How you get it | Can do |
|---|---|---|
| **Owner** | You created the project | Everything: edit, upload, delete documents, invite, delete project |
| **Participant** | The owner invited you | View and edit the project, upload and update documents. **Cannot delete** anything or invite others |

Every business endpoint requires a JWT issued by `POST /login` (valid for 1 hour).
Permissions are resolved from the database on every request.

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
- [ ] **Phase 2** – SQLAlchemy models, Alembic migrations, raw `schema.sql` (without ORM)
- [ ] **Phase 3** – Auth: register, login, JWT
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
