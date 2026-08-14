"""Custom handlers for LogCore."""

import atexit
import logging
import logging.handlers
import queue
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from .config import LogCoreConfig
from .formatters import JSONFormatter, TextFormatter

# Listeners started for queue-based (non-blocking) logging, drained at exit.
_listeners: "List[logging.handlers.QueueListener]" = []
_listeners_lock = threading.Lock()


def _build_formatter(
    config: LogCoreConfig,
    use_colors: Optional[bool] = None,
    stream: Optional[object] = None,
) -> logging.Formatter:
    """Return the formatter for a handler writing to ``stream``."""
    if config.json:
        return JSONFormatter(redact_fields=config.redact_fields)
    return TextFormatter(
        redact_fields=config.redact_fields,
        use_colors=use_colors,
        stream=stream,
    )


class ConsoleHandler:
    def __init__(self, config: LogCoreConfig):
        self.config = config
        stream = sys.stdout if config.console_stream == "stdout" else sys.stderr
        self.handler = logging.StreamHandler(stream)
        self.handler.setFormatter(_build_formatter(config, stream=stream))

    def get_handler(self) -> logging.Handler:
        return self.handler


class FileHandler:
    def __init__(self, config: LogCoreConfig, file_path: str):
        self.config = config

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self.handler = logging.handlers.RotatingFileHandler(
            filename=str(path),
            maxBytes=config.max_file_size,
            backupCount=config.backup_count,
            encoding="utf-8",
        )

        # Never colorize a file. The default resolves from sys.stderr.isatty(),
        # which has nothing to do with where this handler actually writes, and
        # would put raw ANSI escapes in the log file.
        self.handler.setFormatter(_build_formatter(config, use_colors=False))

    def get_handler(self) -> logging.Handler:
        return self.handler


def create_handlers(config: LogCoreConfig) -> List[logging.Handler]:
    """Build the handler list for ``config``.

    With ``async_logging`` enabled the real handlers are moved onto a background
    listener thread and the logger gets a single QueueHandler, so log calls no
    longer block the caller on I/O.
    """
    handlers: List[logging.Handler] = []

    if config.console:
        handlers.append(ConsoleHandler(config).get_handler())

    if config.file:
        handlers.append(FileHandler(config, config.file).get_handler())

    if config.async_logging and handlers:
        return [_wrap_in_queue(handlers, config)]

    return handlers


class _DroppingQueue(queue.Queue):
    """Bounded queue that drops new records instead of blocking the caller.

    A blocking put would reintroduce exactly the stall async logging exists to
    avoid, so an overwhelmed queue sheds load and counts what it lost.
    """

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize)
        self.dropped = 0

    def put_nowait(self, item: object) -> None:
        try:
            super().put_nowait(item)
        except queue.Full:
            self.dropped += 1


def _wrap_in_queue(
    handlers: List[logging.Handler], config: LogCoreConfig
) -> logging.Handler:
    log_queue = _DroppingQueue(config.queue_size)
    queue_handler = logging.handlers.QueueHandler(log_queue)

    listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True
    )
    listener.start()

    with _listeners_lock:
        _listeners.append(listener)

    return queue_handler


def flush(timeout: float = 5.0) -> None:
    """Drain pending records on every active queue listener.

    Waits up to ``timeout`` seconds per listener for its queue to empty, then
    flushes the underlying handlers. Bounded so a wedged handler cannot hang
    process shutdown.
    """
    with _listeners_lock:
        listeners = list(_listeners)

    for listener in listeners:
        # QueueListener.queue is typed as a minimal protocol without empty();
        # ours is always a real queue.Queue.
        is_empty = getattr(listener.queue, "empty", None)
        deadline = time.monotonic() + timeout
        while is_empty is not None and not is_empty():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        for handler in listener.handlers:
            try:
                handler.flush()
            except Exception:  # pragma: no cover - flush is best effort
                pass


def shutdown() -> None:
    """Stop all queue listeners after draining them.

    Registered with atexit, and safe to call explicitly before a hard exit
    (``os._exit``, a container SIGKILL grace period) where atexit will not run.
    """
    with _listeners_lock:
        listeners = list(_listeners)
        _listeners.clear()

    for listener in listeners:
        try:
            listener.stop()
        except Exception:  # pragma: no cover - shutdown is best effort
            pass
        for handler in listener.handlers:
            try:
                handler.close()
            except Exception:  # pragma: no cover - shutdown is best effort
                pass


atexit.register(shutdown)


def dropped_record_count() -> int:
    """Total records shed by full async queues since process start."""
    with _listeners_lock:
        listeners = list(_listeners)
    return sum(getattr(listener.queue, "dropped", 0) for listener in listeners)
