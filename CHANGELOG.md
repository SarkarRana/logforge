# Changelog

All notable changes to LogCore are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.7] - 2026-08-15

Performance, correctness and ecosystem release. Two security fixes, a ~2.6x
speedup on the text path, and the integration points needed to make LogCore the
logger for a whole process rather than only for the lines you write yourself.

### Security
- **Nested secrets are no longer logged in cleartext.** Redaction only ever
  matched top-level keys, because `_log()` stringified dicts and lists before
  any formatter saw them — so `logger.info("login", user={"password": "..."})`
  emitted the password verbatim in JSON mode despite `password` being a default
  redact field. Redaction is now structural and recurses through dicts, lists
  and tuples at any depth (bounded by a depth and node cap).
- **Message bodies are now redacted in JSON mode.** `logger.info("password=hunter2")`
  was masked by `TextFormatter` but passed through `JSONFormatter` untouched, so
  switching to `json=True` for production silently weakened redaction.

### Added
- `configure_stdlib()` routes the stdlib root logger through LogCore's
  formatters, so third-party output (uvicorn, sqlalchemy, requests, celery)
  is formatted consistently with your own. Includes a `quiet=` shortcut for
  turning down noisy libraries, plus `reset_stdlib()`.
- `dict_config_formatter()` for applications that configure logging through
  `logging.config.dictConfig`.
- `CorrelationIdMiddleware` (ASGI) and `WSGICorrelationIdMiddleware` (WSGI):
  zero-dependency correlation-ID propagation for FastAPI, Starlette, Flask and
  Django. Adopts `X-Request-ID` or the W3C `traceparent` trace-id, echoes it on
  the response, and closes out the correlation scope — which also releases
  tail-sampling buffers.
- Opt-in non-blocking logging via `async_logging=True` (`LOGCORE_ASYNC`), moving
  handler I/O onto a background thread with a bounded, load-shedding queue.
  Adds `logcore.flush()`, `logcore.shutdown()`, `logcore.dropped_record_count()`
  and `LogCoreLogger.flush()`.
- `exception_type` and `exception_message` fields alongside the existing
  `exception` string, for grouping in Elasticsearch/Datadog/Sentry.
- `console=False` to disable console output, and `console_stream="stdout"` for
  containers that want logs on stdout.
- `stacklevel=` on log calls, for user-written logging wrappers.
- `Sampler(max_active_buffers=...)` and a `evicted_buffers` stat.
- Much larger public API: `JSONFormatter`, `TextFormatter`, `LogCoreLogger`,
  `LogCoreConfig`, `Timer`, `AsyncTimer`, `correlation_id_context`,
  `generate_correlation_id`, `Decision` and `SamplerStats` are now exported from
  the package root. Previously users had to reach into private modules to
  annotate the type `get_logger` returns.
- New env vars: `LOGCORE_PROPAGATE`, `LOGCORE_CONSOLE`, `LOGCORE_CONSOLE_STREAM`,
  `LOGCORE_ASYNC`, `LOGCORE_QUEUE_SIZE`.
- Python 3.13 in the CI matrix and classifiers; macOS and Windows CI jobs.

### Changed
- **Dict and list extras now serialize as real JSON** instead of Python-repr
  strings. `{"user": "{'name': 'bob'}"}` becomes `{"user": {"name": "bob"}}`.
  The old form could not be indexed by any log pipeline. Anything parsing those
  repr strings needs updating.
- **Records no longer propagate to the root logger.** Previously every LogCore
  record was printed twice as soon as anything in the process called
  `logging.basicConfig()`. Pass `propagate=True` to restore the old behavior.
- **Extras colliding with `LogRecord` attributes are emitted as `key_` instead
  of being silently dropped.** `name`, `module`, `filename`, `process`, `thread`
  and ~18 other reserved words previously vanished with no error. Warns once per
  key.
- Invalid `LOGCORE_*` environment values now emit a `UserWarning` and fall back
  to the default, instead of being silently ignored. A typo'd
  `LOGCORE_SAMPLE_RATE` used to mean shipping 100% of logs with nothing to
  point at.
- Redacted values in text output render as `password=my***` rather than
  `password="my***"`, matching how every other extra field is rendered.
- `Timer`/`AsyncTimer` failures now capture a full traceback via `exc_info`
  instead of a bare `exception=str(exc)` field, and attribute the record to
  user code rather than to `logcore/utils.py`.
