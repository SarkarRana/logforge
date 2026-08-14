# Configuration reference

All knobs are settable either as `get_logger(...)` keyword arguments or via environment variables. Code-level arguments always win.

## Core options

| Argument | Env var | Type | Default | Description |
|---|---|---|---|---|
| `level` | `LOGCORE_LEVEL` | `str` | `"INFO"` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `json` | `LOGCORE_JSON` | `bool` | `False` | `True` for structured JSON output; `False` for colored human-readable text. |
| `file` | `LOGCORE_FILE` | `str \| None` | `None` | Path to a log file. When set, a rotating file handler is added alongside the console handler. |
| `correlation_id` | `LOGCORE_CORRELATION_ID` | `str \| None` | `None` | Initial correlation ID set at logger creation. |
| `max_file_size` | `LOGCORE_MAX_FILE_SIZE` | `int` (bytes) | `10485760` (10 MB) | File rotation threshold. |
| `backup_count` | `LOGCORE_BACKUP_COUNT` | `int` | `5` | Number of rotated files to keep. |
| `redact_fields` | `LOGCORE_REDACT_FIELDS` | `set[str]` | See [redaction](guides/redaction.md) | Fields whose values are partially masked. |

## Output and delivery options

| Argument | Env var | Type | Default | Description |
|---|---|---|---|---|
| `console` | `LOGCORE_CONSOLE` | `bool` | `True` | Emit to the console. Set `False` with `file=` for file-only logging. |
| `console_stream` | `LOGCORE_CONSOLE_STREAM` | `str` | `"stderr"` | `"stderr"` or `"stdout"`. |
| `propagate` | `LOGCORE_PROPAGATE` | `bool` | `False` | Forward records to the stdlib root logger. Leave off unless you want LogCore records handled a second time by root handlers. |
| `async_logging` | `LOGCORE_ASYNC` | `bool` | `False` | Move handler I/O to a background thread so log calls never block on disk or stderr. |
| `queue_size` | `LOGCORE_QUEUE_SIZE` | `int` | `10000` | Bound on the async queue. When full, new records are dropped rather than blocking the caller; see {func}`~logcore.handlers.dropped_record_count`. |

```{warning}
With `async_logging=True`, records sit in a queue until a background thread
writes them. Call {func}`logcore.shutdown` before a hard exit
(`os._exit`, `SIGKILL` grace periods) — the `atexit` hook does not run in
those cases and buffered records are lost.
```

## Sampling options

| Argument | Env var | Type | Default | Description |
|---|---|---|---|---|
| `sampler` | — | `Sampler \| None` | `None` | A fully constructed {class}`~logcore.sampling.Sampler` instance. |
| `sample_rate` | `LOGCORE_SAMPLE_RATE` | `float` | — | Shortcut: equivalent to `sampler=Sampler(rate=sample_rate)`. |
| — | `LOGCORE_SAMPLE_TAIL` | `bool` | `False` | Enable tail-based sampling via env var. |
| — | `LOGCORE_SAMPLE_BUFFER_SIZE` | `int` | `100` | Max records buffered per correlation_id. |
| — | `LOGCORE_SAMPLE_ALWAYS_KEEP` | `str` | `WARNING,ERROR,CRITICAL` | Comma-separated level names that are never sampled. |

```{important}
You can pass `sampler=` or `sample_rate=`, but not both — that raises `ValueError`. The env-var path constructs a single `Sampler` from any combination of the `LOGCORE_SAMPLE_*` vars.
```

## Boolean parsing for env vars

Env vars expecting booleans accept `true`, `1`, `yes`, `on` for true and `false`, `0`, `no`, `off` for false (case-insensitive).

## Invalid environment values

Since 0.1.7, an unparseable `LOGCORE_*` value emits a `UserWarning` and falls back to the default rather than being silently ignored:

```text
UserWarning: Ignoring invalid LOGCORE_SAMPLE_RATE='0.1x': expected a float in
[0.0, 1.0]. Using the default.
```

This matters most for sampling: a typo previously meant shipping 100% of your logs with nothing to indicate why.

## Logger caching and reconfiguration

`get_logger(name)` returns a cached instance per name. Calling it again with **no** configuration arguments returns the same cached logger. Calling it with **any** configuration argument creates a new logger and replaces the cached one — and emits a `UserWarning`:

```text
UserWarning: Logger 'myapp' already exists and is being replaced with new
configuration. Existing references to the old logger will no longer receive
log records.
```

If you see this warning, you probably want to either configure the logger once at startup or use a different name for the second instance.

## Reading the current correlation ID without a logger

```python
from logcore import get_correlation_id, set_correlation_id

set_correlation_id("req-abc")
print(get_correlation_id())  # 'req-abc'
```

These work without instantiating a logger — useful for middleware that runs before any logger is created.
