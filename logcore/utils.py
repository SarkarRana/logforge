"""Utilities for LogCore."""

import asyncio
import contextvars
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional, Type

correlation_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)


def generate_correlation_id() -> str:
    return str(uuid.uuid4())


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    if correlation_id is None:
        correlation_id = generate_correlation_id()

    correlation_context.set(correlation_id)
    return correlation_id


def get_correlation_id() -> Optional[str]:
    return correlation_context.get()


@contextmanager
def correlation_id_context(
    correlation_id: Optional[str] = None,
) -> Generator[str, None, None]:
    if correlation_id is None:
        correlation_id = generate_correlation_id()

    token = correlation_context.set(correlation_id)
    try:
        yield correlation_id
    finally:
        correlation_context.reset(token)


class Timer:
    def __init__(
        self, logger: Any, operation_name: str, level: str = "INFO", **kwargs: Any
    ) -> None:
        self.logger = logger
        self.operation_name = operation_name
        self.level = level.upper()
        self.extra_fields: Dict[str, Any] = kwargs
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        self.logger._log(
            self.level, f"Starting {self.operation_name}", **self.extra_fields
        )
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Any,
    ) -> None:
        self.end_time = time.perf_counter()
        assert self.start_time is not None
        duration = self.end_time - self.start_time

        if exc_type is not None:
            # Pass the live exception rather than str(exc_val): the formatters
            # turn exc_info into a real traceback plus exception_type /
            # exception_message, where a bare string loses all of it.
            self.logger._log(
                "ERROR",
                f"Failed {self.operation_name}",
                duration_ms=round(duration * 1000, 2),
                exc_info=(exc_type, exc_val, exc_tb) if exc_val else False,
                **self.extra_fields,
            )
        else:
            self.logger._log(
                self.level,
                f"Completed {self.operation_name}",
                duration_ms=round(duration * 1000, 2),
                **self.extra_fields,
            )

    @property
    def elapsed(self) -> Optional[float]:
        if self.start_time is None:
            return None
        end_time = self.end_time if self.end_time is not None else time.perf_counter()
        return end_time - self.start_time


class AsyncTimer:
    def __init__(
        self, logger: Any, operation_name: str, level: str = "INFO", **kwargs: Any
    ) -> None:
        self.logger = logger
        self.operation_name = operation_name
        self.level = level.upper()
        self.extra_fields: Dict[str, Any] = kwargs
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    async def __aenter__(self) -> "AsyncTimer":
        self.start_time = time.perf_counter()
        self.logger._log(
            self.level, f"Starting {self.operation_name}", **self.extra_fields
        )
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Any,
    ) -> None:
        self.end_time = time.perf_counter()
        assert self.start_time is not None
        duration = self.end_time - self.start_time

        if exc_type is not None:
            # Pass the live exception rather than str(exc_val): the formatters
            # turn exc_info into a real traceback plus exception_type /
            # exception_message, where a bare string loses all of it.
            self.logger._log(
                "ERROR",
                f"Failed {self.operation_name}",
                duration_ms=round(duration * 1000, 2),
                exc_info=(exc_type, exc_val, exc_tb) if exc_val else False,
                **self.extra_fields,
            )
        else:
            self.logger._log(
                self.level,
                f"Completed {self.operation_name}",
                duration_ms=round(duration * 1000, 2),
                **self.extra_fields,
            )

    @property
    def elapsed(self) -> Optional[float]:
        if self.start_time is None:
            return None
        end_time = self.end_time if self.end_time is not None else time.perf_counter()
        return end_time - self.start_time


def is_async_context() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def safe_str(obj: Any, max_length: int = 1000) -> str:
    try:
        text = str(obj)
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text
    except Exception:
        return f"<{type(obj).__name__} object>"
