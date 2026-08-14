"""Configuration management for LogCore."""

import os
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Set

from .sampling import Sampler, sampler_from_env


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @classmethod
    def from_string(cls, level: str) -> "LogLevel":
        level = level.upper()
        if level == "WARN":
            return cls.WARNING
        return cls(level)


@dataclass
class LogCoreConfig:
    name: str
    level: LogLevel = LogLevel.INFO
    json: bool = False
    file: Optional[str] = None
    correlation_id: Optional[str] = None
    max_file_size: int = 10 * 1024 * 1024
    backup_count: int = 5
    redact_fields: Optional[Set[str]] = None
    sampler: Optional[Sampler] = None
    propagate: bool = False
    console: bool = True
    console_stream: str = "stderr"
    async_logging: bool = False
    queue_size: int = 10000

    def __post_init__(self) -> None:
        if self.redact_fields is None:
            self.redact_fields = {
                "password",
                "passwd",
                "secret",
                "token",
                "key",
                "api_key",
                "access_token",
                "auth",
                "authorization",
                "credential",
                "private_key",
                "cert",
                "certificate",
            }


_TRUE_VALUES = ("true", "1", "yes", "on")
_FALSE_VALUES = ("false", "0", "no", "off")


def _warn_bad_env(name: str, value: str, reason: str) -> None:
    """Warn about an unusable env var instead of silently ignoring it.

    A typo'd LOGCORE_SAMPLE_RATE used to fall through to the default, which
    means full-volume logging in production with no indication why.
    """
    warnings.warn(
        f"Ignoring invalid {name}={value!r}: {reason}. Using the default.",
        UserWarning,
        stacklevel=3,
    )


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    _warn_bad_env(name, raw, "expected a boolean")
    return None


def _env_int(name: str, minimum: int = 0) -> Optional[int]:
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        _warn_bad_env(name, raw, "expected an integer")
        return None
    if value < minimum:
        _warn_bad_env(name, raw, f"must be >= {minimum}")
        return None
    return value


def get_config_from_env() -> Dict[str, Any]:
    config: Dict[str, Any] = {}

    if level := os.getenv("LOGCORE_LEVEL"):
        try:
            config["level"] = LogLevel.from_string(level)
        except ValueError:
            _warn_bad_env(
                "LOGCORE_LEVEL",
                level,
                "expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL",
            )

    json_env = _env_bool("LOGCORE_JSON")
    if json_env is not None:
        config["json"] = json_env

    if file_path := os.getenv("LOGCORE_FILE"):
        config["file"] = file_path

    if correlation_id := os.getenv("LOGCORE_CORRELATION_ID"):
        config["correlation_id"] = correlation_id

    max_size = _env_int("LOGCORE_MAX_FILE_SIZE", minimum=1)
    if max_size is not None:
        config["max_file_size"] = max_size

    backup_count = _env_int("LOGCORE_BACKUP_COUNT", minimum=0)
    if backup_count is not None:
        config["backup_count"] = backup_count

    if redact_fields := os.getenv("LOGCORE_REDACT_FIELDS"):
        config["redact_fields"] = set(
            field.strip() for field in redact_fields.split(",") if field.strip()
        )

    propagate = _env_bool("LOGCORE_PROPAGATE")
    if propagate is not None:
        config["propagate"] = propagate

    console = _env_bool("LOGCORE_CONSOLE")
    if console is not None:
        config["console"] = console

    if stream := os.getenv("LOGCORE_CONSOLE_STREAM"):
        if stream.lower() in ("stdout", "stderr"):
            config["console_stream"] = stream.lower()
        else:
            _warn_bad_env(
                "LOGCORE_CONSOLE_STREAM", stream, "expected 'stdout' or 'stderr'"
            )

    async_logging = _env_bool("LOGCORE_ASYNC")
    if async_logging is not None:
        config["async_logging"] = async_logging

    queue_size = _env_int("LOGCORE_QUEUE_SIZE", minimum=1)
    if queue_size is not None:
        config["queue_size"] = queue_size

    env_sampler = sampler_from_env()
    if env_sampler is not None:
        config["sampler"] = env_sampler

    return config


def create_config(
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
) -> LogCoreConfig:
    env_config = get_config_from_env()

    if sampler is not None and sample_rate is not None:
        raise ValueError("Pass either `sampler` or `sample_rate`, not both.")

    if sampler is None and sample_rate is not None:
        sampler = Sampler(rate=sample_rate)

    if console_stream is not None and console_stream not in ("stdout", "stderr"):
        raise ValueError(
            f"console_stream must be 'stdout' or 'stderr', got {console_stream!r}"
        )

    config_dict = {
        "name": name,
        "level": (
            LogLevel.from_string(level)
            if level
            else env_config.get("level", LogLevel.INFO)
        ),
        "json": json if json is not None else env_config.get("json", False),
        "file": file if file is not None else env_config.get("file"),
        "correlation_id": (
            correlation_id
            if correlation_id is not None
            else env_config.get("correlation_id")
        ),
        "max_file_size": (
            max_file_size
            if max_file_size is not None
            else env_config.get("max_file_size", 10 * 1024 * 1024)
        ),
        "backup_count": (
            backup_count
            if backup_count is not None
            else env_config.get("backup_count", 5)
        ),
        "redact_fields": (
            redact_fields
            if redact_fields is not None
            else env_config.get("redact_fields")
        ),
        "sampler": sampler if sampler is not None else env_config.get("sampler"),
        "propagate": (
            propagate if propagate is not None else env_config.get("propagate", False)
        ),
        "console": console if console is not None else env_config.get("console", True),
        "console_stream": (
            console_stream
            if console_stream is not None
            else env_config.get("console_stream", "stderr")
        ),
        "async_logging": (
            async_logging
            if async_logging is not None
            else env_config.get("async_logging", False)
        ),
        "queue_size": (
            queue_size
            if queue_size is not None
            else env_config.get("queue_size", 10000)
        ),
    }

    return LogCoreConfig(**config_dict)  # type: ignore[arg-type]
