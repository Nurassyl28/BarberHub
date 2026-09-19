"""Structured logging with a per-request id."""

import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

#: Set per request, read by the log filter. A ContextVar rather than a global
#: because concurrent requests share the process.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn installs its own handlers; let them fall through to ours so every
    # line carries the same request id.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def register_request_logging(app: FastAPI) -> None:
    logger = logging.getLogger("app.request")

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Honour an inbound id so a trace survives across services; mint one
        # otherwise. Either way it goes back on the response, so a user can
        # quote it in a bug report and it can be found in the logs.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "%s %s failed after %.1fms",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise
        finally:
            request_id_var.reset(token)

        elapsed = (time.perf_counter() - started) * 1000
        logger.info(
            "%s %s -> %s in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
