"""Tests for the size-limit Lambda (functions/size_limit/size_limit.py).

The Lambda talks to PostgreSQL WITHOUT the ORM, on its own connection, and its
work must be visible to other connections - so these tests use COMMITTED data
(through pg8000, like the Lambda) and clean it up afterwards, instead of the
rolled-back transaction the API tests use. S3 is moto's in-memory S3.
"""

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pg8000.native
import pytest
from size_limit import (
    Config,
    ProjectResult,
    S3Object,
    connect,
    handler,
    objects_to_delete,
    process_event,
    projects_in_event,
)

from project_dashboard.core.config import Settings
from project_dashboard.storage import S3Storage
from tests.conftest import bucket_keys

GRANTS_SQL = Path(__file__).parents[1] / "functions" / "size_limit" / "grants.sql"
LIMIT = 1_000


def s3_event(bucket: str, *keys: str, name: str = "ObjectCreated:Put") -> dict[str, Any]:
    return {
        "Records": [
            {"eventName": name, "s3": {"bucket": {"name": bucket}, "object": {"key": k}}}
            for k in keys
        ]
    }


# --------------------------------------------------------- pure functions --
def test_projects_in_event_parses_decodes_and_deduplicates() -> None:
    event = s3_event(
        "b",
        "projects/7/a.pdf",
        "projects/7/b+c.pdf",  # URL-encoded space
        "projects/12/x.docx",
        "somewhere/else.txt",  # not a project file
    )

    assert projects_in_event(event) == {"b": {7, 12}}


def test_objects_to_delete_removes_newest_unregistered_first() -> None:
    now = datetime.now(UTC)
    objs = [
        S3Object("registered-old", 600, now - timedelta(hours=2)),
        S3Object("orphan-old", 300, now - timedelta(hours=1)),
        S3Object("bypass-new", 400, now),
    ]  # 1300 bytes, limit 1000

    victims = objects_to_delete(objs, registered_keys={"registered-old"}, limit=LIMIT)

    assert [v.key for v in victims] == ["bypass-new"]  # 900 left: within the limit


def test_objects_to_delete_never_touches_registered_documents() -> None:
    now = datetime.now(UTC)
    objs = [S3Object("a", 800, now), S3Object("b", 800, now)]

    assert objects_to_delete(objs, registered_keys={"a", "b"}, limit=LIMIT) == []


# ------------------------------------------------- database-backed tests --
@dataclass
class Db:
    """Committed test data via pg8000, removed again after each test."""

    conn: pg8000.native.Connection
    settings: Settings
    project_ids: list[int]
    user_ids: list[int]

    def project(self, total: int = 0) -> int:
        [[user_id]] = self.conn.run(
            "INSERT INTO users (login, password_hash) VALUES (:login, 'x') RETURNING id",
            login=f"lambda-{time.monotonic_ns()}",
        )
        [[project_id]] = self.conn.run(
            "INSERT INTO projects (name, total_size_bytes) VALUES ('Lambda test', :t) RETURNING id",
            t=total,
        )
        self.conn.run(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (:p, :u, 'owner')",
            p=project_id,
            u=user_id,
        )
        self.user_ids.append(user_id)
        self.project_ids.append(project_id)
        return int(project_id)

    def register(self, project_id: int, key: str, size: int) -> None:
        self.conn.run(
            "INSERT INTO documents (project_id, filename, content_type, size_bytes, storage_key)"
            " VALUES (:p, 'f.pdf', 'application/pdf', :s, :k)",
            p=project_id,
            s=size,
            k=key,
        )

    def total(self, project_id: int) -> int:
        [[total]] = self.conn.run(
            "SELECT total_size_bytes FROM projects WHERE id = :p", p=project_id
        )
        return int(total)

    def config(self, **overrides: Any) -> Config:
        s = self.settings
        values: dict[str, Any] = {
            "limit_bytes": LIMIT,
            "db_host": s.postgres_host,
            "db_port": s.postgres_port,
            "db_name": s.postgres_db,
            "db_user": s.postgres_user,
            "db_password": s.postgres_password.get_secret_value(),
            "ssl_mode": "disable",  # local container; RDS uses verify-full
        }
        return Config(**(values | overrides))


@pytest.fixture
def db(settings: Settings, migrated_database: Any) -> Iterator[Db]:
    conn = pg8000.native.Connection(
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )
    data = Db(conn, settings, [], [])
    yield data
    for project_id in data.project_ids:  # cascades to members and documents
        conn.run("DELETE FROM projects WHERE id = :p", p=project_id)
    for user_id in data.user_ids:
        conn.run("DELETE FROM users WHERE id = :u", u=user_id)
    conn.close()


def put(storage: S3Storage, key: str, size: int) -> None:
    storage._client.put_object(Bucket=storage.bucket, Key=key, Body=b"x" * size)


def run_lambda(db: Db, storage: S3Storage, *keys: str) -> list[ProjectResult]:
    conn = connect(db.config())
    try:
        return process_event(s3_event(storage.bucket, *keys), storage._client, conn, LIMIT)
    finally:
        conn.close()


def test_total_is_recalculated_from_s3(db: Db, storage: S3Storage) -> None:
    project = db.project(total=999)  # drifted, wrong value
    for name, size in (("a", 100), ("b", 250)):
        put(storage, f"projects/{project}/{name}.pdf", size)
        db.register(project, f"projects/{project}/{name}.pdf", size)

    [result] = run_lambda(db, storage, f"projects/{project}/b.pdf")

    assert (result.total_bytes, result.deleted_keys) == (350, [])
    assert db.total(project) == 350  # drift corrected


