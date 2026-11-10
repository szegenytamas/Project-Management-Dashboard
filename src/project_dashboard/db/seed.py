"""Fill the database with demo data: `python -m project_dashboard.db.seed`.

What it creates (the same cast as in the documentation):

    alice  owner of "Website relaunch" (Bob is a participant) and "Mobile app"
    bob    owner of "Research notes" (Alice is a participant)
    carol  registered, member of nothing - to show the 404 for outsiders

Every project gets small, valid PDF/DOCX documents stored in S3.

Design
------
- **Through the services, not raw INSERTs.** Users are registered, projects
  created, invitations sent and documents uploaded with the same functions the
  API uses. Passwords are therefore argon2-hashed, every project gets exactly
  one owner, files land in S3 under `projects/{id}/` and the stored totals are
  correct - the demo data cannot violate a rule the API enforces.
- **Idempotent.** Running it twice changes nothing: existing users are kept
  (their passwords are NOT reset) and a project is skipped if its owner
  already has one with that name.
- **Explicit password reset.** `--reset-passwords` sets the demo password on
  the three demo accounts if they already exist (e.g. created by hand earlier).
  It touches only alice, bob and carol, deletes nothing, and never runs unless
  asked for.
- **No known password in production.** Locally the demo password has a
  default; with ENVIRONMENT=production the password must come from the
  SEED_PASSWORD variable, so a public repository never knows it.
"""

import argparse
import asyncio
import io
import logging
import os
import sys
import zipfile
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.db.models import Project, ProjectMember, ProjectRole, User
from project_dashboard.db.session import create_engine, create_sessionmaker
from project_dashboard.schemas.auth import RegisterRequest
from project_dashboard.services import documents, projects, users
from project_dashboard.services.files import DOCX, PDF, ValidatedFile
from project_dashboard.storage import ObjectStorage, S3Storage

logger = logging.getLogger(__name__)

# Local demo only; the password policy applies to it like to any other password.
DEFAULT_PASSWORD = "Demo-Pass-2026!"  # noqa: S105 - documented local demo value

USERS = ("alice", "bob", "carol")


@dataclass(frozen=True)
class DemoProject:
    owner: str
    name: str
    description: str
    participants: tuple[str, ...]
    files: tuple[tuple[str, str], ...]  # (filename, text shown inside the document)


PROJECTS = (
    DemoProject(
        "alice", "Website relaunch", "New design and content for the company website.", ("bob",),
        (("brief.pdf", "Website relaunch - project brief"),
         ("plan.docx", "Milestones: design, content, launch")),
    ),
    DemoProject(
        "alice", "Mobile app", "First version of the customer app.", (),
        (("requirements.pdf", "Mobile app - requirements"),),
    ),
    DemoProject(
        "bob", "Research notes", "Interviews and findings.", ("alice",),
        (("interviews.docx", "Interview notes, round 1"),),
    ),
)  # fmt: skip


@dataclass
class SeedReport:
    users_created: list[str] = field(default_factory=list)
    users_existing: list[str] = field(default_factory=list)
    passwords_reset: list[str] = field(default_factory=list)
    projects_created: list[str] = field(default_factory=list)
    projects_existing: list[str] = field(default_factory=list)


class SeedError(Exception):
    """The seed cannot run with the given configuration."""


def seed_password(settings: Settings) -> str:
    """The demo password: SEED_PASSWORD, or the local default outside production."""
    password = os.environ.get("SEED_PASSWORD")
    if password is None:
        if settings.environment == "production":
            raise SeedError("Set SEED_PASSWORD: production never uses the default demo password")
        password = DEFAULT_PASSWORD
    for login in USERS:  # the same rules as POST /auth, including "must not contain the login"
        try:
            RegisterRequest(login=login, password=password, repeat_password=password)
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
            raise SeedError(f"SEED_PASSWORD is not accepted for '{login}': {message}") from None
    return password


