"""Core logging functionality for LogCore."""

import logging
import os
import sys
import threading
import warnings
from contextlib import contextmanager
from types import FrameType
from typing import Any, Dict, Generator, Optional, Set, Tuple, Union

try:
    from opentelemetry import trace as _otel_trace

    _HAS_OTEL = True
except ImportError:  # pragma: no cover
    _HAS_OTEL = False

from .config import LogCoreConfig, LogLevel, create_config
from .handlers import create_handlers
from .sampling import Decision, Sampler
from .utils import (
    AsyncTimer,
    Timer,
    correlation_id_context,
    get_correlation_id,
    is_async_context,
    safe_str,
    set_correlation_id,
)

_srcfile = os.path.normcase(__file__)
_utilsfile = os.path.normcase(os.path.join(os.path.dirname(__file__), "utils.py"))

# Files whose frames are skipped when attributing a log record to its caller.
_internal_files = frozenset({_srcfile, _utilsfile})

# co_filename -> normcase(co_filename). normcase is pure string work but it is
# not free at ~1M calls/minute, and the set of source files is tiny and stable.
_normcase_cache: Dict[str, str] = {}


def _find_caller(stacklevel: int = 1) -> Tuple[str, int, str]:
    """Return (filename, lineno, funcname) of the first caller outside logcore.

    Mirrors ``logging.Logger.findCaller``: compares ``co_filename`` against
    precomputed module paths rather than calling ``os.path.abspath``, which
    issues a ``getcwd`` syscall per frame.

    ``stacklevel`` skips additional frames once the first external frame is
    found, so wrappers can attribute a record to their own caller.
    """
    try:
        frame: Optional[FrameType] = sys._getframe(1)
    except (AttributeError, ValueError):  # pragma: no cover - CPython always has it
        return "(unknown file)", 0, "(unknown function)"

    while frame is not None:
        co_filename = frame.f_code.co_filename
        normalized = _normcase_cache.get(co_filename)
        if normalized is None:
            normalized = os.path.normcase(co_filename)
            _normcase_cache[co_filename] = normalized

        if normalized not in _internal_files:
            # First frame outside logcore. Honor any extra stacklevel from here.
            for _ in range(stacklevel - 1):
                if frame.f_back is None:
                    break
                frame = frame.f_back
            return frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name

        frame = frame.f_back

    return "(unknown file)", 0, "(unknown function)"


_logger_lock = threading.RLock()
_loggers: Dict[str, "LogCoreLogger"] = {}

# Level name -> numeric level, resolved once instead of getattr(logging, ...)
# on every call.
_LEVEL_NUMBERS: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}

# Values passed through to the formatter unchanged. dict/list/tuple are kept
# structured so JSON output is machine-parseable and the redactor can recurse.
_PASSTHROUGH_TYPES = (str, bool, int, float, type(None), dict, list, tuple)

# LogRecord attributes a user field must not overwrite. Includes names present
# only on some Python versions (taskName landed in 3.12).
_RESERVED_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_warned_reserved_keys: Set[str] = set()