def test_upload_around_the_api_is_removed_when_over_the_limit(db: Db, storage: S3Storage) -> None:
    project = db.project()
    put(storage, f"projects/{project}/doc.pdf", 700)
    db.register(project, f"projects/{project}/doc.pdf", 700)
    bypass = f"projects/{project}/uploaded-directly-to-s3.pdf"
    put(storage, bypass, 500)  # 1200 > 1000, and no document row

    [result] = run_lambda(db, storage, bypass)

    assert result.deleted_keys == [bypass]
    assert bucket_keys(storage) == [f"projects/{project}/doc.pdf"]
    assert db.total(project) == 700


def test_registered_documents_are_never_deleted(db: Db, storage: S3Storage) -> None:
    project = db.project()
    for name in ("a", "b"):
        put(storage, f"projects/{project}/{name}.pdf", 800)
        db.register(project, f"projects/{project}/{name}.pdf", 800)

    [result] = run_lambda(db, storage, f"projects/{project}/b.pdf")

    assert result.deleted_keys == []
    assert result.still_over_limit  # logged as an error for a human to look at
    assert len(bucket_keys(storage)) == 2
    assert db.total(project) == 1600


def test_deleted_project_is_skipped(db: Db, storage: S3Storage) -> None:
    put(storage, "projects/987654/left-over.pdf", 10)

    [result] = run_lambda(db, storage, "projects/987654/left-over.pdf")

    assert not result.exists


def test_lambda_waits_for_an_upload_in_progress(db: Db, storage: S3Storage) -> None:
    """The API holds the project lock from before its S3 upload until its commit.

    While it does, the new object is in S3 but has no document row yet. A Lambda
    that did not wait would see an 'unregistered' object and delete it.
    """
    project = db.project()
    key = f"projects/{project}/in-flight.pdf"
    api = pg8000.native.Connection(
        user=db.settings.postgres_user,
        password=db.settings.postgres_password.get_secret_value(),
        host=db.settings.postgres_host,
        port=db.settings.postgres_port,
        database=db.settings.postgres_db,
    )
    api.run("START TRANSACTION")
    api.run("SELECT id FROM projects WHERE id = :p FOR UPDATE", p=project)  # API takes the lock
    put(storage, key, 1_500)  # ... uploads to S3 (over the limit, row not committed yet)

    results: list[ProjectResult] = []
    worker = threading.Thread(target=lambda: results.extend(run_lambda(db, storage, key)))
    started = time.monotonic()
    worker.start()
    time.sleep(0.5)
    assert worker.is_alive()  # the Lambda is waiting for the lock
    api.run(
        "INSERT INTO documents (project_id, filename, content_type, size_bytes, storage_key)"
        " VALUES (:p, 'f.pdf', 'application/pdf', 1500, :k)",
        p=project,
        k=key,
    )
    api.run("COMMIT")  # ... and commits: the document is now registered
    worker.join(timeout=10)
    api.close()

    assert time.monotonic() - started >= 0.5
    assert results[0].deleted_keys == []  # the API's file survived
    assert bucket_keys(storage) == [key]


def test_least_privilege_database_user_is_sufficient(
    db: Db, storage: S3Storage, settings: Settings
) -> None:
    """grants.sql, applied for real: the Lambda works with exactly those rights."""
    role, password = "size_lambda_test", "test-only-password"
    sql = (
        GRANTS_SQL.read_text(encoding="utf-8")
        .replace(':"role"', role)
        .replace(":'password'", f"'{password}'")
        .replace(':"database"', settings.postgres_db)
    )
    db.conn.run(f"DROP ROLE IF EXISTS {role}")
    for statement in filter(None, (s.strip() for s in _strip_comments(sql).split(";"))):
        db.conn.run(statement)
    try:
        project = db.project()
        put(storage, f"projects/{project}/bypass.pdf", 1_200)
        conn = connect(db.config(db_user=role, db_password=password))
        try:
            [result] = process_event(
                s3_event(storage.bucket, f"projects/{project}/bypass.pdf"),
                storage._client,
                conn,
                LIMIT,
            )
            assert result.deleted_keys == [f"projects/{project}/bypass.pdf"]
            # ... and it really cannot read anything else:
            with pytest.raises(pg8000.exceptions.DatabaseError, match="permission denied"):
                conn.run("SELECT password_hash FROM users")
        finally:
            conn.close()
    finally:
        db.conn.run(f"DROP OWNED BY {role}")
        db.conn.run(f"DROP ROLE {role}")


def test_handler_reads_its_configuration_from_the_environment(
    db: Db, storage: S3Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = db.project()
    put(storage, f"projects/{project}/a.pdf", 42)
    s = db.settings
    for name, value in {
        "POSTGRES_HOST": s.postgres_host,
        "POSTGRES_PORT": str(s.postgres_port),
        "POSTGRES_DB": s.postgres_db,
        "POSTGRES_USER": s.postgres_user,
        "POSTGRES_PASSWORD": s.postgres_password.get_secret_value(),
        "POSTGRES_SSL_MODE": "disable",
        "PROJECT_SIZE_LIMIT_BYTES": str(LIMIT),
        "AWS_DEFAULT_REGION": s.aws_region,
    }.items():
        monkeypatch.setenv(name, value)

    response = handler(s3_event(storage.bucket, f"projects/{project}/a.pdf"), context=None)

    assert response == {"projects": [{"project_id": project, "total_bytes": 42, "deleted": []}]}
    assert db.total(project) == 42


def _strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
