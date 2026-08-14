"""Interoperability between LogCore and the standard library's ``logging``.

``get_logger`` only formats records you emit yourself. Everything a third-party
library logs — uvicorn, gunicorn, sqlalchemy, requests, celery, botocore — goes
through the stdlib root logger and comes out in whatever format that logger
happens to have. In a JSON log pipeline that means half the output is
unparseable.

:func:`configure_stdlib` closes that gap by putting LogCore's formatters on the
root logger, so *every* record in the process is formatted consistently.
"""

import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from .config import LogLevel, create_config
from .handlers import create_handlers

__all__ = ["configure_stdlib", "reset_stdlib", "dict_config_formatter"]

# Handlers this module installed, so reset_stdlib can remove exactly those.
_installed: List[logging.Handler] = []


def configure_stdlib(
    level: Union[str, LogLevel] = "INFO",
    json: bool = True,
    file: Optional[str] = None,
    replace_existing: bool = True,
    redact_fields: Optional[Set[str]] = None,
    quiet: Optional[Iterable[Union[str, Tuple[str, str]]]] = None,
    async_logging: bool = False,
) -> logging.Logger:
    """Route the stdlib root logger through LogCore's formatters.

    Call this once during application startup, before the libraries you want to
    capture emit anything.

    Args:
        level: Root log level.
        json: Emit JSON (the usual choice when this is worth doing) or
            human-readable text.
        file: Optional path to also write a rotating log file.
        replace_existing: Remove handlers already on the root logger. Leave
            this True to override an earlier ``logging.basicConfig()``, which
            would otherwise double every line.
        redact_fields: Field names to mask. Defaults to LogCore's built-in set.
        quiet: Loggers to turn down. Either a name (raised to WARNING) or a
            ``(name, level)`` pair.
        async_logging: Move handler I/O onto a background thread.

    Returns:
        The configured root logger.

    Example:
        >>> import logcore
        >>> logcore.configure_stdlib(level="INFO", json=True,
        ...                          quiet=["urllib3", ("botocore", "ERROR")])
    """
    level_value = level.value if isinstance(level, LogLevel) else str(level)

    config = create_config(
        name="root",
        level=level_value,
        json=json,
        file=file,
        redact_fields=redact_fields,
        async_logging=async_logging,
    )

    root = logging.getLogger()

    if replace_existing:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - close is best effort
                pass
        _installed.clear()

    numeric_level = getattr(logging, config.level.value)
    root.setLevel(numeric_level)

    for handler in create_handlers(config):
        handler.setLevel(numeric_level)
        root.addHandler(handler)
        _installed.append(handler)

    for entry in quiet or ():
        if isinstance(entry, tuple):
            name, quiet_level = entry
        else:
            name, quiet_level = entry, "WARNING"
        logging.getLogger(name).setLevel(
            getattr(logging, LogLevel.from_string(quiet_level).value)
        )

    return root


def reset_stdlib() -> None:
    """Remove the handlers :func:`configure_stdlib` installed on the root logger.

    Intended for tests and for applications that reconfigure logging at
    runtime. Handlers installed by anything else are left alone.
    """
    root = logging.getLogger()
    for handler in _installed:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - close is best effort
            pass
    _installed.clear()


def dict_config_formatter(
    json: bool = True, redact_fields: Optional[Set[str]] = None
) -> Dict[str, object]:
    """Return a ``logging.config.dictConfig`` formatter entry for LogCore.

    For applications that configure logging declaratively rather than by
    calling :func:`configure_stdlib`.

    Example:
        >>> import logging.config, logcore
        >>> logging.config.dictConfig({
        ...     "version": 1,
        ...     "formatters": {"logcore": logcore.dict_config_formatter()},
        ...     "handlers": {"console": {"class": "logging.StreamHandler",
        ...                              "formatter": "logcore"}},
        ...     "root": {"handlers": ["console"], "level": "INFO"},
        ... })
    """
    entry: Dict[str, object] = {
        "()": (
            "logcore.formatters.JSONFormatter"
            if json
            else "logcore.formatters.TextFormatter"
        )
    }
    if redact_fields is not None:
        entry["redact_fields"] = redact_fields
    return entry