# ----------------------------------------------------------------- documents --
def make_pdf(text: str) -> bytes:
    """A minimal but valid one-page PDF showing `text`."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def make_docx(text: str) -> bytes:
    """A minimal but valid Word document showing `text`."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{escaped}</w:t></w:r></w:p></w:body></w:document>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def demo_file(filename: str, text: str) -> ValidatedFile:
    if filename.endswith(".pdf"):
        return ValidatedFile(filename=filename, content_type=PDF, data=make_pdf(text))
    return ValidatedFile(filename=filename, content_type=DOCX, data=make_docx(text))


# ---------------------------------------------------------------------- seed --
async def _get_or_create_user(
    session: AsyncSession, login: str, password: str, report: SeedReport
) -> User:
    user = await session.scalar(select(User).where(User.login == login))
    if user is not None:
        report.users_existing.append(login)
        return user
    try:
        user = await users.register_user(session, login, password)
    except users.LoginTakenError:  # created by someone else in the meantime
        report.users_existing.append(login)
        found = await session.scalar(select(User).where(User.login == login))
        if found is None:  # cannot happen: the unique violation proves the row exists
            raise RuntimeError(f"User '{login}' vanished during seeding") from None
        return found
    report.users_created.append(login)
    return user


async def _owned_project(session: AsyncSession, owner: User, name: str) -> Project | None:
    return await session.scalar(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == owner.id,
            ProjectMember.role == ProjectRole.OWNER,
            Project.name == name,
        )
    )


async def seed(
    session: AsyncSession,
    storage: ObjectStorage,
    settings: Settings,
    password: str,
    *,
    reset_passwords: bool = False,
) -> SeedReport:
    report = SeedReport()
    accounts = {login: await _get_or_create_user(session, login, password, report)
                for login in USERS}  # fmt: skip
    if reset_passwords:
        for login in report.users_existing:  # only the demo accounts that already existed
            await users.set_password(session, accounts[login], password)
            report.passwords_reset.append(login)

    for demo in PROJECTS:
        owner = accounts[demo.owner]
        label = f"{demo.name} ({demo.owner})"
        if await _owned_project(session, owner, demo.name) is not None:
            report.projects_existing.append(label)
            continue
        project = await projects.create_project(session, owner, demo.name, demo.description)
        for login in demo.participants:  # a brand-new project: nobody is a member yet
            await projects.invite_user(session, project, login)
        files = [demo_file(name, text) for name, text in demo.files]
        await documents.upload_documents(
            session, storage, project.id, owner, files, settings.project_size_limit_bytes
        )
        report.projects_created.append(label)
    return report


async def run(settings: Settings, *, reset_passwords: bool = False) -> SeedReport:
    password = seed_password(settings)
    engine = create_engine(settings)
    try:
        storage = S3Storage(settings)
        if settings.s3_create_bucket:  # local emulator only, as at API startup
            await storage.ensure_bucket()
        async with create_sessionmaker(engine)() as session:
            return await seed(session, storage, settings, password, reset_passwords=reset_passwords)
    finally:
        await engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add the demo users, projects and documents.")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="also set the demo password on alice, bob and carol if they already exist",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        report = asyncio.run(run(get_settings(), reset_passwords=args.reset_passwords))
    except SeedError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    logger.info("Users created: %s", ", ".join(report.users_created) or "-")
    unchanged = [u for u in report.users_existing if u not in report.passwords_reset]
    logger.info("Passwords reset: %s", ", ".join(report.passwords_reset) or "-")
    logger.info("Users already present (password unchanged): %s", ", ".join(unchanged) or "-")
    logger.info("Projects created: %s", "; ".join(report.projects_created) or "-")
    logger.info("Projects already present: %s", "; ".join(report.projects_existing) or "-")
    with_demo_password = report.users_created + report.passwords_reset
    if with_demo_password:
        logger.info("Log in with the demo password as: %s", ", ".join(with_demo_password))
    if unchanged:
        logger.info("%s kept their earlier password (--reset-passwords sets the demo one).",
                    ", ".join(unchanged))  # fmt: skip


if __name__ == "__main__":
    main()
