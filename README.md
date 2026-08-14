# LogCore 🔥

[![PyPI version](https://badge.fury.io/py/logcore.svg)](https://badge.fury.io/py/logcore)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/logcore?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/logcore)
[![Python versions](https://img.shields.io/pypi/pyversions/logcore.svg)](https://pypi.org/project/logcore/)
[![CI](https://github.com/SarkarRana/logcore/actions/workflows/ci.yml/badge.svg)](https://github.com/SarkarRana/logcore/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/SarkarRana/logcore/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-sarkarrana.github.io/logcore-blue.svg)](https://sarkarrana.github.io/logcore/)


**A production-ready logging library for Python**

📖 **Full documentation:** [sarkarrana.github.io/logcore](https://sarkarrana.github.io/logcore/)

LogCore provides a simple, structured, and extensible logging solution that works seamlessly for both small scripts and large microservices. It's designed as a drop-in alternative to Python's built-in logging with a focus on developer experience, observability, and production readiness.

## ✨ Features

- **🚀 Simple API**: Single entrypoint with intuitive configuration
- **📊 Structured Logging**: JSON and human-readable output formats; nested dicts and lists stay real JSON
- **🔗 Correlation IDs**: Built-in request tracing, with ASGI/WSGI middleware included
- **⏱️ Built-in Timing**: Context managers for performance monitoring
- **🛡️ Security**: Recursive redaction of sensitive fields, at any nesting depth
- **🔌 stdlib Interop**: Capture third-party library logs through the same formatters
- **📁 File Rotation**: Configurable log rotation and archival
- **🎨 Colorized Output**: Beautiful console logging with colors (honors `NO_COLOR`)
- **⚡ Async Support**: Safe for asyncio applications, with opt-in non-blocking delivery
- **🧵 Thread-safe**: Concurrent logging without issues
- **🌍 Environment Configuration**: Configure via environment variables

## 🚀 Quick Start

### Installation

```bash
pip install logcore
```

For colored output support:

```bash
pip install logcore[colors]
```

### Basic Usage

```python
from logcore import get_logger

# Create a logger
log = get_logger("myapp", level="INFO", json=True)

# Simple logging
log.info("Application started")
log.error("Something went wrong")

# Structured logging with extra fields
log.info("User login", user="alice", role="admin", success=True)

# Exception logging with automatic traceback
try:
    1 / 0
except Exception:
    log.exception("Division failed")
```

## 📖 Documentation

### Configuration Options

LogCore can be configured through code or environment variables:

```python
from logcore import get_logger

log = get_logger(
    name="myapp",              # Logger name
    level="INFO",              # DEBUG, INFO, WARNING, ERROR, CRITICAL
    json=True,                 # JSON output (False for human-readable)
    file="/path/to/app.log",   # Optional file logging
    correlation_id="req-123",  # Optional correlation ID
    max_file_size=10*1024*1024, # 10MB file size limit
    backup_count=5,            # Keep 5 backup files
    redact_fields={"password", "secret"}  # Fields to redact
)
```

Calling `get_logger` with the same name a second time and no extra arguments returns the cached instance. Passing configuration arguments when a logger already exists replaces it and emits a `UserWarning` — existing references to the old logger will stop receiving records.

### Public API

```python
from logcore import (
    get_logger, LogCoreLogger, LogLevel, LogCoreConfig,
    set_correlation_id, get_correlation_id, correlation_id_context,
    Timer, AsyncTimer,
    JSONFormatter, TextFormatter,
    Sampler, SamplerStats, Decision,
    configure_stdlib, reset_stdlib, dict_config_formatter,
    CorrelationIdMiddleware, WSGICorrelationIdMiddleware,
    flush, shutdown, dropped_record_count,
)
```

| Symbol | Description |
|---|---|
| `get_logger(name, ...)` | Create or retrieve a logger |
| `LogCoreLogger` | The type `get_logger` returns — for type annotations |
| `LogLevel` | Enum of valid log levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `Sampler(rate, always_keep, tail_based, ...)` | Configurable sampler combining rate-based, level-aware, and tail-based sampling |
| `set_correlation_id(id)` | Set a correlation ID on the current context (thread/task) without a logger instance |
| `get_correlation_id()` | Read the current correlation ID, or `None` if unset |
| `correlation_id_context(id)` | Context manager binding an ID for a scope, with proper reset |
| `JSONFormatter` / `TextFormatter` | Standard `logging.Formatter` subclasses, usable in `dictConfig` |
| `configure_stdlib(...)` | Route the stdlib root logger through LogCore's formatters |
| `dict_config_formatter(...)` | A `dictConfig` formatter entry for LogCore |
| `CorrelationIdMiddleware` | ASGI correlation-ID middleware |
| `WSGICorrelationIdMiddleware` | WSGI correlation-ID middleware |
| `flush()` / `shutdown()` | Drain and stop background queue listeners (`async_logging=True`) |
| `dropped_record_count()` | Records shed by a full async queue |

`set_correlation_id` and `get_correlation_id` are useful in middleware that sets the ID before a logger is available:

```python
from logcore import set_correlation_id, get_correlation_id

# In ASGI/WSGI middleware, before any logger is called:
set_correlation_id(request.headers.get("x-correlation-id"))
```

### Environment Variables

Set configuration via environment variables:

```bash
export LOGCORE_LEVEL=DEBUG
export LOGCORE_JSON=true
export LOGCORE_FILE=/var/log/app.log
export LOGCORE_CORRELATION_ID=req-abc-123
export LOGCORE_REDACT_FIELDS=password,token,secret
export LOGCORE_CONSOLE_STREAM=stdout   # default: stderr
export LOGCORE_ASYNC=true              # non-blocking delivery
```

An invalid value emits a `UserWarning` and falls back to the default rather than being silently ignored — a typo'd `LOGCORE_SAMPLE_RATE` used to mean shipping 100% of your logs with nothing to indicate why.

### Output Formats

#### JSON Format

```json
{
  "timestamp": "2025-01-15T10:30:45.123456+00:00",
  "level": "INFO",
  "logger": "myapp",
  "message": "User login",
  "correlation_id": "req-123",
  "user": "alice",
  "success": true
}
```

#### Human-Readable Format

```
2025-01-15 10:30:45.123 INFO     myapp [cid=req-123]: User login user=alice success=true
```

### Advanced Features

#### Correlation IDs for Request Tracing

```python
from logcore import get_logger

log = get_logger("api")

# Set correlation ID for the entire request context
with log.with_correlation_id("req-abc-123"):
    log.info("Processing request")
    process_request()
    log.info("Request completed")
```

#### Performance Timing

```python
# Measure execution time automatically
with log.time("database_query", level="DEBUG"):
    result = expensive_database_operation()

# Outputs:
# Starting database_query
# Completed database_query duration_ms=234.56
```

#### Exception Handling

```python
try:
    risky_operation()
except Exception as e:
    log.exception("Operation failed", operation="risky_operation", user_id=123)
    # Automatically includes full traceback
```

#### Sensitive Data Redaction

Fields are **partially masked** — enough to confirm a value was present without leaking it:

```python
log = get_logger("secure", redact_fields={"password", "token", "ssn"})

log.info("User data", username="alice", password="secret123", token="abc123", role="admin")
# Output: ... username=alice password=se*** token=a*** role=admin
```

Values of 4 characters or fewer are fully redacted (`[REDACTED]`). Longer values reveal a short prefix so you can correlate log lines without exposing the secret.

Redaction is **recursive** — it descends into nested dicts and lists at any depth:

```python
log.info("login", user={"name": "bob", "password": "hunter2secret"},
         tokens=[{"token": "abcdefghij"}])
# {"user":{"name":"bob","password":"hun***"},"tokens":[{"token":"ab***"}]}
```

> **Upgrading from ≤0.1.6:** nested values were stringified before the redactor ran, so secrets inside a dict or list were logged in **cleartext**. If you log structured payloads, upgrade.

Secrets written into the message itself (`log.info("password=hunter2")`) are masked too, in both output formats.

Default redacted fields: `password`, `passwd`, `secret`, `token`, `key`, `api_key`, `access_token`, `auth`, `authorization`, `credential`, `private_key`, `cert`, `certificate`.

#### Log Sampling

For high-throughput services, emitting every record is wasteful. LogCore ships with a built-in `Sampler` that combines three strategies:

```python
from logcore import get_logger, Sampler

log = get_logger(
    "api",
    sampler=Sampler(
        rate=0.01,                                    # 1% of INFO/DEBUG
        always_keep={"WARNING", "ERROR", "CRITICAL"}, # never sampled
        tail_based=True,                              # buffer per request
        tail_buffer_size=100,                         # max records per cid
    ),
)
```

Or use the shortcut for simple rate-based sampling:

```python
log = get_logger("api", sample_rate=0.01)
```

**Tail-based sampling** is the differentiator: when a correlation_id is active, INFO/DEBUG records are buffered instead of emitted. On the first WARNING/ERROR/CRITICAL in that request, the buffer is flushed to handlers — so you get the full history of any request that fails, but pay nothing for successful requests:

```python
with log.with_correlation_id("req-abc"):
    log.info("received request")     # buffered
    log.info("validated input")      # buffered
    log.error("database timeout")    # flushes both INFOs + emits the error
    log.info("retrying")             # passes through (cid is now "interesting")
# On clean exit, any unflushed records are discarded.
```

If you set the correlation_id directly (e.g. in middleware) instead of using the context manager, call `log.flush_sample_buffer()` at request end so the buffer is cleared.

**Environment variables:**

```bash
LOGCORE_SAMPLE_RATE=0.01
LOGCORE_SAMPLE_TAIL=true
LOGCORE_SAMPLE_BUFFER_SIZE=100
LOGCORE_SAMPLE_ALWAYS_KEEP=WARNING,ERROR,CRITICAL
```

**Stats** for observability:

```python
log.sampler.stats()
# SamplerStats(active_buffers=3, buffered_records=42, dropped_overflow=0,
#              kept=120, dropped=9500, buffered=380, flushed=80)
```

#### OpenTelemetry Integration

When an active [OpenTelemetry](https://opentelemetry.io/) span exists, LogCore automatically injects `trace_id` and `span_id` into every log record — zero configuration required.

```bash
pip install logcore[otel]
```

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from logcore import get_logger

trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer("myapp")
log = get_logger("myapp", json=True)

with tracer.start_as_current_span("handle-request"):
    log.info("Processing order", order_id=42)
    # {"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    #  "span_id": "00f067aa0ba902b7", "message": "Processing order", ...}
```

Outside a span the fields are simply absent — no noise in non-traced code paths. Works with any OTel-compatible backend (Jaeger, Zipkin, Honeycomb, Datadog, etc.).

#### File Logging with Rotation

```python
log = get_logger(
    "myapp",
    file="/var/log/myapp.log",
    max_file_size=10 * 1024 * 1024,  # 10MB
    backup_count=5                    # Keep 5 old files
)
```

Files are automatically rotated:

- `myapp.log` (current)
- `myapp.log.1` (previous)
- `myapp.log.2` (older)
- etc.

### Async Support

LogCore is fully compatible with asyncio:

```python
import asyncio
from logcore import get_logger

async def main():
    log = get_logger("async_app")

    # Correlation IDs work across await boundaries
    with log.with_correlation_id():
        log.info("Starting async operation")
        await some_async_task()
        log.info("Async operation completed")

    # Async timing context manager
    async with log.time("async_operation"):
        await another_async_task()

asyncio.run(main())
```

### Capturing Third-Party Logs

`get_logger` only formats the records *you* emit. Everything uvicorn, sqlalchemy, requests or celery logs goes through the stdlib root logger, so in a JSON pipeline half your output is unparseable. `configure_stdlib()` fixes that in one call at startup:

```python
import logcore

logcore.configure_stdlib(
    level="INFO",
    json=True,
    quiet=["urllib3", ("botocore", "ERROR")],  # turn down noisy libraries
)
```

Now every record in the process — yours and everyone else's — uses the same format and the same redaction rules.

Configuring logging declaratively instead? `logcore.dict_config_formatter()` returns a `dictConfig` formatter entry.

### Integration with Web Frameworks

The middleware is zero-dependency — it imports no web framework.

#### FastAPI / Starlette (ASGI)

```python
from fastapi import FastAPI
from logcore import CorrelationIdMiddleware, get_logger

log = get_logger("api", json=True)
app = FastAPI()
app.add_middleware(CorrelationIdMiddleware, logger=log)

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    log.info("Fetching user", user_id=user_id)  # carries the request's ID
    return {"id": user_id}
```

#### Flask / Django (WSGI)

```python
from flask import Flask
from logcore import WSGICorrelationIdMiddleware, get_logger

log = get_logger("webapp", json=True)
app = Flask(__name__)
app.wsgi_app = WSGICorrelationIdMiddleware(app.wsgi_app, logger=log)
```

Both adopt an inbound `X-Request-ID` (falling back to the W3C `traceparent` trace-id, or generating a UUID), bind it for the request, echo it on the response, and release the correlation scope on the way out. Passing `logger=` is what releases tail-sampling buffers — without it a service using tail-based sampling accumulates one buffer per request.

Inbound IDs are validated against `[A-Za-z0-9._:-]{1,128}`, so a client cannot inject newlines into your logs or response headers.

### Non-Blocking Logging

By default a log call writes and flushes on the calling thread. To move that off the hot path:

```python
log = get_logger("api", json=True, async_logging=True)  # or LOGCORE_ASYNC=true
```

Handler I/O runs on a background thread behind a bounded queue. When the queue is full, records are dropped rather than blocking the caller — `logcore.dropped_record_count()` reports how many. Call `logcore.shutdown()` before a hard exit; the `atexit` hook only covers normal termination.

## ⚡ Performance

Measured on Python 3.13, Apple M-series, writing to `/dev/null` (I/O excluded):

| Mode | µs / call | Notes |
|---|---|---|
| stdlib `logging` (text) | ~4.6 µs | baseline |
| stdlib + manual JSON formatter | ~5.5 µs | +0.9 µs |
| **LogCore JSON** | **~9.7 µs** | +5.1 µs for structured output |
| LogCore text | ~9.7 µs | +5.1 µs |

v0.1.7 made the text path ~2.6x faster (25.8 → 9.9 µs) and JSON ~18% faster (12.0 → 9.8 µs). The dominant cost was the text formatter running a 13-branch case-insensitive regex substitution over every rendered line — worth ~13.9 µs of the ~15.9 µs saved, and the reason text output used to be *slower* than JSON. Smaller wins came from dropping a per-frame `os.path.abspath` (a `getcwd` syscall) that computed a value neither formatter emitted (~1 µs), plus timestamp caching and one less dict allocation per record.

The remaining ~5 µs over stdlib buys correlation IDs, sampling, structured field handling and recursive redaction. If you need to shed it on a hot path, `async_logging=True` moves handler I/O off the calling thread.

Run the benchmark yourself: `python examples/benchmark.py`

## 🆚 Comparison with Other Libraries

### vs. Built-in `logging`

| Feature            | LogCore                   | Built-in logging             |
| ------------------ | ------------------------- | ---------------------------- |
| Setup complexity   | ⭐⭐⭐⭐⭐ Single line    | ⭐⭐ Complex setup           |
| Structured logging | ⭐⭐⭐⭐⭐ Built-in       | ⭐⭐ Manual implementation   |
| JSON output        | ⭐⭐⭐⭐⭐ Automatic      | ⭐⭐ Custom formatter needed |
| Correlation IDs    | ⭐⭐⭐⭐⭐ Built-in       | ⭐ Custom context needed     |
| Security           | ⭐⭐⭐⭐⭐ Auto-redaction | ⭐ Manual filtering          |
| Colors             | ⭐⭐⭐⭐⭐ Auto-detected  | ⭐⭐ Third-party needed      |

### vs. `loguru`

| Feature          | LogCore                     | Loguru                         |
| ---------------- | --------------------------- | ------------------------------ |
| Production focus | ⭐⭐⭐⭐⭐ Enterprise-ready | ⭐⭐⭐⭐ Great for development |
| Correlation IDs  | ⭐⭐⭐⭐⭐ Built-in context | ⭐⭐ Manual binding            |
| Security         | ⭐⭐⭐⭐⭐ Auto-redaction   | ⭐⭐ Manual filtering          |
| Async support    | ⭐⭐⭐⭐⭐ Context-aware    | ⭐⭐⭐ Basic support           |
| Performance      | ⭐⭐⭐⭐ Good               | ⭐⭐⭐⭐⭐ Excellent           |
| Ecosystem        | ⭐⭐⭐⭐⭐ Standard logging | ⭐⭐⭐ Custom approach         |

## 🛠️ Development

### Setup

```bash
git clone https://github.com/SarkarRana/logcore.git
cd logcore

# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=logcore

# Run specific test categories
pytest -m "not slow"          # Skip slow tests
pytest -m integration         # Run integration tests only
```

### Code Quality

```bash
# Format code
black logcore tests
isort logcore tests

# Lint
flake8 logcore tests

# Type checking
mypy logcore
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## 🎯 Roadmap

### Shipped
- [x] **OpenTelemetry**: Automatic trace/span ID injection from active spans (v0.1.4)
- [x] **Async support**: `AsyncTimer` with isolated correlation IDs per task (v0.1.4)
- [x] **Partial masking**: Secrets show a short prefix, not just `[REDACTED]` (v0.1.4)
- [x] **Accurate caller info**: `filename`, `lineno`, and `funcName` now reflect the real call site (v0.1.5)
- [x] **Reconfiguration warning**: `get_logger` emits `UserWarning` when replacing a cached logger (v0.1.5)
- [x] **`LogLevel`, `set_correlation_id`, `get_correlation_id`** promoted to top-level public API (v0.1.5)
- [x] **Log sampling**: Rate-based, level-aware, and tail-based sampling with per-correlation-id buffering (v0.1.6)
- [x] **stdlib interop**: `configure_stdlib()` routes third-party library logs through LogCore's formatters (v0.1.7)
- [x] **Web middleware**: Zero-dependency ASGI and WSGI correlation-ID middleware (v0.1.7)
- [x] **Non-blocking delivery**: Opt-in `async_logging=True` moves handler I/O to a background thread (v0.1.7)
- [x] **Recursive redaction**: Secrets masked at any nesting depth, in dicts and lists (v0.1.7)

### Planned
- [ ] **`logger.bind()`**: Child loggers carrying persistent context fields
- [ ] **Sentry integration**: Automatic error forwarding with structured context
- [ ] **OTLP export**: Direct log shipping to OpenTelemetry collectors
- [ ] **Kubernetes metadata**: Pod/node/namespace injection via downward API env vars
- [ ] **Per-level sample rates**: e.g. 100% ERROR, 10% INFO, 1% DEBUG

## 💖 Support

If you find LogCore useful, please consider:

- ⭐ Starring the repository
- 🐛 Reporting bugs and issues
- 💡 Suggesting new features
- 📖 Improving documentation
- 💻 Contributing code

---

**Built with ❤️ for the Python community**
