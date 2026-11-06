"""Settings that only matter in AWS, tested without AWS."""

import ssl
from pathlib import Path

from pydantic import SecretStr

from project_dashboard.core.config import Settings

BUNDLE = Path(__file__).parents[1] / "certs" / "rds-global-bundle.pem"


def make_settings(**overrides: object) -> Settings:
    return Settings(
        postgres_password=SecretStr("x"),
        jwt_secret_key=SecretStr("k" * 32),
        **overrides,  # type: ignore[arg-type]
    )


def test_local_database_connections_are_plain() -> None:
    assert make_settings(postgres_ssl_mode="disable").database_connect_args() == {"timeout": 5}


def test_rds_connections_verify_certificate_and_host_name() -> None:
    args = make_settings(
        postgres_ssl_mode="verify-full", postgres_ssl_root_cert=str(BUNDLE)
    ).database_connect_args()

    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED  # the chain must lead to an RDS CA
    assert context.check_hostname  # ... and the certificate must name the RDS endpoint


def test_rds_ca_bundle_is_in_the_repository() -> None:
    text = BUNDLE.read_text(encoding="ascii")
    assert text.count("BEGIN CERTIFICATE") > 50  # one CA per region and generation
