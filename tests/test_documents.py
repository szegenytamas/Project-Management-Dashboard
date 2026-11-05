"""API tests for documents: upload, list, download, replace, delete - with S3 (moto).

Every test checks both sides: the HTTP response AND the real state in
PostgreSQL and in the S3 bucket.
"""

from collections.abc import AsyncIterator, Iterable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.db.models import Document, Project
from project_dashboard.storage import S3Storage, StorageError, StoredObject, get_storage
from tests.conftest import TEST_MAX_FILE_SIZE, TEST_PROJECT_LIMIT, AuthUser, MakeUser, bucket_keys
from tests.sample_files import DOCX_TYPE, PDF_TYPE, make_docx, make_pdf, make_zip_without_word

FileTuple = tuple[str, bytes, str]


async def new_project(client: AsyncClient, owner: AuthUser) -> int:
    response = await client.post("/projects", json={"name": "Apollo"}, headers=owner.headers)
    project_id: int = response.json()["id"]
    return project_id


async def upload(
    client: AsyncClient, user: AuthUser, project_id: int, files: Iterable[FileTuple]
) -> Any:
    return await client.post(
        f"/project/{project_id}/documents",
        files=[("files", f) for f in files],
        headers=user.headers,
    )


async def total_size(db_session: AsyncSession, project_id: int) -> int:
    project = await db_session.get(Project, project_id, populate_existing=True)
    assert project is not None
    return project.total_size_bytes


@pytest.fixture
async def alice(make_user: MakeUser) -> AuthUser:
    return await make_user("alice")


# ------------------------------------------------------------------ upload --
async def test_upload_stores_file_in_s3_and_metadata_in_db(
    client: AsyncClient, alice: AuthUser, db_session: AsyncSession, storage: S3Storage
) -> None:
    project_id = await new_project(client, alice)
    pdf = make_pdf(1_500)

    response = await upload(client, alice, project_id, [("plan.pdf", pdf, PDF_TYPE)])

    assert response.status_code == 201
    [doc] = response.json()
    assert (doc["filename"], doc["content_type"], doc["size_bytes"]) == (
        "plan.pdf",
        PDF_TYPE,
        1_500,
    )
    assert doc["uploaded_by_id"] == alice.id
    row = await db_session.get(Document, doc["id"])
    assert row is not None
    assert row.storage_key.startswith(f"projects/{project_id}/")  # the layout the Lambda relies on
    assert row.storage_key.endswith(".pdf")
    assert bucket_keys(storage) == [row.storage_key]
    stored = storage._client.get_object(Bucket=storage.bucket, Key=row.storage_key)["Body"].read()
    assert stored == pdf
    assert await total_size(db_session, project_id) == 1_500


async def test_upload_several_files_at_once(
    client: AsyncClient, alice: AuthUser, db_session: AsyncSession, storage: S3Storage
) -> None:
    project_id = await new_project(client, alice)
    docx = make_docx()

    response = await upload(
        client,
        alice,
        project_id,
        [("a.pdf", make_pdf(1_000), PDF_TYPE), ("b.docx", docx, DOCX_TYPE)],
    )

    assert response.status_code == 201
    assert [d["content_type"] for d in response.json()] == [PDF_TYPE, DOCX_TYPE]
    assert len(bucket_keys(storage)) == 2
    assert await total_size(db_session, project_id) == 1_000 + len(docx)


async def test_type_is_detected_from_content_not_from_the_client(
    client: AsyncClient, alice: AuthUser
) -> None:
    project_id = await new_project(client, alice)

    # The client claims a nonsense type; the real PDF content decides.
    response = await upload(client, alice, project_id, [("x.pdf", make_pdf(), "text/plain")])

    assert response.status_code == 201
    assert response.json()[0]["content_type"] == PDF_TYPE


async def test_path_in_filename_is_stripped(client: AsyncClient, alice: AuthUser) -> None:
    project_id = await new_project(client, alice)

    response = await upload(
        client, alice, project_id, [("C:\\fakepath\\..\\secret\\report.pdf", make_pdf(), PDF_TYPE)]
    )

    assert response.json()[0]["filename"] == "report.pdf"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("notes.pdf", b"just some text, not a pdf"),  # wrong content, right extension
        ("report.docx", make_pdf()),  # PDF content with a .docx name
        ("archive.docx", make_zip_without_word()),  # a ZIP, but not a Word file
        ("virus.exe", make_pdf()),  # valid content, forbidden extension
        ("empty.pdf", b""),
        ("noextension", make_pdf()),
    ],
)
async def test_unsupported_files_are_rejected_with_415(
    client: AsyncClient,
    alice: AuthUser,
    storage: S3Storage,
    db_session: AsyncSession,
    filename: str,
    content: bytes,
) -> None:
    project_id = await new_project(client, alice)

    response = await upload(client, alice, project_id, [(filename, content, PDF_TYPE)])

    assert response.status_code == 415
    assert bucket_keys(storage) == []
    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0


