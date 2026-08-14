"""Correlation-ID middleware for ASGI and WSGI applications.

Zero-dependency and framework-agnostic: nothing here imports FastAPI,
Starlette, Flask or Django. Both middlewares:

1. Adopt an inbound request ID (``X-Request-ID``, falling back to the W3C
   ``traceparent`` trace-id), or generate one.
2. Bind it for the duration of the request via :func:`correlation_id_context`,
   so every log record emitted while handling the request carries it.
3. Echo it back on the response so a client can quote it in a bug report.
4. Close out the correlation scope on the way out. With tail-based sampling
   this is what releases the buffered records — without it, a service using
   ``set_correlation_id`` directly accumulates one buffer per request.
"""

import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from .logger import LogCoreLogger
from .utils import correlation_id_context

__all__ = ["CorrelationIdMiddleware", "WSGICorrelationIdMiddleware", "DEFAULT_HEADER"]

DEFAULT_HEADER = "X-Request-ID"

# version "-" trace-id "-" parent-id "-" flags
_TRACEPARENT_RE = re.compile(
    r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$", re.IGNORECASE
)

# Correlation IDs land in log output and response headers, so bound their
# length and character set rather than reflecting arbitrary client input.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def _clean(value: Optional[str]) -> Optional[str]:
    """Return ``value`` if it is safe to use as a correlation ID."""
    if not value:
        return None
    value = value.strip()
    if not _SAFE_ID_RE.match(value):
        return None
    return value


def _from_traceparent(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = _TRACEPARENT_RE.match(value.strip())
    return match.group(1) if match else None


class CorrelationIdMiddleware:
    """ASGI middleware binding a correlation ID for each request.

    Works with any ASGI framework (FastAPI, Starlette, Litestar, Quart)::

        app.add_middleware(CorrelationIdMiddleware)

    or by wrapping directly::

        app = CorrelationIdMiddleware(app)

    Args:
        app: The ASGI application to wrap.
        header: Request/response header carrying the ID.
        logger: Optional LogCore logger whose tail-sampling buffer should be
            released when the request ends.
        echo: Whether to add the ID to the response headers.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        header: str = DEFAULT_HEADER,
        logger: Optional[LogCoreLogger] = None,
        echo: bool = True,
    ) -> None:
        self.app = app
        self.header = header
        self.header_bytes = header.lower().encode("latin-1")
        self.logger = logger
        self.echo = echo

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers: Iterable[Tuple[bytes, bytes]] = scope.get("headers") or ()
        incoming: Optional[str] = None
        traceparent: Optional[str] = None

        for key, value in headers:
            lowered = key.lower()
            if lowered == self.header_bytes:
                incoming = value.decode("latin-1", "replace")
            elif lowered == b"traceparent":
                traceparent = value.decode("latin-1", "replace")

        correlation_id = _clean(incoming) or _from_traceparent(traceparent)

        with correlation_id_context(correlation_id) as cid:
            if self.echo:
                send = self._wrap_send(send, cid)
            try:
                await self.app(scope, receive, send)
            finally:
                self._release(cid)

    def _wrap_send(self, send: Any, cid: str) -> Any:
        header_pair = (self.header.encode("latin-1"), cid.encode("latin-1"))

        async def send_with_header(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers") or ()) + [header_pair]
            await send(message)

        return send_with_header

    def _release(self, cid: str) -> None:
        if self.logger is not None:
            self.logger.flush_sample_buffer(cid)


class WSGICorrelationIdMiddleware:
    """WSGI counterpart of :class:`CorrelationIdMiddleware`.

    Works with Flask, Django and any other WSGI application::

        app.wsgi_app = WSGICorrelationIdMiddleware(app.wsgi_app)
    """

    def __init__(
        self,
        app: Callable[..., Iterable[bytes]],
        header: str = DEFAULT_HEADER,
        logger: Optional[LogCoreLogger] = None,
        echo: bool = True,
    ) -> None:
        self.app = app
        self.header = header
        # WSGI upper-cases headers and replaces dashes: X-Request-ID becomes
        # HTTP_X_REQUEST_ID.
        self.environ_key = "HTTP_" + header.upper().replace("-", "_")
        self.logger = logger
        self.echo = echo

    def __call__(
        self, environ: Dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        correlation_id = _clean(environ.get(self.environ_key)) or _from_traceparent(
            environ.get("HTTP_TRACEPARENT")
        )

        with correlation_id_context(correlation_id) as cid:

            def start_response_with_header(
                status: str,
                headers: List[Tuple[str, str]],
                exc_info: Any = None,
            ) -> Any:
                if self.echo:
                    headers = list(headers) + [(self.header, cid)]
                return start_response(status, headers, exc_info)

            try:
                # Consume the iterable inside the correlation scope so records
                # emitted by a streaming response still carry the ID.
                return list(self.app(environ, start_response_with_header))
            finally:
                if self.logger is not None:
                    self.logger.flush_sample_buffer(cid)
