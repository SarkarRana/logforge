"""Formatters for LogCore logging output."""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

try:
    import colorama
    from colorama import Fore, Style

    HAS_COLORS = True
except ImportError:
    HAS_COLORS = False

    class Fore:  # type: ignore[no-redef]
        RED = YELLOW = GREEN = BLUE = CYAN = MAGENTA = WHITE = ""

    class Style:  # type: ignore[no-redef]
        RESET_ALL = BRIGHT = ""


_colorama_initialized = False


def _init_colorama() -> None:
    """Initialize colorama on first coloured formatter, not at import time.

    ``colorama.init()`` replaces sys.stdout/sys.stderr on Windows. Doing that
    as a side effect of ``import logcore`` surprises applications that never
    asked for coloured output.
    """
    global _colorama_initialized
    if HAS_COLORS and not _colorama_initialized:
        _colorama_initialized = True
        just_fix = getattr(colorama, "just_fix_windows_console", None)
        if just_fix is not None:
            just_fix()
        else:  # pragma: no cover - colorama < 0.4.6
            colorama.init()


def _should_use_colors(stream: Any) -> bool:
    """Decide whether to emit ANSI codes for ``stream``.

    Honors the NO_COLOR / FORCE_COLOR conventions, then falls back to asking
    the destination stream whether it is a terminal. Callers writing to a file
    must pass ``use_colors=False`` rather than relying on this.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return HAS_COLORS
    if os.environ.get("TERM") == "dumb":
        return False
    if not HAS_COLORS:
        return False
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except Exception:  # pragma: no cover - defensive
        return False


# All standard LogRecord attributes plus logcore fields that are emitted
# explicitly (e.g. correlation_id). Shared between both formatters so that
# adding a field in one place covers both.
_STDLIB_LOG_FIELDS: frozenset = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "exc_info",
        "exc_text",
        "stack_info",
        "taskName",
        "correlation_id",
    }
)


#: How deep the redaction walk descends before replacing the remainder with a
#: placeholder. Guards against deeply nested or self-referential structures.
MAX_REDACT_DEPTH = 6

#: Ceiling on containers visited in a single record's redaction walk.
MAX_REDACT_NODES = 1000


class RedactingFormatter:
    """Base formatter with partial masking of sensitive fields.

    Redaction is *structural*: the walk descends into dicts, lists and tuples
    of the log record's own fields, so a secret nested inside a payload is
    masked just like a top-level one. A regex pass over the rendered message
    catches secrets embedded in message text, which no structural walk can see.
    """

    def __init__(self, redact_fields: Optional[Set[str]] = None):
        self.redact_fields = redact_fields or set()
        # Precomputed once; _redact_value used to rebuild this per call *and*
        # per recursion level.
        self._redact_keys = frozenset(f.lower() for f in self.redact_fields)
        # Cheap "is any sensitive field name even mentioned here?" gate, run
        # before the expensive capture-group substitution. Measured faster
        # than a group-free probe regex: str.__contains__ has no automaton
        # setup and no IGNORECASE folding.
        self._redact_needles = tuple(self._redact_keys)
        self.redact_pattern = self._build_pattern()

    @staticmethod
    def _mask(value: Any) -> str:
        """Return a partially masked string, revealing only a short prefix.

        Values of 4 chars or fewer are fully redacted because any prefix
        would reveal a significant fraction of the secret.
        """
        s = str(value)
        if len(s) <= 4:
            return "[REDACTED]"
        prefix_len = min(4, max(1, len(s) // 4))
        return s[:prefix_len] + "***"

    def _build_pattern(self) -> Optional[re.Pattern]:
        if not self.redact_fields:
            return None
        fields = "|".join(re.escape(f) for f in self.redact_fields)
        pattern = rf'("{fields}"|{fields})(\s*[:=]\s*)("[^"]*"|[^\s,\]}}]+)'
        return re.compile(pattern, re.IGNORECASE)

    def _redact_text(self, text: str) -> str:
        """Mask ``field=value`` pairs appearing inside free text.

        The substitution regex carries three capture groups and a Python-level
        replacement callback; running it over every record dominated formatting
        cost. The substring pre-scan skips it entirely for the overwhelming
        majority of messages, which name no sensitive field.
        """
        if not self.redact_pattern or not text:
            return text

        # The pattern only matches `field<sep>value`, so text carrying neither
        # separator cannot match. Cheapest possible gate, and it spares the
        # lowercased copy below for the common "plain prose message" case.
        if "=" not in text and ":" not in text:
            return text

        lowered = text.lower()
        for needle in self._redact_needles:
            if needle in lowered:
                break
        else:
            return text

        def replace(match: "re.Match[str]") -> str:
            key = match.group(1)
            sep = match.group(2)
            raw = match.group(3)
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            return f'{key}{sep}"{self._mask(raw)}"'

        return self.redact_pattern.sub(replace, text)

    def _redact_value(self, value: Any, depth: int, budget: List[int]) -> Any:
        """Recursively mask sensitive keys inside dicts, lists and tuples."""
        if depth > MAX_REDACT_DEPTH:
            return "[TRUNCATED]"

        budget[0] -= 1
        if budget[0] < 0:
            return "[TRUNCATED]"

        if isinstance(value, dict):
            redacted: Dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str) and key.lower() in self._redact_keys:
                    redacted[key] = self._mask(item)
                else:
                    redacted[key] = self._redact_value(item, depth + 1, budget)
            return redacted

        if isinstance(value, (list, tuple)):
            items = [self._redact_value(item, depth + 1, budget) for item in value]
            # Only exact tuples are rebuilt as tuples. Reconstructing a
            # subclass via type(value)(items) breaks namedtuples, whose
            # __new__ takes one positional argument per field. Both forms
            # serialize to a JSON array either way.
            return tuple(items) if type(value) is tuple else items

        return value

    def _redact_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Return ``data`` with every sensitive field masked, at any depth.

        Returns ``data`` itself when nothing needs masking and no value is a
        container worth descending into — the overwhelmingly common case, and
        rebuilding the dict for it cost an allocation per record.
        """
        keys = self._redact_keys
        if not keys:
            return data

        for key, value in data.items():
            if key.lower() in keys or isinstance(value, (dict, list, tuple)):
                break
        else:
            return data

        result: Dict[str, Any] = {}
        budget = [MAX_REDACT_NODES]

        for key, value in data.items():
            if key.lower() in keys:
                result[key] = self._mask(value)
            elif isinstance(value, (dict, list, tuple)):
                result[key] = self._redact_value(value, 1, budget)
            else:
                result[key] = value

        return result


