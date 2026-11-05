"""Business logic for projects and memberships.

Permission checks are NOT done here - the route has already obtained a
`ProjectAccess` from services/permissions.py before calling these functions.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from project_dashboard.db.models import Document, Project, ProjectMember, ProjectRole, User
from project_dashboard.services.errors import AlreadyMemberError, UserNotFoundError


async def create_project(
    session: AsyncSession, owner: User, name: str, description: str
) -> Project:
    project = Project(name=name, description=description)
    # Project and owner membership are committed TOGETHER: if either insert
    # fails, both are rolled back, so a project without an owner cannot exist.
    project.members.append(ProjectMember(user_id=owner.id, role=ProjectRole.OWNER))
    session.add(project)
    await session.commit()
    return project


async def list_projects(session: AsyncSession, user_id: int) -> list[tuple[Project, ProjectRole]]:
    """All projects the user is a member of, each with documents and the user's role.

    selectinload runs ONE extra query for the documents of all these projects
    together (WHERE project_id IN (...)). Total: 2 queries, however many
    projects - instead of 1 + N (the N+1 problem).
    """
    result = await session.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id)
        .options(selectinload(Project.documents))
        .order_by(Project.id)
    )
    return [(project, role) for project, role in result]


async def update_project(
    session: AsyncSession, project: Project, name: str, description: str
) -> Project:
    project.name = name
    project.description = description
    await session.commit()
    # updated_at is set by the database (onupdate=now()), so its new value is
    # only in PostgreSQL. In async code we must reload it explicitly - an
    # implicit lazy load on attribute access is not possible.
    await session.refresh(project)
    return project


async def delete_project(session: AsyncSession, project: Project) -> list[str]:
    """Delete the project; return the S3 keys of its documents for cleanup.

    One DELETE statement: PostgreSQL's ON DELETE CASCADE removes the
    memberships and document rows. The keys are read first because the rows
    are gone afterwards; the route deletes the S3 objects after this commit.
    """
    keys = list(
        await session.scalars(select(Document.storage_key).where(Document.project_id == project.id))
    )
    await session.execute(delete(Project).where(Project.id == project.id))
    await session.commit()
    return keys


@dataclass(frozen=True)
class Invitation:
    project_id: int
    user: User
    role: ProjectRole
    created_at: datetime


async def invite_user(session: AsyncSession, project: Project, login: str) -> Invitation:
    user = await session.scalar(select(User).where(User.login == login))
    if user is None:
        raise UserNotFoundError(login)
    # A plain INSERT ... RETURNING instead of session.add(ProjectMember(...)):
    # the session may already hold a membership object with the same key (the
    # owner's own), and the ORM would warn about two objects for one row. The
    # database's primary key (project_id, user_id) is the judge either way -
    # no race between two simultaneous invites, and the owner inviting
    # themselves is caught too.
    try:
        created_at = await session.scalar(
            insert(ProjectMember)
            .values(project_id=project.id, user_id=user.id, role=ProjectRole.PARTICIPANT)
            .returning(ProjectMember.created_at)
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "pk_project_members" in str(exc.orig):
            raise AlreadyMemberError(login) from exc
        raise
    if created_at is None:  # cannot happen: RETURNING always yields the inserted row
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return Invitation(project.id, user, ProjectRole.PARTICIPANT, created_at)
