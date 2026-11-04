-- =============================================================================
-- Project Management Dashboard - database schema WITHOUT an ORM
-- =============================================================================
-- The same tables that the ORM models + Alembic migration create, written by
-- hand in plain PostgreSQL. A test (tests/test_schema.py) applies this file to
-- an empty schema and asserts that columns, defaults, constraints and indexes
-- are identical to the migrated database, so the two can never drift apart.
--
-- Apply manually, e.g. to an empty database:
--   psql "postgresql://dashboard:dashboard@localhost:5433/dashboard" -f schema.sql
--
-- In the application, Alembic migrations remain the source of truth: they can
-- also CHANGE an existing schema step by step, which a CREATE script cannot.
-- =============================================================================

BEGIN;

-- Registered accounts. Only a password HASH is stored, never the password.
CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY,
    login         VARCHAR(64)  NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_login UNIQUE (login),
    CONSTRAINT ck_users_login_min_length CHECK (char_length(login) >= 3)
);

-- Projects. total_size_bytes is a deliberate denormalization: it duplicates
-- SUM(documents.size_bytes) so the size limit can be checked with one read.
CREATE TABLE projects (
    id               BIGINT GENERATED ALWAYS AS IDENTITY,
    name             VARCHAR(100) NOT NULL,
    description      TEXT         NOT NULL DEFAULT '',
    total_size_bytes BIGINT       NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_projects PRIMARY KEY (id),
    CONSTRAINT ck_projects_name_not_blank CHECK (char_length(btrim(name)) > 0),
    CONSTRAINT ck_projects_total_size_non_negative CHECK (total_size_bytes >= 0)
);

-- Document METADATA. The file bytes live in S3 under storage_key.
CREATE TABLE documents (
    id             BIGINT GENERATED ALWAYS AS IDENTITY,
    project_id     BIGINT       NOT NULL,
    filename       VARCHAR(255) NOT NULL,
    content_type   VARCHAR(127) NOT NULL,
    size_bytes     BIGINT       NOT NULL,
    storage_key    VARCHAR(512) NOT NULL,
    uploaded_by_id BIGINT,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_documents PRIMARY KEY (id),
    CONSTRAINT fk_documents_project_id_projects
        FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_documents_uploaded_by_id_users
        FOREIGN KEY (uploaded_by_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_documents_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_documents_size_non_negative CHECK (size_bytes >= 0)
);

CREATE INDEX ix_documents_project_id ON documents (project_id);

-- Who may access which project, and with which role (many-to-many).
CREATE TABLE project_members (
    project_id BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    role       VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_project_members PRIMARY KEY (project_id, user_id),
    CONSTRAINT fk_project_members_project_id_projects
        FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_project_members_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT ck_project_members_project_role CHECK (role IN ('owner', 'participant'))
);

-- "Which projects can I access?" starts from user_id.
CREATE INDEX ix_project_members_user_id ON project_members (user_id);

-- At most one owner per project (partial unique index).
CREATE UNIQUE INDEX uq_project_members_one_owner
    ON project_members (project_id)
    WHERE role = 'owner';

COMMIT;
