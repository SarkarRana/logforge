"""LogCore - Production logging for Python."""

from .config import LogCoreConfig, LogLevel
from .formatters import JSONFormatter, TextFormatter
from .handlers import dropped_record_count, flush, shutdown
from .interop import configure_stdlib, dict_config_formatter, reset_stdlib
from .logger import LogCoreLogger, get_logger
from .middleware import CorrelationIdMiddleware, WSGICorrelationIdMiddleware
from .sampling import Decision, Sampler, SamplerStats
from .utils import (
    AsyncTimer,
    Timer,
    correlation_id_context,
    generate_correlation_id,
    get_correlation_id,
    set_correlation_id,
)

try:  # pragma: no cover - trivial, and the fallback only fires when unbuilt
    from importlib.metadata import PackageNotFoundError, version

    # Single source of truth: read what is actually installed. Keeping a
    # hand-maintained literal here let __version__ drift from pyproject.toml,
    # so the published 0.1.6 wheel reported itself as 0.1.5.
    __version__ = version("logcore")
except (ImportError, PackageNotFoundError):  # pragma: no cover
    __version__ = "0.0.0.dev0"

__all__ = [
    # Core
    "get_logger",
    "LogCoreLogger",
    "LogCoreConfig",
    "LogLevel",
    # Correlation IDs
    "set_correlation_id",
    "get_correlation_id",
    "generate_correlation_id",
    "correlation_id_context",
    # Timing
    "Timer",
    "AsyncTimer",
    # Formatters (for dictConfig and custom handlers)
    "JSONFormatter",
    "TextFormatter",
    # Sampling
    "Sampler",
    "SamplerStats",
    "Decision",
    # stdlib interop
    "configure_stdlib",
    "reset_stdlib",
    "dict_config_formatter",
    # Web middleware
    "CorrelationIdMiddleware",
    "WSGICorrelationIdMiddleware",
    # Lifecycle
    "flush",
    "shutdown",
    "dropped_record_count",
    "__version__",
]
