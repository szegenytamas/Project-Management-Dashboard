"""Object storage for document files (AWS S3 or an S3-compatible emulator)."""

from typing import Annotated

from fastapi import Depends, Request

from project_dashboard.storage.s3 import ObjectStorage, S3Storage, StorageError, StoredObject


def get_storage(request: Request) -> ObjectStorage:
    """FastAPI dependency: the storage client created once in lifespan."""
    storage: ObjectStorage = request.app.state.storage
    return storage


StorageDep = Annotated[ObjectStorage, Depends(get_storage)]

__all__ = ["ObjectStorage", "S3Storage", "StorageDep", "StorageError", "StoredObject", "get_storage"]
