"""Tests for the ORM models and the database rules behind them.

Many rules are enforced by PostgreSQL itself (constraints, cascades). That is
the last line of defence: even buggy application code or a manual SQL session
cannot create an invalid state.
"""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from project_dashboard.db.models import Document, Project, ProjectMember, ProjectRole, User


async def make_user(session: AsyncSession, login: str = "alice") -> User:
    user = User(login=login, password_hash="not-a-real-hash")
    session.add(user)
    await session.flush()  # send INSERT now, so user.id is assigned
    return user


async def make_project(session: AsyncSession, owner: User, name: str = "Apollo") -> Project:
    project = Project(name=name)
    project.members.append(ProjectMember(user=owner, role=ProjectRole.OWNER))
    session.add(project)
    await session.flush()
    return project


async def test_server_defaults_are_filled_by_postgres(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    project = await make_project(db_session, owner)
    await db_session.refresh(project)

    assert project.id is not None
    assert project.description == ""
    assert project.total_size_bytes == 0
    assert project.created_at is not None
    assert project.created_at.tzinfo is not None  # TIMESTAMPTZ -> timezone-aware


async def test_project_with_members_and_documents_loads_eagerly(
    db_session: AsyncSession,
) -> None:
    owner = await make_user(db_session, "owner")
    guest = await make_user(db_session, "guest")
    project = await make_project(db_session, owner)
    db_session.add_all(
        [
            ProjectMember(project=project, user=guest, role=ProjectRole.PARTICIPANT),
            Document(
                project=project,
                filename="spec.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                storage_key=f"projects/{project.id}/spec.pdf",
                uploaded_by_id=owner.id,
            ),
        ]
    )
    await db_session.flush()
    db_session.expunge_all()  # forget everything; the next query starts fresh

    # selectinload = 1 query for projects + 1 per relationship, however many
    # projects there are. This is the fix for the "N+1 queries" problem.
    loaded = await db_session.scalar(
        select(Project)
        .where(Project.id == project.id)
        .options(selectinload(Project.members), selectinload(Project.documents))
    )

    assert loaded is not None
    assert {(m.user_id, m.role) for m in loaded.members} == {
        (owner.id, ProjectRole.OWNER),
        (guest.id, ProjectRole.PARTICIPANT),
    }
    assert [d.filename for d in loaded.documents] == ["spec.pdf"]


async def test_relationships_must_be_loaded_explicitly(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    project = await make_project(db_session, owner)
    db_session.expunge_all()

    loaded = await db_session.get(Project, project.id)

    assert loaded is not None
    with pytest.raises(InvalidRequestError, match="lazy='raise'"):
        _ = loaded.members


async def test_login_must_be_unique(db_session: AsyncSession) -> None:
    await make_user(db_session, "alice")

    with pytest.raises(IntegrityError, match="uq_users_login"):
        await make_user(db_session, "alice")


async def test_login_shorter_than_three_characters_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_users_login_min_length"):
        await make_user(db_session, "al")


async def test_project_name_cannot_be_blank(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)

    with pytest.raises(IntegrityError, match="ck_projects_name_not_blank"):
        await make_project(db_session, owner, name="   ")


async def test_a_project_can_have_only_one_owner(db_session: AsyncSession) -> None:
    owner = await make_user(db_session, "owner")
    other = await make_user(db_session, "other")
    project = await make_project(db_session, owner)

    db_session.add(ProjectMember(project=project, user=other, role=ProjectRole.OWNER))
    with pytest.raises(IntegrityError, match="uq_project_members_one_owner"):
        await db_session.flush()


async def test_a_user_can_join_a_project_only_once(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    project = await make_project(db_session, owner)

    # Plain SQL, because the ORM would refuse to track two objects with the
    # same primary key before even reaching the database.
    with pytest.raises(IntegrityError, match="pk_project_members"):
        await db_session.execute(
            text("INSERT INTO project_members (project_id, user_id, role) VALUES (:p, :u, :r)"),
            {"p": project.id, "u": owner.id, "r": "participant"},
        )


async def test_unknown_role_is_rejected_by_the_database(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    guest = await make_user(db_session, "guest")
    project = await make_project(db_session, owner)

    with pytest.raises(IntegrityError, match="ck_project_members_project_role"):
        await db_session.execute(
            text("INSERT INTO project_members (project_id, user_id, role) VALUES (:p, :u, :r)"),
            {"p": project.id, "u": guest.id, "r": "admin"},
        )


async def test_deleting_a_project_cascades_to_members_and_documents(
    db_session: AsyncSession,
) -> None:
    owner = await make_user(db_session)
    project = await make_project(db_session, owner)
    db_session.add(
        Document(
            project_id=project.id,
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=1,
            storage_key="projects/a.pdf",
        )
    )
    await db_session.flush()

    await db_session.delete(project)
    await db_session.flush()

    members = await db_session.scalar(select(func.count()).select_from(ProjectMember))
    documents = await db_session.scalar(select(func.count()).select_from(Document))
    users = await db_session.scalar(select(func.count()).select_from(User))
    assert (members, documents, users) == (0, 0, 1)  # the user account stays