- `colorama.init()` is no longer called at import time; it runs on first use of
  a coloured formatter. Colour selection honors `NO_COLOR`, `FORCE_COLOR` and
  `TERM=dumb`.
- Coverage now enforces an 85% floor with branch coverage enabled.

### Fixed
- **~2.6x faster text logging** (25.8 → 9.7 µs/call) and ~20% faster JSON
  (12.0 → 9.7 µs/call), measured by `examples/benchmark.py`. Two causes:
  `_find_caller` called `os.path.abspath` per stack frame — a `getcwd` syscall
  each time — to compute a value neither formatter ever emitted; and
  `TextFormatter` ran a 13-branch `IGNORECASE` regex substitution over every
  rendered line.
- **Unbounded memory growth in tail-based sampling.** `Sampler` tracked
  correlation IDs in dicts that were never evicted, so any service using
  `set_correlation_id()` (rather than the `with_correlation_id()` context
  manager) leaked one buffer per request indefinitely. Now LRU-capped at
  `max_active_buffers` (default 1000).
- Sampling decisions are made before the record is built, so a dropped record no
  longer pays for frame inspection, `makeRecord`, the OpenTelemetry span lookup
  or field coercion. Sampler lock acquisitions per record went from 2–3 to 1.
- File handlers no longer receive colour settings derived from
  `sys.stderr.isatty()`, which wrote raw ANSI escape codes into log files when
  running from a terminal with colorama installed.
- Reconfiguring a logger now closes its old handlers instead of dropping the
  references, which leaked a file descriptor each time.
- Exception tracebacks are formatted once and cached on `record.exc_text`
  instead of being re-walked by every attached handler.
- `logger.exception(msg, exc_info=...)` no longer ignores an explicitly passed
  `exc_info`, and passing an exception instance now works.
- `record.stack_info` is rendered by both formatters instead of being dropped.
- Payload shapes that `json.dumps` rejects — self-referential structures and
  non-primitive dict keys — no longer cause the record to be dropped. Because
  stdlib logging swallows formatter exceptions, these would have vanished
  silently; they now serialize with `[CIRCULAR]` and stringified keys.
- Logging a `namedtuple` no longer raises inside the formatter.
- `__version__` is read from installed package metadata. It was hardcoded to
  `"0.1.5"` while `pyproject.toml` said `0.1.6`, so the published 0.1.6 wheel
  reported the wrong version and the docs site rendered the wrong release.
- The release workflow no longer publishes to PyPI without running tests, lint
  or type checks first, and it verifies the tag matches the package version.
  The duplicate publish job in `ci.yml` — which raced the release workflow for
  the same version and guaranteed one red run per release — has been removed.
- The `security-scan` CI job actually reports now; both tools ran with
  `|| true` and the report was never surfaced, so it could not fail.
- Docs are built on pull requests, not only after merge to `main`.
- Deleted `tests/pytest.ini`, which shadowed the pyproject pytest config and
  silently disabled coverage and asyncio settings for `pytest tests/`.

### Notes
- The PyPI publish step still uses a long-lived `PYPI_API_TOKEN`. Migrating to
  Trusted Publishing (OIDC) is recommended and requires a one-time publisher
  entry on PyPI first, so it was left for a follow-up.

## [0.1.6] - 2026-05-27

### Added
- **Log sampling.** New `Sampler` class combining rate-based, level-aware, and tail-based strategies. Tail-based mode buffers records under the current correlation_id and flushes them on the first `always_keep` record (default WARNING/ERROR/CRITICAL), so failed requests get full history while successful ones cost nothing. Buffer is bounded per correlation_id to prevent unbounded memory growth.
- `Sampler` exported from top-level `logcore` package.
- `get_logger(..., sampler=...)` and `get_logger(..., sample_rate=...)` shortcuts.
- `LogCoreLogger.flush_sample_buffer(cid)` for users who set correlation_id directly without the context manager.
- Environment variables: `LOGCORE_SAMPLE_RATE`, `LOGCORE_SAMPLE_TAIL`, `LOGCORE_SAMPLE_BUFFER_SIZE`, `LOGCORE_SAMPLE_ALWAYS_KEEP`.
- GitHub issue templates (`bug_report.md`, `feature_request.md`) and a pull request template under `.github/`.
- `.flake8` config so local `flake8 logcore tests` matches CI (`max-line-length=88`).

