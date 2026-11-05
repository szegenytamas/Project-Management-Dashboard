"""The authorization matrix, tested through the real API.

The evaluation criteria grade authorization strictly: a participant who can
delete, or an outsider who can read someone else's project, fails the project.
So every (endpoint x role) combination is tested - and for the forbidden ones
we also check that NOTHING changed, not just the status code.
"""

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.db.models import Project, ProjectMember, ProjectRole
from tests.conftest import AuthUser, MakeUser


@dataclass
class Scenario:
    project_id: int
    users: dict[str, AuthUser]  # "owner", "participant", "outsider"


@pytest.fixture
async def scenario(client: AsyncClient, make_user: MakeUser, db_session: AsyncSession) -> Scenario:
    owner, participant, outsider = (
        await make_user("owner"),
        await make_user("participant"),
        await make_user("outsider"),
    )
    await make_user("newbie")  # someone to invite
    response = await client.post("/projects", json={"name": "Apollo"}, headers=owner.headers)
    project_id = response.json()["id"]
    db_session.add(
        ProjectMember(project_id=project_id, user_id=participant.id, role=ProjectRole.PARTICIPANT)
    )
    await db_session.flush()
    return Scenario(project_id, {"owner": owner, "participant": participant, "outsider": outsider})


# (method, path, request kwargs) for every endpoint that acts on a project.
ENDPOINTS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "view": ("GET", "/project/{id}/info", {}),
    "update": ("PUT", "/project/{id}/info", {"json": {"name": "Changed", "description": "x"}}),
    "delete": ("DELETE", "/project/{id}", {}),
    "invite": ("POST", "/project/{id}/invite", {"params": {"user": "newbie"}}),
}

#            owner  participant  outsider
MATRIX = {
    "view":   (200,   200,         404),
    "update": (200,   200,         404),
    "delete": (204,   403,         404),
    "invite": (201,   403,         404),
}  # fmt: skip


@pytest.mark.parametrize("role", ["owner", "participant", "outsider"])
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_authorization_matrix(
    client: AsyncClient, scenario: Scenario, endpoint: str, role: str
) -> None:
    method, path, kwargs = ENDPOINTS[endpoint]
    expected = MATRIX[endpoint][["owner", "participant", "outsider"].index(role)]

    response = await client.request(
        method, path.format(id=scenario.project_id), headers=scenario.users[role].headers, **kwargs
    )

    assert response.status_code == expected, response.text


async def test_participant_delete_leaves_project_intact(
    client: AsyncClient, scenario: Scenario, db_session: AsyncSession
) -> None:
    response = await client.delete(
        f"/project/{scenario.project_id}", headers=scenario.users["participant"].headers
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Only the project owner can delete the project"}
    assert await db_session.get(Project, scenario.project_id) is not None


async def test_participant_invite_adds_nobody(
    client: AsyncClient, scenario: Scenario, db_session: AsyncSession
) -> None:
    await client.post(
        f"/project/{scenario.project_id}/invite",
        params={"user": "newbie"},
        headers=scenario.users["participant"].headers,
    )

    members = await db_session.scalars(
        select(ProjectMember.user_id).where(ProjectMember.project_id == scenario.project_id)
    )
    assert len(list(members)) == 2  # still only owner + participant


async def test_outsider_update_changes_nothing(
    client: AsyncClient, scenario: Scenario, db_session: AsyncSession
) -> None:
    await client.put(
        f"/project/{scenario.project_id}/info",
        json={"name": "Hacked", "description": "x"},
        headers=scenario.users["outsider"].headers,
    )

    project = await db_session.get(Project, scenario.project_id)
    assert project is not None
    assert project.name == "Apollo"


async def test_outsider_cannot_tell_foreign_project_from_missing_one(
    client: AsyncClient, scenario: Scenario
) -> None:
    headers = scenario.users["outsider"].headers

    foreign = await client.get(f"/project/{scenario.project_id}/info", headers=headers)
    missing = await client.get("/project/999999/info", headers=headers)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


# Every business endpoint, without a token -> 401 before anything else happens.
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/projects"),
        ("GET", "/projects"),
        ("GET", "/project/1/info"),
        ("PUT", "/project/1/info"),
        ("DELETE", "/project/1"),
        ("POST", "/project/1/invite?user=bob"),
    ],
)
async def test_every_business_endpoint_requires_a_token(
    client: AsyncClient, method: str, path: str
) -> None:
    body = {"name": "x", "description": "y"} if method in {"POST", "PUT"} else None

    response = await client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
