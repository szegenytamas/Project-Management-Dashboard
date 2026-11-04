"""ORM models: one class per table.

Normalization (3NF)
-------------------
Each fact is stored exactly once:
- a user's login lives only in `users`;
- "who may access which project, with which role" lives only in
  `project_members`. This association table resolves the many-to-many
  relationship between users and projects, and the owner is simply the member
  with role 'owner' - there is no separate owner_id column to keep in sync;
- every document belongs to exactly one project (`documents.project_id`).

Deliberate denormalization
--------------------------
`projects.total_size_bytes` could always be computed as
SUM(documents.size_bytes). We store it anyway, so the per-project size limit can
be checked with one cheap read instead of an aggregate query on every upload.
The price: it must be kept in sync (the S3-triggered Lambda recalculates it in
Phase 7), and it can be briefly stale.
"""

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from project_dashboard.db.base import Base, CreatedAtMixin, TimestampMixin


class ProjectRole(StrEnum):
    OWNER = "owner"
    PARTICIPANT = "participant"


class User(CreatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("char_length(login) >= 3", name="login_min_length"),)

    # GENERATED ALWAYS AS IDENTITY: PostgreSQL's modern auto-increment.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    # Only a slow, salted hash (argon2, Phase 3) is stored - never the password.
    password_hash: Mapped[str] = mapped_column(String(255))

    memberships: Mapped[list["ProjectMember"]] = relationship(
        back_populates="user", passive_deletes=True, lazy="raise"
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("total_size_bytes >= 0", name="total_size_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))

    # lazy="raise": touching project.members without loading it first raises an
    # error instead of silently running a query. In async code an implicit query
    # cannot run anyway, and it is how the "N+1 queries" problem sneaks in.
    # Load explicitly: select(Project).options(selectinload(Project.members)).
    #
    # passive_deletes=True: let PostgreSQL's ON DELETE CASCADE remove the
    # children, instead of the ORM loading and deleting them one by one.
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="Document.id",
    )


class ProjectMember(CreatedAtMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        # "My projects" looks members up by user_id. The primary key
        # (project_id, user_id) only helps lookups that start with project_id.
        Index("ix_project_members_user_id", "user_id"),
        # At most ONE owner per project: a partial unique index only covers
        # rows WHERE role = 'owner'. Participants are unlimited.
        Index(
            "uq_project_members_one_owner",
            "project_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    # Composite primary key: a user can be a member of a project only once.
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Stored as VARCHAR + CHECK constraint instead of a native PostgreSQL ENUM
    # type: adding a role later is a simple constraint change, while altering
    # a native ENUM inside a migration is awkward.
    role: Mapped[ProjectRole] = mapped_column(
        Enum(
            ProjectRole,
            name="project_role",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda roles: [role.value for role in roles],
        )
    )

    project: Mapped[Project] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="size_non_negative"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Where the file lives in S3 (the "object key"). The bytes are NOT in the DB.
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    # SET NULL: if the uploader's account is removed, the document stays.
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project: Mapped[Project] = relationship(back_populates="documents", lazy="raise")
