"""Request and response models for projects, documents and memberships."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from project_dashboard.db.models import Project, ProjectRole

# strip_whitespace: "  Apollo  " is stored as "Apollo", and "   " becomes ""
# which then fails min_length - the same rule as the database CHECK constraint.
ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class ProjectCreate(BaseModel):
    # extra="forbid": an unknown field (e.g. a typo "nmae") is a 422 instead of
    # being silently ignored - and nobody can sneak in fields like "owner_id".
    model_config = ConfigDict(extra="forbid")

    name: ProjectName
    description: Description = ""


class ProjectUpdate(BaseModel):
    """PUT replaces the details, so both fields are required."""

    model_config = ConfigDict(extra="forbid")

    name: ProjectName
    description: Description


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by_id: int | None
    created_at: datetime
    updated_at: datetime


class ProjectInfo(BaseModel):
    """Project details plus the caller's own role in it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    total_size_bytes: int
    created_at: datetime
    updated_at: datetime
    role: ProjectRole

    @classmethod
    def build(cls, project: Project, role: ProjectRole) -> "ProjectInfo":
        return cls.model_validate({**_columns(project), "role": role})


class ProjectRead(ProjectInfo):
    """Full info for GET /projects: details + documents."""

    documents: list[DocumentRead]

    @classmethod
    def build(cls, project: Project, role: ProjectRole) -> "ProjectRead":
        documents = [DocumentRead.model_validate(d) for d in project.documents]
        return cls.model_validate({**_columns(project), "role": role, "documents": documents})


class MemberRead(BaseModel):
    project_id: int
    user_id: int
    login: str
    role: ProjectRole
    created_at: datetime


def _columns(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "total_size_bytes": project.total_size_bytes,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