### Changed
- Minimum supported Python version raised from 3.8 to 3.9. CI stopped running the 3.8 matrix entry; the classifier and `requires-python` now reflect that.
- `LogCoreLogger.with_correlation_id()` now discards any tail-buffered records on clean exit.
- Removed the `Documentation` project URL that pointed back to the README; it will return once a dedicated docs site is published.

## [0.1.5] - 2026-05-20

### Fixed
- `TextFormatter` now applies the same partial-masking logic (`se***`) as `JSONFormatter` for redacted fields. Previously it emitted a flat `[REDACTED]` string, contradicting documented behavior.
- `TextFormatter` no longer emits stdlib `LogRecord` internals (`exc_info`, `exc_text`, `stack_info`, `taskName`) as extra key=value pairs on every line.
- `LogRecord.filename`, `lineno`, and `funcName` are now populated with the real call site instead of the hardcoded placeholder `"(unknown file)"` / `0`.
- `exc_info != True` guard in both formatters replaced with a plain truthiness check — the previous form was a no-op (a tuple is never `== True`) and could mask edge cases.

### Changed
- `skip_fields` de-duplicated into a single module-level `_STDLIB_LOG_FIELDS` frozenset shared by both `JSONFormatter` and `TextFormatter`. Adding a field to suppress now only requires one change.
- `get_logger` emits a `UserWarning` when called with configuration arguments for a name that already has a cached logger. Previously the replacement was silent, making it easy to create dangling references.

## [0.1.4] - 2025-01-15

### Added
- OpenTelemetry integration: `trace_id` and `span_id` are automatically injected into log records when an active span exists. Zero configuration required; install `logcore[otel]` to enable.
- `AsyncTimer` context manager for `async with logger.time(...)` in asyncio applications.
- `is_async_context()` utility to auto-detect running event loop.
- Partial masking for redacted fields: values longer than 4 characters show a short prefix (e.g. `se***`) instead of a blanket `[REDACTED]`, making it possible to correlate log lines without leaking secrets.
- `logcore/py.typed` marker for PEP 561 compliance.
- `examples/benchmark.py` with measured throughput numbers.

### Changed
- JSON formatter timestamps now emit UTC with timezone offset (`+00:00`) for unambiguous parsing by log aggregators.
- `is_async_context()` now uses `asyncio.get_running_loop()` (raises `RuntimeError` when no loop is running) instead of `asyncio.current_task()` (returned `None` in non-async contexts, making detection unreliable).
- `Optional[Set[str]]` type annotation used consistently across `LogCoreConfig`, `JSONFormatter`, and `TextFormatter` — removes false mypy errors under strict mode.
- Removed redundant `LogLevel.WARN` enum member; `LogLevel.from_string("WARN")` continues to normalise to `LogLevel.WARNING`.

### Fixed
- Logger caching lock now uses `threading.RLock` to prevent deadlocks when `get_logger` is called recursively from within a handler.

## [0.1.3] - 2024-12-01

### Changed
- Complete migration from `logforge` to `logcore` package name.
- Updated all internal references, classifiers, and PyPI metadata.

## [0.1.2] - 2024-11-15

### Added
- Environment variable configuration via `LOGCORE_*` prefix.
- `LOGCORE_REDACT_FIELDS` env var accepts a comma-separated list.
- File logging with `RotatingFileHandler`; configurable `max_file_size` and `backup_count`.
- `with_correlation_id()` context manager using `contextvars` for per-task isolation in async code.

### Changed
- `get_logger` returns a cached instance per name; passing configuration parameters forces a new instance.

## [0.1.1] - 2024-10-20

### Added
- `logger.time()` context manager that logs start, completion, and `duration_ms`.
- `logger.exception()` convenience method that captures the current traceback.
- Colorama-based coloured output in `TextFormatter`; falls back gracefully when colorama is absent.

## [0.1.0] - 2024-10-01

### Added
- Initial release as `logforge`.
- `get_logger(name)` single-entrypoint API.
- `JSONFormatter` and `TextFormatter`.
- `RedactingFormatter` base class with configurable sensitive-field redaction.
- Thread-safe logger registry.
- MIT license.

[Unreleased]: https://github.com/SarkarRana/logcore/compare/v0.1.7...HEAD
[0.1.7]: https://github.com/SarkarRana/logcore/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/SarkarRana/logcore/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/SarkarRana/logcore/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/SarkarRana/logcore/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/SarkarRana/logcore/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/SarkarRana/logcore/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/SarkarRana/logcore/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SarkarRana/logcore/releases/tag/v0.1.0