async def test_one_bad_file_rejects_the_whole_upload(
    client: AsyncClient, alice: AuthUser, storage: S3Storage, db_session: AsyncSession
) -> None:
    project_id = await new_project(client, alice)

    response = await upload(
        client,
        alice,
        project_id,
        [("good.pdf", make_pdf(), PDF_TYPE), ("bad.pdf", b"not a pdf", PDF_TYPE)],
    )

    assert response.status_code == 415
    assert bucket_keys(storage) == []  # the good file was not stored either
    assert await total_size(db_session, project_id) == 0


async def test_file_larger_than_the_limit_is_413(
    client: AsyncClient, alice: AuthUser, storage: S3Storage
) -> None:
    project_id = await new_project(client, alice)

    response = await upload(
        client, alice, project_id, [("big.pdf", make_pdf(TEST_MAX_FILE_SIZE + 1), PDF_TYPE)]
    )

    assert response.status_code == 413
    assert bucket_keys(storage) == []


async def test_project_size_limit_is_enforced(
    client: AsyncClient, alice: AuthUser, storage: S3Storage, db_session: AsyncSession
) -> None:
    project_id = await new_project(client, alice)
    chunk = TEST_MAX_FILE_SIZE  # 20 000; the project limit is 50 000
    files = [(f"{i}.pdf", make_pdf(chunk), PDF_TYPE) for i in range(2)]
    assert (await upload(client, alice, project_id, files)).status_code == 201

    response = await upload(client, alice, project_id, [("third.pdf", make_pdf(chunk), PDF_TYPE)])

    assert response.status_code == 413
    assert "Project size limit exceeded" in response.json()["detail"]
    assert await total_size(db_session, project_id) == 2 * chunk  # unchanged
    assert len(bucket_keys(storage)) == 2
    assert 3 * chunk > TEST_PROJECT_LIMIT


async def test_too_many_files_in_one_request_is_413(client: AsyncClient, alice: AuthUser) -> None:
    project_id = await new_project(client, alice)

    response = await upload(
        client, alice, project_id, [(f"{i}.pdf", make_pdf(), PDF_TYPE) for i in range(4)]
    )

    assert response.status_code == 413


async def test_upload_without_files_is_422(client: AsyncClient, alice: AuthUser) -> None:
    project_id = await new_project(client, alice)

    response = await client.post(f"/project/{project_id}/documents", headers=alice.headers)

    assert response.status_code == 422


# ------------------------------------------------------- list and download --
async def test_list_documents_and_include_them_in_projects(
    client: AsyncClient, alice: AuthUser
) -> None:
    project_id = await new_project(client, alice)
    await upload(client, alice, project_id, [("a.pdf", make_pdf(), PDF_TYPE)])

    listed = await client.get(f"/project/{project_id}/documents", headers=alice.headers)
    projects = await client.get("/projects", headers=alice.headers)

    assert listed.status_code == 200
    assert [d["filename"] for d in listed.json()] == ["a.pdf"]
    assert projects.json()[0]["documents"] == listed.json()


async def test_download_returns_the_exact_bytes(client: AsyncClient, alice: AuthUser) -> None:
    project_id = await new_project(client, alice)
    pdf = make_pdf(3_000, marker="original")
    [doc] = (
        await upload(client, alice, project_id, [("Árvíztűrő terv.pdf", pdf, PDF_TYPE)])
    ).json()

    response = await client.get(f"/document/{doc['id']}", headers=alice.headers)

    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == PDF_TYPE
    assert response.headers["content-length"] == "3000"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''%C3%81rv%C3%ADzt%C5%B1r%C5%91%20terv.pdf" in disposition


async def test_unknown_document_is_404(client: AsyncClient, alice: AuthUser) -> None:
    response = await client.get("/document/999999", headers=alice.headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


# ----------------------------------------------------------------- replace --
async def test_replace_swaps_the_file_and_removes_the_old_object(
    client: AsyncClient, alice: AuthUser, storage: S3Storage, db_session: AsyncSession
) -> None:
    project_id = await new_project(client, alice)
    [doc] = (
        await upload(client, alice, project_id, [("v1.pdf", make_pdf(1_000), PDF_TYPE)])
    ).json()
    old_keys = bucket_keys(storage)
    docx = make_docx("version 2")

    response = await client.put(
        f"/document/{doc['id']}",
        files={"file": ("v2.docx", docx, DOCX_TYPE)},
        headers=alice.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["filename"], body["content_type"]) == (doc["id"], "v2.docx", DOCX_TYPE)
    assert body["size_bytes"] == len(docx)
    new_keys = bucket_keys(storage)
    assert len(new_keys) == 1
    assert new_keys != old_keys  # new object, old one deleted
    assert await total_size(db_session, project_id) == len(docx)
    download = await client.get(f"/document/{doc['id']}", headers=alice.headers)
    assert download.content == docx


async def test_replace_respects_the_project_limit(
    client: AsyncClient, alice: AuthUser, storage: S3Storage, db_session: AsyncSession
) -> None:
    project_id = await new_project(client, alice)
    big = TEST_MAX_FILE_SIZE  # 20 000
    files = [
        ("a.pdf", make_pdf(big), PDF_TYPE),
        ("b.pdf", make_pdf(big), PDF_TYPE),
        ("small.pdf", make_pdf(9_000), PDF_TYPE),
    ]
    small = (await upload(client, alice, project_id, files)).json()[2]
    keys_before = bucket_keys(storage)
    # 49 000 of 50 000 used. Replacing the 9 000 file with a 20 000 one would
    # make it 60 000: refused. (The old size is subtracted before checking.)

    response = await client.put(
        f"/document/{small['id']}",
        files={"file": ("bigger.pdf", make_pdf(big), PDF_TYPE)},
        headers=alice.headers,
    )

    assert response.status_code == 413
    assert await total_size(db_session, project_id) == 49_000  # unchanged
    assert bucket_keys(storage) == keys_before  # no new object left behind
    assert 2 * big + 9_000 <= TEST_PROJECT_LIMIT < 3 * big


# ------------------------------------------------------------------ delete --
async def test_owner_deletes_document_row_and_object(
    client: AsyncClient, alice: AuthUser, storage: S3Storage, db_session: AsyncSession
) -> None:
    project_id = await new_project(client, alice)
    [doc] = (await upload(client, alice, project_id, [("a.pdf", make_pdf(1_234), PDF_TYPE)])).json()

    response = await client.delete(f"/document/{doc['id']}", headers=alice.headers)

    assert response.status_code == 204
    assert await db_session.get(Document, doc["id"]) is None
    assert bucket_keys(storage) == []
    assert await total_size(db_session, project_id) == 0


async def test_deleting_a_project_deletes_its_files(
    client: AsyncClient, alice: AuthUser, storage: S3Storage
) -> None:
    project_id = await new_project(client, alice)
    other_id = await new_project(client, alice)
    await upload(
        client,
        alice,
        project_id,
        [("a.pdf", make_pdf(), PDF_TYPE), ("b.docx", make_docx(), DOCX_TYPE)],
    )
    await upload(client, alice, other_id, [("keep.pdf", make_pdf(), PDF_TYPE)])

    response = await client.delete(f"/project/{project_id}", headers=alice.headers)

    assert response.status_code == 204
    remaining = bucket_keys(storage)
    assert len(remaining) == 1
    assert remaining[0].startswith(f"projects/{other_id}/")  # other project untouched


# --------------------------------------------------------------- failures --
class BrokenStorage:
    """S3 is down: every call fails."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageError("connection refused")

    async def get(self, key: str) -> StoredObject:
        raise StorageError("connection refused")

    async def delete(self, keys: Iterable[str]) -> None:
        raise StorageError("connection refused")


@pytest.fixture
async def broken_storage(app: FastAPI) -> AsyncIterator[None]:
    app.dependency_overrides[get_storage] = BrokenStorage
    yield


async def test_storage_outage_returns_503_and_stores_nothing(
    client: AsyncClient, alice: AuthUser, db_session: AsyncSession, broken_storage: None
) -> None:
    project_id = await new_project(client, alice)

    response = await upload(client, alice, project_id, [("a.pdf", make_pdf(), PDF_TYPE)])

    assert response.status_code == 503
    assert response.json() == {"detail": "Document storage is temporarily unavailable"}
    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await total_size(db_session, project_id) == 0


async def test_database_failure_after_upload_removes_the_s3_object(
    client: AsyncClient,
    alice: AuthUser,
    storage: S3Storage,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compensation: S3 succeeded, the commit failed -> the object must not stay behind."""
    project_id = await new_project(client, alice)

    async def failing_commit() -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(db_session, "commit", failing_commit)
    with pytest.raises(RuntimeError, match="database went away"):
        await upload(client, alice, project_id, [("a.pdf", make_pdf(), PDF_TYPE)])

    assert bucket_keys(storage) == []
