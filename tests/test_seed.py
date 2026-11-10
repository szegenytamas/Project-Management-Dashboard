"""The demo-data seed: correct data, idempotent, and safe in production."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.core.config import Settings
from project_dashboard.db import seed as seed_module
from project_dashboard.db.models import Document, Project, ProjectMember, ProjectRole, User
from project_dashboard.db.seed import (
    DEFAULT_PASSWORD,
    SeedError,
    make_docx,
    make_pdf,
    seed,
    seed_password,
)
from project_dashboard.services.files import DOCX, PDF, detect_type
from project_dashboard.storage import S3Storage
from tests.conftest import bucket_keys


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_seed_creates_the_demo_world(
    db_session: AsyncSession, storage: S3Storage, settings: Settings
) -> None:
    report = await seed(db_session, storage, settings, DEFAULT_PASSWORD)

    assert sorted(report.users_created) == ["alice", "bob", "carol"]
    assert len(report.projects_created) == 3

    roles = {
        (name, login, role)
        for name, login, role in await db_session.execute(
            select(Project.name, User.login, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .join(User, User.id == ProjectMember.user_id)
        )
    }
    assert roles == {
        ("Website relaunch", "alice", ProjectRole.OWNER),
        ("Website relaunch", "bob", ProjectRole.PARTICIPANT),
        ("Mobile app", "alice", ProjectRole.OWNER),
        ("Research notes", "bob", ProjectRole.OWNER),
        ("Research notes", "alice", ProjectRole.PARTICIPANT),
    }  # carol is in no project: the outsider for the demo

    # Every document row has its object in S3, and every stored total is exact.
    docs = list(await db_session.scalars(select(Document)))
    assert len(docs) == 4
    assert sorted(d.storage_key for d in docs) == bucket_keys(storage)
    for project in await db_session.scalars(select(Project)):
        sizes = [d.size_bytes for d in docs if d.project_id == project.id]
        assert project.total_size_bytes == sum(sizes) > 0


async def test_seed_twice_changes_nothing(
    db_session: AsyncSession, storage: S3Storage, settings: Settings
) -> None:
    await seed(db_session, storage, settings, DEFAULT_PASSWORD)
    counts = [await count(db_session, m) for m in (User, Project, ProjectMember, Document)]

    report = await seed(db_session, storage, settings, "Other-Pass-2026!")

    assert report.users_created == [] and report.projects_created == []
    assert sorted(report.users_existing) == ["alice", "bob", "carol"]
    assert len(report.projects_existing) == 3
    assert [await count(db_session, m) for m in (User, Project, ProjectMember, Document)] == counts
    assert len(bucket_keys(storage)) == counts[3]


async def test_seeded_users_can_log_in(
    client: AsyncClient, db_session: AsyncSession, storage: S3Storage, settings: Settings
) -> None:
    await seed(db_session, storage, settings, DEFAULT_PASSWORD)

    response = await client.post("/login", json={"login": "bob", "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    token = response.json()["access_token"]
    projects = await client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert {(p["name"], p["role"]) for p in projects.json()} == {
        ("Website relaunch", "participant"),
        ("Research notes", "owner"),
    }


def test_demo_files_pass_the_upload_type_check() -> None:
    # The seed bypasses HTTP, so prove its files would also be accepted through the API.
    assert detect_type(make_pdf("Hello (world)")) == PDF
    assert detect_type(make_docx("Fish & <chips>")) == DOCX


def test_default_password_is_used_locally(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    assert seed_password(settings) == DEFAULT_PASSWORD


def test_production_requires_an_explicit_password(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = settings.model_copy(update={"environment": "production"})
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    with pytest.raises(SeedError, match="SEED_PASSWORD"):
        seed_password(production)

    monkeypatch.setenv("SEED_PASSWORD", "Prod-Demo-Pass-91!")
    assert seed_password(production) == "Prod-Demo-Pass-91!"


@pytest.mark.parametrize(
    ("password", "reason"),
    [("Short-Pas1!", "at least 12"), ("Bob-Password-42!", "must not contain the login")],
)
def test_weak_seed_password_is_rejected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, password: str, reason: str
) -> None:
    monkeypatch.setenv("SEED_PASSWORD", password)
    with pytest.raises(SeedError, match=reason):
        seed_password(settings)


def test_main_exits_with_an_error_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, settings: Settings
) -> None:
    monkeypatch.setattr(
        seed_module, "get_settings",
        lambda: settings.model_copy(update={"environment": "production"}),
    )  # fmt: skip
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        seed_module.main()
    assert exit_info.value.code == 1
    assert "SEED_PASSWORD" in caplog.text
