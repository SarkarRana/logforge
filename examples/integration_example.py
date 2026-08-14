#!/usr/bin/env python3
"""The v0.1.7 integration features, runnable without any web framework.

Demonstrates:

1. configure_stdlib()  - third-party library logs formatted like your own
2. structured extras   - nested dicts stay real JSON, and stay redacted
3. WSGI middleware     - correlation IDs bound per request
4. async_logging       - handler I/O off the calling thread

Run with: python examples/integration_example.py
"""

import logging
from typing import Any, Callable, Dict, List, Tuple

import logcore
from logcore import WSGICorrelationIdMiddleware, get_logger


def demo_stdlib_capture() -> None:
    print("\n=== 1. Third-party logs captured through LogCore ===")
    logcore.configure_stdlib(level="INFO", json=True, quiet=["chatty"])

    # Pretend these are uvicorn / sqlalchemy / requests.
    logging.getLogger("sqlalchemy.engine").info("SELECT * FROM orders")
    logging.getLogger("chatty").info("this is filtered out by quiet=")
    logging.getLogger("chatty").warning("but warnings still get through")

    logcore.reset_stdlib()


def demo_structured_and_redaction() -> None:
    print("\n=== 2. Nested structure preserved, secrets masked at any depth ===")
    log = get_logger("orders", level="INFO", json=True)

    log.info(
        "order placed",
        order={"id": 4821, "total": 99.5},
        # password/token are default redact fields; before v0.1.7 these were
        # stringified before redaction ran and shipped in cleartext.
        customer={"name": "bob", "password": "hunter2secret"},
        payments=[{"token": "tok_abcdefghij"}],
    )


def demo_middleware() -> None:
    print("\n=== 3. Correlation IDs bound per request ===")
    log = get_logger("api", level="INFO", json=True)

    def app(environ: Dict[str, Any], start_response: Callable[..., Any]) -> List[bytes]:
        # No correlation plumbing in the handler at all.
        log.info("handling request", path=environ.get("PATH_INFO"))
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    captured: Dict[str, Any] = {}

    def start_response(
        status: str, headers: List[Tuple[str, str]], exc_info: Any = None
    ) -> None:
        captured["headers"] = headers

    wrapped = WSGICorrelationIdMiddleware(app, logger=log)

    # Client supplies an ID...
    wrapped({"PATH_INFO": "/orders", "HTTP_X_REQUEST_ID": "req-42"}, start_response)
    print(f"  echoed back: {captured['headers']}")

    # ...or one is generated.
    wrapped({"PATH_INFO": "/health"}, start_response)


def demo_async_logging() -> None:
    print("\n=== 4. Non-blocking delivery ===")
    log = get_logger("worker", level="INFO", json=True, async_logging=True)

    for index in range(3):
        log.info("processed batch", batch=index)

    # Records sit in a queue until the listener thread writes them, so drain
    # before exit. atexit does this too, but not on a hard exit.
    logcore.flush()
    print(f"  dropped due to full queue: {logcore.dropped_record_count()}")
    logcore.shutdown()


def main() -> None:
    demo_stdlib_capture()
    demo_structured_and_redaction()
    demo_middleware()
    demo_async_logging()
    print()


if __name__ == "__main__":
    main()
