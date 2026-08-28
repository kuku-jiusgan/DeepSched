from fastapi import Request
from fastapi.responses import JSONResponse

from app.domain.errors import DomainConflictError, DomainNotFoundError, DomainValidationError


def register_domain_exception_handlers(app) -> None:
    app.add_exception_handler(DomainNotFoundError, _not_found)
    app.add_exception_handler(DomainValidationError, _validation_error)
    app.add_exception_handler(DomainConflictError, _conflict)


def _not_found(_request: Request, exc: DomainNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


def _validation_error(_request: Request, exc: DomainValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _conflict(_request: Request, exc: DomainConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.detail or str(exc)})
