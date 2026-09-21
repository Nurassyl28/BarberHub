"""Error hierarchy and the single error envelope (see docs/SPEC.md §7)."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base for every error we raise deliberately.

    Subclasses set `code` and `status_code`; handlers below turn them into the
    error envelope so no route has to build one by hand.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Validation failed"


class UnauthorizedError(AppError):
    code = "UNAUTHORIZED"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Authentication required"


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN
    message = "You do not have access to this resource"


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Resource not found"


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT
    message = "Conflicting state"


class SlotTakenError(ConflictError):
    code = "SLOT_TAKEN"
    message = "This time slot has just been booked by someone else"


class OutsideWorkingHoursError(ConflictError):
    code = "OUTSIDE_WORKING_HOURS"
    message = "The barber is not available at this time"


class InvalidTransitionError(ConflictError):
    code = "INVALID_TRANSITION"
    message = "This status change is not allowed"


class CutoffPassedError(ConflictError):
    code = "CUTOFF_PASSED"
    message = "It is too late to change this appointment"


class AlreadyReviewedError(ConflictError):
    code = "ALREADY_REVIEWED"
    message = "This appointment has already been reviewed"


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many requests"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                "VALIDATION_ERROR",
                "Validation failed",
                {"fields": _compact_validation_errors(exc)},
            ),
        )

    @app.exception_handler(PydanticValidationError)
    async def _pydantic(_: Request, exc: PydanticValidationError) -> JSONResponse:
        """A model validated inside a dependency, not from the request body.

        FastAPI only converts pydantic errors it raises itself, so a
        `Depends()`-resolved query model whose own validator fails would escape
        as a 500. This makes it the 422 the caller deserves.
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                "VALIDATION_ERROR",
                "Validation failed",
                {
                    "fields": [
                        {
                            "field": ".".join(str(p) for p in err["loc"]) or "query",
                            "message": err["msg"],
                        }
                        for err in exc.errors()
                    ]
                },
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "INTERNAL_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )


def _compact_validation_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    """Flatten pydantic's error list into `{field, message}` pairs."""
    out: list[dict[str, str]] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p != "body")
        out.append({"field": loc or "body", "message": err["msg"]})
    return out
