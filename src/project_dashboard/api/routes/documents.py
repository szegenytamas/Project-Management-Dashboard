"""Documents: list, upload, download, replace and delete.

Access uses the same permission matrix as the projects:
  list / download            owner, participant
  upload / replace           owner, participant
  delete                     owner only
Non-members get 404 for the project and for each of its documents.
"""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from project_dashboard.api.deps import (
    CurrentUser,
    require_document_access,
    require_project_access,
)
from project_dashboard.core.config import Settings, get_settings
from project_dashboard.db.session import SessionDep
from project_dashboard.schemas.projects import DocumentRead
from project_dashboard.services import documents
from project_dashboard.services.errors import TooManyFilesError
from project_dashboard.services.files import read_and_validate
from project_dashboard.services.permissions import Action, DocumentAccess, ProjectAccess
from project_dashboard.storage import StorageDep

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing, invalid or expired token"},
    status.HTTP_404_NOT_FOUND: {"description": "Not found, or you are not a member"},
}
_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_413_CONTENT_TOO_LARGE: {"description": "File or project size limit exceeded"},
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"description": "Only PDF and DOCX are accepted"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Document storage unavailable"},
}

router = APIRouter(tags=["documents"], responses=_ERRORS)
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/project/{project_id}/documents", response_model=list[DocumentRead])
async def list_documents(
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.VIEW))],
    session: SessionDep,
) -> list[DocumentRead]:
    docs = await documents.list_documents(session, access.project.id)
    return [DocumentRead.model_validate(d) for d in docs]


@router.post(
    "/project/{project_id}/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=list[DocumentRead],
    responses=_UPLOAD_ERRORS,
)
async def upload_documents(
    files: Annotated[list[UploadFile], File(description="One or more PDF / DOCX files")],
    access: Annotated[ProjectAccess, Depends(require_project_access(Action.UPLOAD_DOCUMENT))],
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> list[DocumentRead]:
    """Upload documents (owner or participant). All files succeed or none is kept."""
    if len(files) > settings.max_files_per_upload:
        raise TooManyFilesError(f"At most {settings.max_files_per_upload} files per upload")
    # Validate EVERY file before storing any: one bad file rejects the whole request.
    validated = [await read_and_validate(f, settings.max_file_size_bytes) for f in files]
    docs = await documents.upload_documents(
        session, storage, access.project.id, user, validated, settings.project_size_limit_bytes
    )
    return [DocumentRead.model_validate(d) for d in docs]


@router.get(
    "/document/{document_id}",
    response_class=StreamingResponse,
    responses={status.HTTP_200_OK: {"content": {"application/pdf": {}}, "description": "The file"}},
)
async def download_document(
    access: Annotated[DocumentAccess, Depends(require_document_access(Action.VIEW))],
    storage: StorageDep,
) -> StreamingResponse:
    """Download the file - the only non-JSON response of the API."""
    document = access.document
    stored = await storage.get(document.storage_key)
    return StreamingResponse(
        stored.chunks,
        media_type=document.content_type,
        headers={
            "Content-Disposition": content_disposition(document.filename),
            "Content-Length": str(stored.size),
        },
    )


@router.put("/document/{document_id}", response_model=DocumentRead, responses=_UPLOAD_ERRORS)
async def replace_document(
    file: Annotated[UploadFile, File(description="The new version (PDF / DOCX)")],
    access: Annotated[DocumentAccess, Depends(require_document_access(Action.UPLOAD_DOCUMENT))],
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> DocumentRead:
    """Replace a document's file with a new version (owner or participant)."""
    validated = await read_and_validate(file, settings.max_file_size_bytes)
    document = await documents.replace_document(
        session, storage, access.document, user, validated, settings.project_size_limit_bytes
    )
    return DocumentRead.model_validate(document)


@router.delete("/document/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    access: Annotated[DocumentAccess, Depends(require_document_access(Action.DELETE_DOCUMENT))],
    session: SessionDep,
    storage: StorageDep,
) -> Response:
    """Delete a document and its file in S3 (owner only)."""
    await documents.delete_document(session, storage, access.document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def content_disposition(filename: str) -> str:
    """`attachment` header that works for any file name (RFC 6266 / RFC 5987).

    `filename=` gets an ASCII-only fallback with quotes removed; `filename*=`
    carries the real UTF-8 name, e.g. 'Árvíztűrő.pdf', percent-encoded.
    """
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
