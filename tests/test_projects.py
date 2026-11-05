"""API tests for the project endpoints: create, list, info, update, delete, invite."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from project_dashboard.db.models import Document, Project, ProjectMember, ProjectRole
from tests.conftest import AuthUser, MakeUser


async def create(
    client: AsyncClient, owner: AuthUser, name: str = "Apollo", **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        "/projects",
        json={"name": name, "description": "Moon landing", **extra},
        headers=owner.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------ create --
async def test_create_makes_the_caller_owner(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    alice = await make_user("alice")

    body = await create(client, alice)

    assert body["name"] == "Apollo"
    assert body["role"] == "owner"
    assert body["total_size_bytes"] == 0
    member = await db_session.get(ProjectMember, (body["id"], alice.id))
    assert member is not None
    assert member.role == ProjectRole.OWNER


async def test_create_strips_whitespace_and_defaults_description(
    client: AsyncClient, make_user: MakeUser
) -> None:
    alice = await make_user("alice")

    response = await client.post("/projects", json={"name": "  Apollo  "}, headers=alice.headers)

    assert response.status_code == 201
    assert response.json()["name"] == "Apollo"
    assert response.json()["description"] == ""


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "    "},  # blank after stripping
        {"name": "x" * 101},
        {"description": "no name"},
        {"name": "Apollo", "owner_id": 1},  # unknown field is rejected, not ignored
    ],
)
async def test_create_rejects_invalid_body(
    client: AsyncClient, make_user: MakeUser, body: dict[str, Any]
) -> None:
    alice = await make_user("alice")

    response = await client.post("/projects", json=body, headers=alice.headers)

    assert response.status_code == 422


# -------------------------------------------------------------------- list --
async def test_list_shows_only_my_projects_with_role_and_documents(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    alice, bob = await make_user("alice"), await make_user("bob")
    mine = await create(client, alice, "Mine")
    shared = await create(client, bob, "Shared")
    await create(client, bob, "Bob only")
    db_session.add(
        ProjectMember(project_id=shared["id"], user_id=alice.id, role=ProjectRole.PARTICIPANT)
    )
    db_session.add(
        Document(
            project_id=mine["id"],
            filename="plan.pdf",
            content_type="application/pdf",
            size_bytes=10,
            storage_key="k/plan.pdf",
            uploaded_by_id=alice.id,
        )
    )
    await db_session.flush()

    response = await client.get("/projects", headers=alice.headers)

    assert response.status_code == 200
    projects = {p["name"]: p for p in response.json()}
    assert set(projects) == {"Mine", "Shared"}  # never "Bob only"
    assert projects["Mine"]["role"] == "owner"
    assert projects["Shared"]["role"] == "participant"
    assert [d["filename"] for d in projects["Mine"]["documents"]] == ["plan.pdf"]
    assert projects["Shared"]["documents"] == []


async def test_list_is_empty_for_a_new_user(client: AsyncClient, make_user: MakeUser) -> None:
    alice = await make_user("alice")

    response = await client.get("/projects", headers=alice.headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_list_uses_a_constant_number_of_queries(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, engine: AsyncEngine
) -> None:
    """Guard against the N+1 problem: 1 project or 5 projects, same query count."""
    alice = await make_user("alice")

    async def count_queries() -> int:
        statements: list[str] = []

        def record(*args: Any) -> None:
            statements.append(args[2])  # the SQL text

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            assert (await client.get("/projects", headers=alice.headers)).status_code == 200
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
        return len(statements)

    async def add_project(name: str) -> None:
        project = await create(client, alice, name)
        db_session.add(
            Document(
                project_id=project["id"],
                filename=f"{name}.pdf",
                content_type="application/pdf",
                size_bytes=1,
                storage_key=f"k/{name}",
            )
        )
        await db_session.flush()

    await add_project("p1")
    with_one = await count_queries()
    for i in range(2, 6):
        await add_project(f"p{i}")
    with_five = await count_queries()

    assert with_one == with_five
    assert with_five <= 3  # load user + projects + all their documents


# ------------------------------------------------------------- info/update --
async def test_get_info(client: AsyncClient, make_user: MakeUser) -> None:
    alice = await make_user("alice")
    project = await create(client, alice)

    response = await client.get(f"/project/{project['id']}/info", headers=alice.headers)

    assert response.status_code == 200
    assert response.json() == project


async def test_put_replaces_details_and_returns_them(
    client: AsyncClient, make_user: MakeUser
) -> None:
    alice = await make_user("alice")
    project = await create(client, alice)

    response = await client.put(
        f"/project/{project['id']}/info",
        json={"name": "Artemis", "description": "Back to the Moon"},
        headers=alice.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["description"]) == ("Artemis", "Back to the Moon")
    assert body["updated_at"] >= project["updated_at"]
    again = await client.get(f"/project/{project['id']}/info", headers=alice.headers)
    assert again.json()["name"] == "Artemis"


async def test_put_requires_both_fields(client: AsyncClient, make_user: MakeUser) -> None:
    alice = await make_user("alice")
    project = await create(client, alice)

    response = await client.put(
        f"/project/{project['id']}/info", json={"name": "Artemis"}, headers=alice.headers
    )

    assert response.status_code == 422


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc", str(2**63)])
async def test_invalid_project_id_is_422_not_500(
    client: AsyncClient, make_user: MakeUser, bad_id: str
) -> None:
    alice = await make_user("alice")

    response = await client.get(f"/project/{bad_id}/info", headers=alice.headers)

    assert response.status_code == 422


async def test_unknown_project_is_404(client: AsyncClient, make_user: MakeUser) -> None:
    alice = await make_user("alice")

    response = await client.get("/project/999999/info", headers=alice.headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


# ------------------------------------------------------------------ delete --
async def test_owner_deletes_project_with_members_and_documents(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    alice, bob = await make_user("alice"), await make_user("bob")
    project = await create(client, alice)
    await client.post(
        f"/project/{project['id']}/invite", params={"user": "bob"}, headers=alice.headers
    )
    db_session.add(
        Document(
            project_id=project["id"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=1,
            storage_key="k/a",
        )
    )
    await db_session.flush()

    response = await client.delete(f"/project/{project['id']}", headers=alice.headers)

    assert response.status_code == 204
    assert response.content == b""
    assert await db_session.get(Project, project["id"]) is None
    remaining = await db_session.scalar(
        select(func.count())
        .select_from(ProjectMember)
        .where(ProjectMember.project_id == project["id"])
    )
    assert remaining == 0
    assert (
        await client.get(f"/project/{project['id']}/info", headers=bob.headers)
    ).status_code == 404


# ------------------------------------------------------------------ invite --
async def test_invited_user_becomes_participant_immediately(
    client: AsyncClient, make_user: MakeUser
) -> None:
    alice, bob = await make_user("alice"), await make_user("bob")
    project = await create(client, alice)
    # bob's token was issued BEFORE the invite - it must still work afterwards.

    response = await client.post(
        f"/project/{project['id']}/invite", params={"user": "BOB"}, headers=alice.headers
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["login"], body["role"], body["user_id"]) == ("bob", "participant", bob.id)
    info = await client.get(f"/project/{project['id']}/info", headers=bob.headers)
    assert info.status_code == 200
    assert info.json()["role"] == "participant"


async def test_invite_unknown_user_is_404(client: AsyncClient, make_user: MakeUser) -> None:
    alice = await make_user("alice")
    project = await create(client, alice)

    response = await client.post(
        f"/project/{project['id']}/invite", params={"user": "nobody"}, headers=alice.headers
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "User 'nobody' not found"}


@pytest.mark.parametrize("login", ["bob", "alice"])  # twice, and the owner herself
async def test_invite_existing_member_is_409(
    client: AsyncClient, make_user: MakeUser, login: str
) -> None:
    alice = await make_user("alice")
    await make_user("bob")
    project = await create(client, alice)
    url = f"/project/{project['id']}/invite"
    if login == "bob":
        await client.post(url, params={"user": "bob"}, headers=alice.headers)

    response = await client.post(url, params={"user": login}, headers=alice.headers)

    assert response.status_code == 409


@pytest.mark.parametrize("params", [{}, {"user": "a"}, {"user": "bad login!"}])
async def test_invite_requires_a_valid_login_parameter(
    client: AsyncClient, make_user: MakeUser, params: dict[str, str]
) -> None:
    alice = await make_user("alice")
    project = await create(client, alice)

    response = await client.post(
        f"/project/{project['id']}/invite", params=params, headers=alice.headers
    )

    assert response.status_code == 422