def _json_safe(value: Any, seen: Set[int]) -> Any:
    """Coerce ``value`` into something ``json.dumps`` will always accept.

    Only used as a fallback after a serialization failure, so it can afford to
    be thorough: it breaks reference cycles and stringifies dict keys that JSON
    cannot represent.
    """
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return "[CIRCULAR]"
        seen.add(marker)
        try:
            return {
                (
                    key
                    if isinstance(key, (str, int, float, bool)) or key is None
                    else str(key)
                ): _json_safe(item, seen)
                for key, item in value.items()
            }
        finally:
            seen.discard(marker)

    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen:
            return "[CIRCULAR]"
        seen.add(marker)
        try:
            return [_json_safe(item, seen) for item in value]
        finally:
            seen.discard(marker)

    return value


class JSONFormatter(RedactingFormatter, logging.Formatter):
    """JSON formatter for structured logging."""

    def __init__(self, redact_fields: Optional[Set[str]] = None):
        super().__init__(redact_fields=redact_fields)
        logging.Formatter.__init__(self)
        self._ts_cache_second = -1
        self._ts_cache_prefix = ""

    def _format_timestamp(self, created: float) -> str:
        """ISO 8601 UTC timestamp, with the whole-second part cached.

        Building a datetime and calling isoformat() per record is a measurable
        slice of formatting cost; only the sub-second tail actually changes
        between records logged in the same second.
        """
        whole = int(created)
        if whole != self._ts_cache_second:
            self._ts_cache_second = whole
            self._ts_cache_prefix = datetime.fromtimestamp(
                whole, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S")
        micros = int((created - whole) * 1_000_000)
        return f"{self._ts_cache_prefix}.{micros:06d}+00:00"

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            entry["correlation_id"] = correlation_id

        for key, value in record.__dict__.items():
            if key not in _STDLIB_LOG_FIELDS:
                entry[key] = value

        if record.exc_info:
            exc_type, exc_value = record.exc_info[0], record.exc_info[1]
            # Populate exc_text so a second handler (console + file is the
            # default when file= is set) reuses it instead of re-walking the
            # whole traceback.
            if record.exc_text is None:
                record.exc_text = self.formatException(record.exc_info)
            entry["exception"] = record.exc_text
            if exc_type is not None:
                entry["exception_type"] = exc_type.__name__
                entry["exception_message"] = str(exc_value)

        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        entry = self._redact_dict(entry)
        entry["message"] = self._redact_text(entry["message"])
        try:
            return json.dumps(
                entry, default=str, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError):
            # Self-referential structures and non-primitive dict keys are the
            # two shapes json.dumps refuses outright. Losing the record is far
            # worse than emitting a coerced version of it -- and stdlib logging
            # swallows formatter exceptions, so the line would vanish silently.
            return json.dumps(
                _json_safe(entry, set()),
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
            )


class TextFormatter(RedactingFormatter, logging.Formatter):
    """Text formatter with colors."""

    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def __init__(
        self,
        redact_fields: Optional[Set[str]] = None,
        use_colors: Optional[bool] = None,
        stream: Optional[Any] = None,
    ):
        super().__init__(redact_fields=redact_fields)

        if use_colors is None:
            use_colors = _should_use_colors(
                stream if stream is not None else sys.stderr
            )

        self.use_colors = use_colors
        if use_colors:
            _init_colorama()
        logging.Formatter.__init__(self)

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]

        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, "")
            level = f"{color}{level:8}{Style.RESET_ALL}"
        else:
            level = f"{level:8}"

        message = self._redact_text(record.getMessage())

        correlation_part = ""
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            correlation_part = f" [cid={correlation_id}]"

        # Redact structurally, then render. Regexing the finished line instead
        # meant a full IGNORECASE sub over every record, and missed secrets
        # nested inside dict/list values.
        extras = []
        raw_extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STDLIB_LOG_FIELDS
        }
        if raw_extras:
            for key, value in self._redact_dict(raw_extras).items():
                extras.append(f"{key}={value}")

        extra_part = " " + " ".join(extras) if extras else ""
        name_part = f"{record.name}{correlation_part}"
        formatted = f"{timestamp} {level} {name_part}: {message}{extra_part}"

        if record.exc_info:
            if record.exc_text is None:
                record.exc_text = self.formatException(record.exc_info)
            formatted += "\n" + record.exc_text

        if record.stack_info:
            formatted += "\n" + self.formatStack(record.stack_info)

        return formatted
