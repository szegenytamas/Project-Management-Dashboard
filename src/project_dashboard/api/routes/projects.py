"""Projects and invitations.

Every endpoint here is protected. Endpoints on an existing project declare
`require_project_access(<action>)`, which runs: token -> membership (404) ->
role allows action (403). The endpoint body only runs when all checks pass.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import AfterValidator

from project_dashboard.api.deps import CurrentUser, require_project_access
from project_dashboard.db.models import ProjectRole
from project_dashboard.db.session import SessionDep
from project_dashboard.schemas.projects import (
    MemberRead,
    ProjectCreate,
    ProjectInfo,
    ProjectRead,
    ProjectUpdate,
)
from project_dashboard.services import documents, projects
from project_dashboard.services.permissions import Action, ProjectAccess
from project_dashboard.storage import StorageDep

_401: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing, invalid or expired token"}
}
_404: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "Project not found, or you are not a member"}
}
_403: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {"description": "Your role does not allow this action"}
}

router = APIRouter(tags=["projects"], responses=_401)

InviteLogin = Annotated[
    str,
    Query(
        alias="user",
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="Login of the user to invite",
    ),
    AfterValidator(str.lower),
]


@router.post("/projects", status_code=status.HTTP_201_CREATED, response_model=ProjectInfo)
async def create_project(
    body: ProjectCreate, user: CurrentUser, session: SessionDep
) -> ProjectInfo:
    """Create a project. The creator automatically becomes its owner."""
    project = await projects.create_project(session, user, body.name, body.description)
    return ProjectInfo.build(project, ProjectRole.OWNER)


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(user: CurrentUser, session: SessionDep) -> list[ProjectRead]:
    """All projects you own or participate in, with their documents."""
    rows = await projects.list_projects(session, user.id)
    return [ProjectRead.build(project, role) for project, role in rows]


@router.get("/project/{project_id}/info", response_model=ProjectInfo, responses=_404)
async def get_project_info(
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.VIEW))],
) -> ProjectInfo:
    return ProjectInfo.build(access.project, access.role)


@router.put("/project/{project_id}/info", response_model=ProjectInfo, responses=_404)
async def update_project_info(
    body: ProjectUpdate,
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.UPDATE))],
    session: SessionDep,
) -> ProjectInfo:
    """Replace name and description (owner or participant)."""
    project = await projects.update_project(session, access.project, body.name, body.description)
    return ProjectInfo.build(project, access.role)


@router.delete(
    "/project/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_404, **_403},
)
async def delete_project(
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.DELETE))],
    session: SessionDep,
    storage: StorageDep,
) -> Response:
    """Delete the project with its memberships, documents and their files in S3 (owner only)."""
    keys = await projects.delete_project(session, access.project)
    # Rows are committed away first; only then the files (see services/documents.py).
    await documents.delete_files(storage, keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/project/{project_id}/invite",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberRead,
    responses={
        **_404,
        **_403,
        status.HTTP_409_CONFLICT: {"description": "The user is already a member"},
    },
)
async def invite_user(
    login: InviteLogin,
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.INVITE))],
    session: SessionDep,
) -> MemberRead:
    """Give another registered user participant access (owner only)."""
    invitation = await projects.invite_user(session, access.project, login)
    return MemberRead(
        project_id=invitation.project_id,
        user_id=invitation.user.id,
        login=invitation.user.login,
        role=invitation.role,
        created_at=invitation.created_at,
    )
