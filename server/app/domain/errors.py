class DomainError(Exception):
    """Base class for business errors mapped at the API boundary."""


class DomainNotFoundError(DomainError):
    pass


class DomainValidationError(DomainError):
    pass


class DomainConflictError(DomainError):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail
