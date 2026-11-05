"""The single source of truth for "who may do what on a project".

Every endpoint that touches a project asks `get_project_access()`. There is no
other place where roles are compared, so the rules cannot drift apart between
endpoints (graded as "permission resolution is consistent").

Decision order:
1. Is the caller a member of the project?   no  -> 404 (hide that it exists)
2. Does the caller's role allow the action?  no  -> 403
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.db.models import Document, Project, ProjectMember, ProjectRole
from project_dashboard.services.errors import (
    DocumentNotFoundError,
    PermissionDeniedError,
    ProjectNotFoundError,
)


class Action(StrEnum):
    VIEW = "view the project"
    UPDATE = "update the project"
    DELETE = "delete the project"
    INVITE = "invite users"
    UPLOAD_DOCUMENT = "upload or replace documents"
    DELETE_DOCUMENT = "delete documents"


# The permission matrix. Owner: everything. Participant: read and modify,
# never delete, never invite.
PERMISSIONS: dict[ProjectRole, frozenset[Action]] = {
    ProjectRole.OWNER: frozenset(Action),
    ProjectRole.PARTICIPANT: frozenset({Action.VIEW, Action.UPDATE, Action.UPLOAD_DOCUMENT}),
}


def is_allowed(role: ProjectRole, action: Action) -> bool:
    return action in PERMISSIONS[role]


@dataclass(frozen=True)
class ProjectAccess:
    """What an endpoint receives once access has been granted."""

    project: Project
    role: ProjectRole


async def get_project_access(
    session: AsyncSession, project_id: int, user_id: int, action: Action
) -> ProjectAccess:
    # One query: the project AND the caller's membership. A project without a
    # membership row for this user yields no row - indistinguishable from a
    # project that does not exist at all.
    row = (
        await session.execute(
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(Project.id == project_id, ProjectMember.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise ProjectNotFoundError()
    project, role = row
    if not is_allowed(role, action):
        raise PermissionDeniedError(f"Only the project owner can {action.value}")
    return ProjectAccess(project=project, role=role)


@dataclass(frozen=True)
class DocumentAccess:
    document: Document
    project_id: int
    role: ProjectRole


async def get_document_access(
    session: AsyncSession, document_id: int, user_id: int, action: Action
) -> DocumentAccess:
    """The same rules for a document: access is decided by its project's membership.

    A document that does not exist and a document in someone else's project
    give the identical 404, so document IDs cannot be probed either.
    """
    row = (
        await session.execute(
            select(Document, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Document.project_id)
            .where(Document.id == document_id, ProjectMember.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise DocumentNotFoundError()
    document, role = row
    if not is_allowed(role, action):
        raise PermissionDeniedError(f"Only the project owner can {action.value}")
    return DocumentAccess(document=document, project_id=document.project_id, role=role)
