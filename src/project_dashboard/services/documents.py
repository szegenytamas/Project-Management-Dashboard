"""Business logic for documents: metadata in PostgreSQL, bytes in S3.

Two systems cannot share one transaction, so the ORDER of steps decides what
a failure leaves behind:

- Upload / replace: S3 first, then the database. If the database step fails,
  the just-uploaded objects are deleted again ("compensation"). A crash in
  between leaves at worst an unreferenced object - never a document row that
  points to a missing file.
- Delete: database first, then S3. The user never sees a document whose file
  is gone; if the S3 delete fails, an orphan object remains and is logged.

The size limit is checked while the project row is locked (SELECT ... FOR
UPDATE): concurrent uploads to the same project wait for each other, so two
uploads cannot both pass the check and together exceed the limit.
"""

import logging
from collections.abc import Iterable
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.db.models import Document, Project, User
from project_dashboard.services.errors import ProjectNotFoundError, ProjectSizeLimitError
from project_dashboard.services.files import ValidatedFile
from project_dashboard.storage import ObjectStorage, StorageError

logger = logging.getLogger(__name__)


def make_storage_key(project_id: int, file: ValidatedFile) -> str:
    # projects/{id}/ prefix: the Lambda (Phase 7) derives the project from it and
    # sums all objects under it. A random name avoids collisions and any tricks
    # with user-supplied file names.
    return f"projects/{project_id}/{uuid4().hex}{file.extension}"


async def list_documents(session: AsyncSession, project_id: int) -> list[Document]:
    result = await session.scalars(
        select(Document).where(Document.project_id == project_id).order_by(Document.id)
    )
    return list(result)


async def upload_documents(
    session: AsyncSession,
    storage: ObjectStorage,
    project_id: int,
    uploader: User,
    files: list[ValidatedFile],
    limit: int,
) -> list[Document]:
    project = await _lock_project(session, project_id)
    new_total = project.total_size_bytes + sum(f.size for f in files)
    _check_limit(new_total, limit)

    uploaded: list[str] = []
    try:
        documents = []
        for file in files:
            key = make_storage_key(project_id, file)
            await storage.put(key, file.data, file.content_type)
            uploaded.append(key)
            documents.append(
                Document(
                    project_id=project_id,
                    filename=file.filename,
                    content_type=file.content_type,
                    size_bytes=file.size,
                    storage_key=key,
                    uploaded_by_id=uploader.id,
                )
            )
        session.add_all(documents)
        project.total_size_bytes = new_total
        await session.commit()
    except Exception:
        await session.rollback()
        await _delete_quietly(storage, uploaded)  # compensation: undo the S3 side
        raise
    return documents


async def replace_document(
    session: AsyncSession,
    storage: ObjectStorage,
    document: Document,
    uploader: User,
    file: ValidatedFile,
    limit: int,
) -> Document:
    project = await _lock_project(session, document.project_id)
    new_total = project.total_size_bytes - document.size_bytes + file.size
    _check_limit(new_total, limit)

    old_key = document.storage_key
    new_key = make_storage_key(document.project_id, file)
    await storage.put(new_key, file.data, file.content_type)
    try:
        document.filename = file.filename
        document.content_type = file.content_type
        document.size_bytes = file.size
        document.storage_key = new_key
        document.uploaded_by_id = uploader.id
        project.total_size_bytes = new_total
        await session.commit()
    except Exception:
        await session.rollback()
        await _delete_quietly(storage, [new_key])
        raise
    await session.refresh(document)  # updated_at is set by the database
    # Old file removed LAST: if anything above failed, the row still points
    # to the old, intact object.
    await _delete_quietly(storage, [old_key])
    return document


async def delete_document(
    session: AsyncSession, storage: ObjectStorage, document: Document
) -> None:
    project = await _lock_project(session, document.project_id)
    key = document.storage_key
    project.total_size_bytes -= document.size_bytes
    await session.execute(delete(Document).where(Document.id == document.id))
    await session.commit()
    await _delete_quietly(storage, [key])


async def delete_files(storage: ObjectStorage, keys: Iterable[str]) -> None:
    """Remove the files of a deleted project (after its rows are committed away)."""
    await _delete_quietly(storage, keys)


# ----------------------------------------------------------------- helpers --
async def _lock_project(session: AsyncSession, project_id: int) -> Project:
    # populate_existing: overwrite any copy of the project already in the
    # session with the freshly locked row, so total_size_bytes is current.
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:  # deleted between the access check and now
        raise ProjectNotFoundError()
    return project


def _check_limit(new_total: int, limit: int) -> None:
    if new_total > limit:
        raise ProjectSizeLimitError(
            f"Project size limit exceeded: {new_total} of {limit} bytes would be used"
        )


async def _delete_quietly(storage: ObjectStorage, keys: Iterable[str]) -> None:
    """Best-effort cleanup: a failure here must not hide the original outcome."""
    key_list = list(keys)
    if not key_list:
        return
    try:
        await storage.delete(key_list)
    except StorageError:
        logger.warning("Could not delete S3 objects, left as orphans: %s", key_list)
