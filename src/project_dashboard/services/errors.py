"""Domain errors raised by the services.

Services describe WHAT went wrong in business terms; they know nothing about
HTTP. One central handler (core/errors.py) translates each family into a
status code, so every endpoint answers the same situation the same way.
"""


class DomainError(Exception):
    """Base class. `detail` is the message the client will see."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(DomainError):
    """-> 404. Also used when something exists but the caller may not see it."""


class PermissionDeniedError(DomainError):
    """-> 403. The caller can see the project, but their role forbids the action."""


class ConflictError(DomainError):
    """-> 409. The request clashes with the current state (e.g. a duplicate)."""


class PayloadTooLargeError(DomainError):
    """-> 413. A file, an upload or a project's total size exceeds a limit."""


class UnsupportedMediaTypeError(DomainError):
    """-> 415. The file is not one of the accepted document types."""


class FileTooLargeError(PayloadTooLargeError):
    pass


class TooManyFilesError(PayloadTooLargeError):
    pass


class ProjectSizeLimitError(PayloadTooLargeError):
    pass


class UnsupportedFileTypeError(UnsupportedMediaTypeError):
    pass


class DocumentNotFoundError(NotFoundError):
    def __init__(self) -> None:
        super().__init__("Document not found")


class ProjectNotFoundError(NotFoundError):
    def __init__(self) -> None:
        super().__init__("Project not found")


class UserNotFoundError(NotFoundError):
    def __init__(self, login: str) -> None:
        super().__init__(f"User '{login}' not found")


class AlreadyMemberError(ConflictError):
    def __init__(self, login: str) -> None:
        super().__init__(f"User '{login}' is already a member of this project")
