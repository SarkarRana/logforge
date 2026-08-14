# Capturing third-party logs

`get_logger` only formats the records *you* emit. Everything a library logs —
uvicorn, gunicorn, sqlalchemy, requests, celery, botocore — goes through the
standard library's root logger and comes out in whatever format that logger
happens to have.

In a JSON pipeline this is the difference between a searchable log stream and
one where half the lines are unparseable:

```text
{"timestamp":"...","level":"INFO","logger":"myapp","message":"order placed"}
INFO:     127.0.0.1:52814 - "GET /orders HTTP/1.1" 200 OK
```

## configure_stdlib

Call it once at startup, before the libraries you want to capture emit anything:

```python
import logcore

logcore.configure_stdlib(
    level="INFO",
    json=True,
    quiet=["urllib3", ("botocore", "ERROR")],
)
```

Now every record in the process — yours and everyone else's — is formatted the
same way and redacted by the same rules:

```text
{"timestamp":"...","level":"INFO","logger":"myapp","message":"order placed"}
{"timestamp":"...","level":"INFO","logger":"uvicorn.access","message":"127.0.0.1:52814 - \"GET /orders HTTP/1.1\" 200 OK"}
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `level` | `"INFO"` | Root log level. |
| `json` | `True` | JSON or human-readable text. |
| `file` | `None` | Optional rotating file to write as well. |
| `replace_existing` | `True` | Remove handlers already on the root logger. |
| `redact_fields` | `None` | Field names to mask; defaults to LogCore's built-in set. |
| `quiet` | `None` | Loggers to turn down — a name (raised to `WARNING`) or a `(name, level)` pair. |
| `async_logging` | `False` | Move handler I/O to a background thread. |

```{note}
Leave `replace_existing=True` unless you know you need otherwise. If something
already called `logging.basicConfig()`, leaving its handler attached means every
line is emitted twice.
```

## Using dictConfig instead

For applications that configure logging declaratively:

```python
import logging.config
import logcore

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"logcore": logcore.dict_config_formatter(json=True)},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "logcore"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
})
```

`JSONFormatter` and `TextFormatter` are ordinary `logging.Formatter` subclasses,
so they work anywhere the stdlib accepts a formatter.

## Correlation IDs across a request

The web middleware binds a correlation ID for the lifetime of each request, so
every record emitted while handling it — including those from third-party
libraries captured above — carries the same ID.

### ASGI (FastAPI, Starlette, Litestar, Quart)

```python
from fastapi import FastAPI
from logcore import CorrelationIdMiddleware, get_logger

log = get_logger("api", json=True)
app = FastAPI()
app.add_middleware(CorrelationIdMiddleware, logger=log)
```

### WSGI (Flask, Django)

```python
from flask import Flask
from logcore import WSGICorrelationIdMiddleware, get_logger

log = get_logger("api", json=True)
app = Flask(__name__)
app.wsgi_app = WSGICorrelationIdMiddleware(app.wsgi_app, logger=log)
```

Both middlewares:

1. Adopt an inbound `X-Request-ID`, falling back to the trace-id from a W3C
   `traceparent` header, or generate a UUID.
2. Bind it for the duration of the request, so it appears on every log record.
3. Echo it back on the response, so a client can quote it in a bug report.
4. Release the correlation scope on the way out.

```{important}
Passing `logger=` is what makes step 4 release tail-sampling buffers. Without
it, a service using tail-based sampling accumulates one buffer per request. See
[sampling](sampling.md).
```

Inbound IDs are validated against `[A-Za-z0-9._:-]{1,128}` and rejected
otherwise, so a client cannot inject newlines into your log stream or your
response headers.

## Non-blocking delivery

By default a log call writes and flushes on the calling thread. For services
where that latency matters:

```python
log = get_logger("api", json=True, async_logging=True)
```

Handler I/O moves to a background thread behind a bounded queue. When the queue
is full, records are dropped rather than blocking the caller —
{func}`~logcore.handlers.dropped_record_count` reports how many.

Call {func}`logcore.shutdown` before a hard exit; the `atexit` hook covers
normal termination only.
