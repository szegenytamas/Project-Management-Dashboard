"""Regression test: the Lambda's database connection over TLS.

RDS requires TLS. pg8000 only runs its channel-binding code (which depends on
the scramp library) on encrypted connections - and the regular test database
does not use TLS. So a broken pg8000 / scramp combination passed every test and
failed only in AWS ("module 'scramp' has no attribute 'make_channel_binding'").

This test starts a TLS-enabled PostgreSQL in Docker and connects through the
Lambda's own connect(). Only the certificate check is relaxed, because the
server uses the image's self-signed test certificate instead of an RDS one.
"""

import shutil
import ssl
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pg8000.native
import pytest
from size_limit import Config, connect

PASSWORD = "tls-test-only"


@pytest.fixture(scope="module")
def tls_postgres_port() -> Iterator[int]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is needed for the TLS test")
    name = f"pd-tls-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            docker, "run", "-d", "--rm", "--name", name,
            "-e", f"POSTGRES_PASSWORD={PASSWORD}",
            "-p", "127.0.0.1::5432",  # any free port on the host
            "postgres:17",
            "-c", "ssl=on",
            "-c", "ssl_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem",
            "-c", "ssl_key_file=/etc/ssl/private/ssl-cert-snakeoil.key",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    try:
        mapping = subprocess.run(  # noqa: S603 - fixed arguments
            [docker, "port", name, "5432"], check=True, capture_output=True, text=True
        ).stdout
        port = int(mapping.splitlines()[0].rsplit(":", 1)[1])
        _wait_until_ready(port)
        yield port
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True, check=False)  # noqa: S603


def _wait_until_ready(port: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            pg8000.native.Connection(
                user="postgres", password=PASSWORD, host="127.0.0.1", port=port, timeout=3
            ).close()
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def test_lambda_connects_to_postgres_over_tls(
    tls_postgres_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_context = ssl.create_default_context

    def accept_self_signed(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        context = real_context()  # same TLS settings, minus the RDS-specific trust
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    monkeypatch.setattr(ssl, "create_default_context", accept_self_signed)  # undone after the test
    config = Config(
        limit_bytes=1,
        db_host="127.0.0.1",
        db_port=tls_postgres_port,
        db_name="postgres",
        db_user="postgres",
        db_password=PASSWORD,
        ssl_mode="verify-full",  # the production setting: connect() builds a TLS context
    )

    conn = connect(config)
    try:
        [[encrypted, protocol]] = conn.run(
            "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
        )
        [[lock_timeout]] = conn.run("SHOW lock_timeout")
    finally:
        conn.close()

    assert encrypted is True
    assert protocol.startswith("TLSv1.")
    assert lock_timeout == "20s"  # connect() configured the session as in production