class LogCoreLogger:
    def __init__(self, config: LogCoreConfig):
        self.config = config
        self.sampler: Optional[Sampler] = config.sampler

        self._logger = logging.getLogger(f"logcore.{config.name}")
        self._logger.setLevel(getattr(logging, config.level.value))

        # Records are emitted by our own handlers. Propagating them to the root
        # logger as well makes every line appear twice as soon as anything in
        # the process calls logging.basicConfig().
        self._logger.propagate = config.propagate

        # clear() drops the references without closing them, leaking a file
        # descriptor per reconfiguration when a file handler is attached.
        for old_handler in list(self._logger.handlers):
            self._logger.removeHandler(old_handler)
            try:
                old_handler.close()
            except Exception:  # pragma: no cover - handler close is best effort
                pass

        handlers = create_handlers(config)
        for handler in handlers:
            handler.setLevel(getattr(logging, config.level.value))
            self._logger.addHandler(handler)

        if config.correlation_id:
            set_correlation_id(config.correlation_id)

    def _log(self, level: str, message: str, *args: Any, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", False)
        stacklevel = kwargs.pop("stacklevel", 1)

        numeric_level = _LEVEL_NUMBERS.get(level, logging.INFO)

        if not self._logger.isEnabledFor(numeric_level):
            return

        correlation_id = get_correlation_id()

        # Decide before building the record. A dropped record should not pay for
        # frame inspection, makeRecord, the OTel span lookup, or field coercion.
        sampler = self.sampler
        decision = Decision.KEEP
        if sampler is not None:
            decision = sampler.decide_early(numeric_level, correlation_id)
            if decision is Decision.DROP:
                return

        if exc_info is True:
            exc_info = sys.exc_info()
        elif isinstance(exc_info, BaseException):
            exc_info = (type(exc_info), exc_info, exc_info.__traceback__)

        fn, lno, func = _find_caller(stacklevel)
        record = self._logger.makeRecord(
            self._logger.name,
            numeric_level,
            fn,
            lno,
            message,
            args,
            exc_info=exc_info,
            func=func,
        )

        if correlation_id:
            record.correlation_id = correlation_id

        if _HAS_OTEL:
            _span = _otel_trace.get_current_span()
            if _span.is_recording():
                _ctx = _span.get_span_context()
                if _ctx.is_valid:
                    record.trace_id = format(_ctx.trace_id, "032x")
                    record.span_id = format(_ctx.span_id, "016x")

        if kwargs:
            self._attach_extras(record, kwargs)

        if sampler is not None:
            if decision is Decision.BUFFER:
                sampler.buffer(record, correlation_id)
                return
            for buffered in sampler.flush_pending(record, correlation_id):
                self._logger.handle(buffered)

        self._logger.handle(record)

    @staticmethod
    def _attach_extras(record: logging.LogRecord, extras: Dict[str, Any]) -> None:
        """Merge user-supplied fields onto ``record``.

        Structured values (dict/list/tuple) are kept intact so JSON output stays
        machine-parseable and the redactor can walk into them. Only genuinely
        opaque objects are stringified.

        Keys that collide with a LogRecord attribute are stored under ``key_``
        rather than being dropped, and warn once per key.
        """
        for key, value in extras.items():
            target = key
            if key in _RESERVED_RECORD_FIELDS or hasattr(record, key):
                target = f"{key}_"
                if key not in _warned_reserved_keys:
                    _warned_reserved_keys.add(key)
                    warnings.warn(
                        f"Log field '{key}' collides with a reserved LogRecord "
                        f"attribute and was emitted as '{target}' instead. "
                        "Rename the field to silence this warning.",
                        UserWarning,
                        stacklevel=4,
                    )

            if isinstance(value, _PASSTHROUGH_TYPES):
                setattr(record, target, value)
            else:
                setattr(record, target, safe_str(value))

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("DEBUG", message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("INFO", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("WARNING", message, *args, **kwargs)

    def warn(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("ERROR", message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._log("CRITICAL", message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self.error(message, *args, **kwargs)

    def flush(self) -> None:
        """Flush all handlers attached to this logger."""
        for handler in list(self._logger.handlers):
            try:
                handler.flush()
            except Exception:  # pragma: no cover - flush is best effort
                pass

    def time(
        self, operation_name: str, level: str = "INFO", **kwargs: Any
    ) -> Union[Timer, AsyncTimer]:
        """Return a context manager that logs start/complete and duration_ms.

        Auto-detects async context: returns AsyncTimer inside a running event
        loop task, Timer otherwise. Use with ``async with`` or ``with`` accordingly.
        """
        if is_async_context():
            return AsyncTimer(self, operation_name, level, **kwargs)
        else:
            return Timer(self, operation_name, level, **kwargs)

    @contextmanager
    def with_correlation_id(
        self, correlation_id: Optional[str] = None
    ) -> Generator[str, None, None]:
        """Return a context manager that sets a correlation ID for this scope.

        Uses contextvars, so the ID is isolated per async task or thread.
        A UUID is generated automatically when correlation_id is omitted.

        When a tail-based sampler is attached, any buffered records for this
        correlation_id are discarded on clean exit (the request didn't error,
        so we drop the captured history).
        """
        with correlation_id_context(correlation_id) as cid:
            try:
                yield cid
            finally:
                if self.sampler is not None and self.sampler.tail_based:
                    self.sampler.discard_buffer(cid)

    def flush_sample_buffer(self, correlation_id: Optional[str] = None) -> int:
        """Discard any tail-buffered records for ``correlation_id``.

        Call this at request end when you set the correlation_id directly
        (e.g. via middleware) instead of using ``with_correlation_id``. When
        ``correlation_id`` is omitted, the current contextvar value is used.

        Returns the number of records discarded; 0 if no sampler is attached,
        sampling is not tail-based, or no buffer exists for the given cid.
        """
        if self.sampler is None or not self.sampler.tail_based:
            return 0
        cid = correlation_id if correlation_id is not None else get_correlation_id()
        if cid is None:
            return 0
        return self.sampler.discard_buffer(cid)

    def set_level(self, level: Union[str, LogLevel]) -> None:
        if isinstance(level, str):
            level = LogLevel.from_string(level)

        self.config.level = level
        numeric_level = getattr(logging, level.value)

        self._logger.setLevel(numeric_level)
        for handler in self._logger.handlers:
            handler.setLevel(numeric_level)

    def get_level(self) -> LogLevel:
        return self.config.level

    def is_enabled_for(self, level: Union[str, LogLevel]) -> bool:
        if isinstance(level, str):
            level = LogLevel.from_string(level)

        numeric_level = getattr(logging, level.value)
        return self._logger.isEnabledFor(numeric_level)


def get_logger(
    name: str,
    level: Optional[str] = None,
    json: Optional[bool] = None,
    file: Optional[str] = None,
    correlation_id: Optional[str] = None,
    max_file_size: Optional[int] = None,
    backup_count: Optional[int] = None,
    redact_fields: Optional[Set[str]] = None,
    sampler: Optional[Sampler] = None,
    sample_rate: Optional[float] = None,
    propagate: Optional[bool] = None,
    console: Optional[bool] = None,
    console_stream: Optional[str] = None,
    async_logging: Optional[bool] = None,
    queue_size: Optional[int] = None,
) -> "LogCoreLogger":
    """Return a LogCoreLogger for the given name, creating it if needed.

    Loggers are cached by name. Calling with the same name and no extra
    arguments returns the existing logger. Passing any configuration
    argument forces a new logger to be created and cached, replacing the old one.
    Environment variables (LOGCORE_*) are applied as defaults when a parameter
    is omitted.

    Pass ``sampler`` for full control, or ``sample_rate`` as a shortcut for
    ``Sampler(rate=sample_rate)``. Passing both raises ``ValueError``.
    """
    if sampler is not None and sample_rate is not None:
        raise ValueError("Pass either `sampler` or `sample_rate`, not both.")

    with _logger_lock:
        if name in _loggers:
            existing_logger = _loggers[name]

            if all(
                param is None
                for param in [
                    level,
                    json,
                    file,
                    correlation_id,
                    max_file_size,
                    backup_count,
                    redact_fields,
                    sampler,
                    sample_rate,
                    propagate,
                    console,
                    console_stream,
                    async_logging,
                    queue_size,
                ]
            ):
                return existing_logger

            warnings.warn(
                f"Logger '{name}' already exists and is being replaced with new "
                "configuration. Existing references to the old logger will no longer "
                "receive log records.",
                UserWarning,
                stacklevel=2,
            )

        config = create_config(
            name=name,
            level=level,
            json=json,
            file=file,
            correlation_id=correlation_id,
            max_file_size=max_file_size,
            backup_count=backup_count,
            redact_fields=redact_fields,
            sampler=sampler,
            sample_rate=sample_rate,
            propagate=propagate,
            console=console,
            console_stream=console_stream,
            async_logging=async_logging,
            queue_size=queue_size,
        )

        logger = LogCoreLogger(config)
        _loggers[name] = logger

        return logger
