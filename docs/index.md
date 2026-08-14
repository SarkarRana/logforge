# LogCore

**A production-ready structured logging library for Python.**

LogCore wraps Python's stdlib `logging` with a single ergonomic entrypoint, structured JSON output, async-safe correlation IDs, automatic sensitive-data redaction, OpenTelemetry integration, and intelligent log sampling — all with **zero required dependencies**.

```python
from logcore import get_logger

log = get_logger("myapp", json=True)
log.info("user login", user="alice", role="admin")
```

```{toctree}
:maxdepth: 2
:caption: Getting started

quickstart
```

```{toctree}
:maxdepth: 2
:caption: Guides

guides/correlation_ids
guides/sampling
guides/opentelemetry
guides/frameworks
guides/stdlib_interop
guides/redaction
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
configuration
changelog
```

## At a glance

- **Single entrypoint.** `get_logger(name)` returns a configured, cached logger — no `dictConfig`, no handler/formatter wiring.
- **Structured by default.** JSON output mode produces records that log aggregators can query directly.
- **Async-safe correlation IDs.** Per-task isolation via `contextvars`.
- **Automatic redaction.** Sensitive fields (passwords, tokens, keys) are partially masked — enough to confirm a value was present without leaking it.
- **OpenTelemetry-aware.** `trace_id` and `span_id` injected automatically when a span is active.
- **Tail-based sampling.** Buffer records per request, flush them on error, drop them on success. Pay nothing for healthy traffic; keep everything when something breaks.
- **Strict typing.** PEP 561–compliant `py.typed` marker; mypy-clean.

## Install

```bash
pip install logcore
```

Optional extras:

```bash
pip install "logcore[colors]"  # colored console output
pip install "logcore[otel]"    # OpenTelemetry integration
```

## Where to go next

- New to LogCore? Start with the [**quickstart**](quickstart.md).
- High-volume service? See [**log sampling**](guides/sampling.md).
- Need request-scoped logging? See [**correlation IDs**](guides/correlation_ids.md).
- Looking for a specific function? See the [**API reference**](api.md).
