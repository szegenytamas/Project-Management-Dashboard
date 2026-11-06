"""AWS Lambda: keep each project's total file size correct and enforce the limit.

Trigger: S3 events (ObjectCreated / ObjectRemoved) for keys `projects/{id}/...`.

For every project named in the event:

1. Lock the project row (SELECT ... FOR UPDATE). The API holds the SAME lock
   from before it uploads a file until its database commit, so the Lambda
   waits exactly as long as an upload is still in progress - and then sees
   every upload either committed or rolled back. (Lock timeout: 20 s.)
2. List the project's objects in S3 and add up their sizes. S3 is the source
   of truth for how many bytes are really stored.
3. If the total exceeds the limit: delete objects that have NO document row
   (uploaded around the API, or left over as orphans), newest first, until
   the project is within the limit. Documents the API registered are never
   deleted - the API already checked the limit for them under the same lock.
4. Write the total to projects.total_size_bytes (the denormalized column).

Deliberately written WITHOUT an ORM: a few parameterized SQL statements via
pg8000 (a pure-Python PostgreSQL driver - nothing to compile for Lambda). This
is the project's "query path without the ORM". The database user it runs as
may only read document keys and update one column (see grants.sql).
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus

import boto3
import pg8000.native

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = logging.getLogger("size_limit")
logger.setLevel(logging.INFO)

KEY_PATTERN = re.compile(r"^projects/(\d+)/")
S3_DELETE_BATCH = 1000
HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Config:
    limit_bytes: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    ssl_mode: str = "verify-full"
    ssl_root_cert: str = os.path.join(HERE, "rds-global-bundle.pem")
    lock_timeout: str = "20s"

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        return cls(
            limit_bytes=int(env.get("PROJECT_SIZE_LIMIT_BYTES", 50 * 1024 * 1024)),
            db_host=env["POSTGRES_HOST"],
            db_port=int(env.get("POSTGRES_PORT", "5432")),
            db_name=env.get("POSTGRES_DB", "dashboard"),
            db_user=env.get("POSTGRES_USER", "size_lambda"),
            db_password=env["POSTGRES_PASSWORD"],
            ssl_mode=env.get("POSTGRES_SSL_MODE", "verify-full"),
            ssl_root_cert=env.get(
                "POSTGRES_SSL_ROOT_CERT", os.path.join(HERE, "rds-global-bundle.pem")
            ),
        )


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    last_modified: datetime


@dataclass
class ProjectResult:
    project_id: int
    exists: bool
    total_bytes: int = 0
    deleted_keys: list[str] = field(default_factory=list)
    still_over_limit: bool = False


# ------------------------------------------------------------ pure functions --
def projects_in_event(event: dict[str, Any]) -> dict[str, set[int]]:
    """{bucket: {project ids}} for all records with a `projects/{id}/` key."""
    found: dict[str, set[int]] = {}
    for record in event.get("Records", []):
        s3 = record.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        # Keys in S3 events are URL-encoded ("a b.pdf" arrives as "a+b.pdf").
        key = unquote_plus(s3.get("object", {}).get("key", ""))
        match = KEY_PATTERN.match(key)
        if bucket and match:
            found.setdefault(bucket, set()).add(int(match.group(1)))
    return found


def objects_to_delete(
    objects: list[S3Object], registered_keys: set[str], limit: int
) -> list[S3Object]:
    """Unregistered objects to remove, newest first, until total <= limit."""
    total = sum(o.size for o in objects)
    unregistered = sorted(
        (o for o in objects if o.key not in registered_keys),
        key=lambda o: o.last_modified,
        reverse=True,
    )
    victims = []
    for obj in unregistered:
        if total <= limit:
            break
        victims.append(obj)
        total -= obj.size
    return victims


# ------------------------------------------------------------- side effects --
def list_objects(s3: S3Client, bucket: str, prefix: str) -> list[S3Object]:
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            objects.append(S3Object(item["Key"], item["Size"], item["LastModified"]))
    return objects


def delete_objects(s3: S3Client, bucket: str, keys: list[str]) -> None:
    for start in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[start : start + S3_DELETE_BATCH]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]})


def process_project(
    conn: pg8000.native.Connection, s3: S3Client, bucket: str, project_id: int, limit: int
) -> ProjectResult:
    conn.run("START TRANSACTION")
    try:
        # Waits while the API holds this lock (an upload in progress).
        locked = conn.run("SELECT id FROM projects WHERE id = :pid FOR UPDATE", pid=project_id)
        if not locked:
            # Project deleted (the API removes its files) or never existed.
            conn.run("ROLLBACK")
            return ProjectResult(project_id, exists=False)

        objects = list_objects(s3, bucket, f"projects/{project_id}/")
        total = sum(o.size for o in objects)
        result = ProjectResult(project_id, exists=True)

        if total > limit:
            rows = conn.run(
                "SELECT storage_key FROM documents WHERE project_id = :pid", pid=project_id
            )
            registered = {row[0] for row in rows}
            victims = objects_to_delete(objects, registered, limit)
            if victims:
                delete_objects(s3, bucket, [v.key for v in victims])
                result.deleted_keys = [v.key for v in victims]
                total -= sum(v.size for v in victims)
            result.still_over_limit = total > limit

        conn.run(
            "UPDATE projects SET total_size_bytes = :total WHERE id = :pid",
            total=total,
            pid=project_id,
        )
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise
    result.total_bytes = total
    return result


def process_event(
    event: dict[str, Any], s3: S3Client, conn: pg8000.native.Connection, limit: int
) -> list[ProjectResult]:
    results = []
    for bucket, project_ids in projects_in_event(event).items():
        for project_id in sorted(project_ids):
            result = process_project(conn, s3, bucket, project_id, limit)
            results.append(result)
            log_result(result, limit)
    return results


def log_result(result: ProjectResult, limit: int) -> None:
    # One JSON line per project: searchable in CloudWatch Logs Insights.
    entry = {
        "project_id": result.project_id,
        "exists": result.exists,
        "total_bytes": result.total_bytes,
        "limit_bytes": limit,
        "deleted_keys": result.deleted_keys,
        "still_over_limit": result.still_over_limit,
    }
    if result.still_over_limit:
        logger.error(json.dumps({"event": "project_over_limit", **entry}))
    elif result.deleted_keys:
        logger.warning(json.dumps({"event": "limit_enforced", **entry}))
    else:
        logger.info(json.dumps({"event": "total_updated", **entry}))


def connect(config: Config) -> pg8000.native.Connection:
    ssl_context = None
    if config.ssl_mode == "verify-full":
        # Encrypt AND verify that this really is the RDS server (CA + host name).
        ssl_context = ssl.create_default_context(cafile=config.ssl_root_cert)
    conn = pg8000.native.Connection(
        user=config.db_user,
        password=config.db_password,
        host=config.db_host,
        port=config.db_port,
        database=config.db_name,
        ssl_context=ssl_context,
        timeout=10,
    )
    # Never wait forever for the API's lock; Lambda retries a failed invocation.
    # set_config() instead of "SET lock_timeout = ..." because SET cannot take
    # a bound parameter - and SQL is never built with string formatting.
    conn.run("SELECT set_config('lock_timeout', :value, false)", value=config.lock_timeout)
    return conn


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    config = Config.from_env()
    s3 = boto3.client("s3")
    conn = connect(config)
    try:
        results = process_event(event, s3, conn, config.limit_bytes)
    finally:
        conn.close()
    return {
        "projects": [
            {"project_id": r.project_id, "total_bytes": r.total_bytes, "deleted": r.deleted_keys}
            for r in results
        ]
    }
