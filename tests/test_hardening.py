"""Regression tests for LogCore hardening work (introduced in v0.1.7).

Grouped by the defect each test pins down, so a failure names the regression
rather than just the assertion.
"""

import io
import json
import logging
import os
import sys
import tempfile
import warnings
from typing import Any, Dict, List, Optional, Tuple

import pytest

from logcore import (
    CorrelationIdMiddleware,
    JSONFormatter,
    Sampler,
    TextFormatter,
    WSGICorrelationIdMiddleware,
    configure_stdlib,
    get_logger,
    reset_stdlib,
)
from logcore.logger import _loggers, _warned_reserved_keys

from .conftest import close_logcore_handlers


def _json_logger(name: str, **kwargs: Any) -> Tuple[Any, io.StringIO]:
    """Return a JSON logger writing to an in-memory buffer."""
    _loggers.clear()
    buffer = io.StringIO()
    log = get_logger(name, level="DEBUG", json=True, **kwargs)
    for handler in log._logger.handlers:
        handler.stream = buffer  # type: ignore[attr-defined]
    return log, buffer


def _last_json(buffer: io.StringIO) -> Dict[str, Any]:
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines, "no log output captured"
    return json.loads(lines[-1])


class TestStructuredValues:
    """Dict/list extras must survive to the output as real JSON."""

    def test_dict_extra_is_json_object(self) -> None:
        log, buffer = _json_logger("struct1")
        log.info("event", payload={"a": 1, "nested": {"b": 2}})

        data = _last_json(buffer)
        assert data["payload"] == {"a": 1, "nested": {"b": 2}}

    def test_list_extra_is_json_array(self) -> None:
        log, buffer = _json_logger("struct2")
        log.info("event", items=[1, 2, {"c": 3}])

        data = _last_json(buffer)
        assert data["items"] == [1, 2, {"c": 3}]

    def test_opaque_object_still_stringified(self) -> None:
        class Widget:
            def __str__(self) -> str:
                return "widget-7"

        log, buffer = _json_logger("struct3")
        log.info("event", widget=Widget())

        assert _last_json(buffer)["widget"] == "widget-7"

    def test_non_serializable_inside_dict_does_not_raise(self) -> None:
        log, buffer = _json_logger("struct4")
        log.info("event", payload={"when": object()})

        assert "payload" in _last_json(buffer)

    def test_circular_reference_does_not_lose_the_record(self) -> None:
        """stdlib logging swallows formatter errors, so a raise = a lost line."""
        payload: Dict[str, Any] = {"a": 1}
        payload["self"] = payload

        # redact_fields=set() disables the redaction walk, whose depth cap
        # would otherwise break the cycle before json.dumps sees it.
        log, buffer = _json_logger("circ1", redact_fields=set())
        log.info("circ", payload=payload)

        data = _last_json(buffer)
        assert data["payload"]["a"] == 1
        assert data["payload"]["self"] == "[CIRCULAR]"

    def test_unserializable_dict_keys_do_not_lose_the_record(self) -> None:
        log, buffer = _json_logger("keys1")
        log.info("k", payload={1: "one", (2, 3): "tuple-key"})

        data = _last_json(buffer)
        assert data["payload"]["1"] == "one"
        assert data["payload"]["(2, 3)"] == "tuple-key"

    def test_namedtuple_does_not_crash_the_formatter(self) -> None:
        """Rebuilding a tuple subclass via type(v)(items) breaks namedtuples."""
        import collections

        Point = collections.namedtuple("Point", "x y")

        log, buffer = _json_logger("struct5")
        log.info("event", point=Point(1, 2))

        assert _last_json(buffer)["point"] == [1, 2]

    def test_tuple_stays_a_json_array(self) -> None:
        log, buffer = _json_logger("struct6")
        log.info("event", pair=(1, "two"))

        assert _last_json(buffer)["pair"] == [1, "two"]

    def test_secret_inside_namedtuple_is_masked(self) -> None:
        import collections

        Cred = collections.namedtuple("Cred", "user payload")

        log, buffer = _json_logger("struct7")
        log.info("event", cred=Cred("bob", {"password": "hunter2secret"}))

        assert "hunter2secret" not in buffer.getvalue()


class TestNestedRedaction:
    """The bug that motivated this release: nested secrets shipped in cleartext."""

    def test_nested_dict_secret_is_masked(self) -> None:
        log, buffer = _json_logger("redact1")
        log.info("login", user={"name": "bob", "password": "hunter2secret"})

        raw = buffer.getvalue()
        assert "hunter2secret" not in raw
        assert _last_json(buffer)["user"]["name"] == "bob"

    def test_secret_inside_list_of_dicts_is_masked(self) -> None:
        log, buffer = _json_logger("redact2")
        log.info("batch", items=[{"token": "abcdefghij"}, {"ok": 1}])

        raw = buffer.getvalue()
        assert "abcdefghij" not in raw
        assert _last_json(buffer)["items"][1] == {"ok": 1}

    def test_deeply_nested_secret_is_masked(self) -> None:
        log, buffer = _json_logger("redact3")
        log.info("deep", a={"b": {"c": {"secret": "topsecretvalue"}}})

        assert "topsecretvalue" not in buffer.getvalue()

    def test_recursion_is_depth_capped(self) -> None:
        payload: Dict[str, Any] = {"level": 0}
        cursor = payload
        for depth in range(1, 40):
            child: Dict[str, Any] = {"level": depth}
            cursor["next"] = child
            cursor = child

        log, buffer = _json_logger("redact4")
        log.info("deep", payload=payload)

        assert "[TRUNCATED]" in buffer.getvalue()

    def test_message_body_secret_masked_in_json(self) -> None:
        """JSON mode used to skip message-body redaction entirely."""
        log, buffer = _json_logger("redact5")
        log.info("auth failed password=hunter2secret retrying")

        assert "hunter2secret" not in buffer.getvalue()

    def test_message_body_secret_masked_in_text(self) -> None:
        formatter = TextFormatter(redact_fields={"password"}, use_colors=False)
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="using password=hunter2secret now",
            args=(),
            exc_info=None,
        )

        assert "hunter2secret" not in formatter.format(record)

    def test_message_without_separator_is_untouched(self) -> None:
        """The fast path must not corrupt ordinary prose."""
        formatter = JSONFormatter(redact_fields={"password"})
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="the password was rotated",
            args=(),
            exc_info=None,
        )

        assert json.loads(formatter.format(record))["message"] == (
            "the password was rotated"
        )


class TestReservedFieldCollisions:
    """Colliding extras were silently dropped; they must now survive."""

    def setup_method(self) -> None:
        _warned_reserved_keys.clear()

    def test_colliding_key_is_suffixed_not_dropped(self) -> None:
        log, buffer = _json_logger("collide1")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            log.info("hi", name="shadow", module="m", ok=1)

        data = _last_json(buffer)
        assert data["name_"] == "shadow"
        assert data["module_"] == "m"
        assert data["ok"] == 1

    def test_collision_warns_once_per_key(self) -> None:
        log, _ = _json_logger("collide2")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            log.info("first", name="a")
            log.info("second", name="b")

        assert len(caught) == 1
        assert "name" in str(caught[0].message)


class TestPropagation:
    """Records were emitted twice once anything called basicConfig()."""

    def test_records_do_not_reach_root_by_default(self) -> None:
        _loggers.clear()
        root_buffer = io.StringIO()
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        root.handlers = [logging.StreamHandler(root_buffer)]
        root.setLevel(logging.INFO)
        try:
            log = get_logger("prop1", level="INFO", json=True)
            for handler in log._logger.handlers:
                handler.stream = io.StringIO()  # type: ignore[attr-defined]
            log.info("only-once")
            assert root_buffer.getvalue() == ""
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_propagate_can_be_re_enabled(self) -> None:
        _loggers.clear()
        log = get_logger("prop2", level="INFO", json=True, propagate=True)
        assert log._logger.propagate is True


class TestFileHandlerOutput:
    def test_no_ansi_escapes_in_file(self) -> None:
        """use_colors resolved from stderr.isatty() regardless of destination."""

        class FakeTTY:
            def isatty(self) -> bool:
                return True

            def write(self, _: str) -> int:
                return 0

            def flush(self) -> None:
                return None

        _loggers.clear()
        real_stderr = sys.stderr
        sys.stderr = FakeTTY()  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "app.log")
                log = get_logger("file1", level="INFO", json=False, file=path)
                log.info("hello", user="alice")
                log.flush()
                with open(path, "rb") as handle:
                    raw = handle.read()
                close_logcore_handlers()
        finally:
            sys.stderr = real_stderr

        assert b"\x1b[" not in raw
        assert b"hello" in raw

    def test_console_can_be_disabled(self) -> None:
        _loggers.clear()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.log")
            log = get_logger("file2", level="INFO", json=True, file=path, console=False)
            assert len(log._logger.handlers) == 1
            close_logcore_handlers()


class TestStructuredExceptions:
    def test_exception_type_and_message_emitted(self) -> None:
        log, buffer = _json_logger("exc1")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")

        data = _last_json(buffer)
        assert data["exception_type"] == "ValueError"
        assert data["exception_message"] == "boom"
        assert "Traceback" in data["exception"]

    def test_explicit_exc_info_is_respected(self) -> None:
        log, buffer = _json_logger("exc2")
        log.exception("failed", exc_info=False)

        assert "exception" not in _last_json(buffer)


class TestCallerAttribution:
    def test_timer_attributes_to_user_code_not_logcore(self) -> None:
        """Timer records used to point at logcore/utils.py."""
        _loggers.clear()
        log = get_logger("caller1", level="INFO", json=True)
        records: List[logging.LogRecord] = []

        class Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        log._logger.handlers = [Collector()]

        with log.time("work"):
            pass

        assert records
        for record in records:
            # Normalize separators so this is a real assertion on Windows
            # rather than one that passes because the path uses backslashes.
            where = record.pathname.replace(os.sep, "/")
            assert "logcore/utils.py" not in where
            assert "logcore/logger.py" not in where
            assert record.filename == "test_hardening.py"

    def test_timer_failure_captures_traceback(self) -> None:
        log, buffer = _json_logger("caller2")
        with pytest.raises(ValueError):
            with log.time("work"):
                raise ValueError("inner boom")

        data = _last_json(buffer)
        assert data["exception_type"] == "ValueError"


class TestSamplerMemoryBounds:
    def test_buffers_are_evicted_past_cap(self) -> None:
        sampler = Sampler(tail_based=True, rate=0.0, max_active_buffers=5)
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="x",
            args=(),
            exc_info=None,
        )

        for index in range(50):
            sampler.buffer(record, cid=f"cid-{index}")

        stats = sampler.stats()
        assert stats.active_buffers == 5
        assert stats.evicted_buffers == 45

    def test_early_decision_drops_without_a_record(self) -> None:
        sampler = Sampler(rate=0.0)
        from logcore.sampling import Decision

        assert sampler.decide_early(logging.INFO, None) is Decision.DROP
        assert sampler.decide_early(logging.ERROR, None) is Decision.KEEP


class TestEnvValidation:
    def test_bad_level_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOGCORE_LEVEL", "TRACE")
        from logcore.config import get_config_from_env

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = get_config_from_env()

        assert "level" not in config
        assert any("LOGCORE_LEVEL" in str(w.message) for w in caught)

    def test_bad_sample_rate_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOGCORE_SAMPLE_RATE", "0.1x")
        from logcore.sampling import sampler_from_env

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sampler_from_env()

        assert any("LOGCORE_SAMPLE_RATE" in str(w.message) for w in caught)


class TestNewEnvVars:
    """The v0.1.7 config surface, driven from the environment."""

    def _env_config(self, monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
        from logcore.config import get_config_from_env

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            return get_config_from_env(), caught

    def test_propagate_and_console_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, _ = self._env_config(
            monkeypatch,
            LOGCORE_PROPAGATE="true",
            LOGCORE_CONSOLE="off",
            LOGCORE_CONSOLE_STREAM="stdout",
        )

        assert config["propagate"] is True
        assert config["console"] is False
        assert config["console_stream"] == "stdout"

    def test_async_and_queue_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, _ = self._env_config(
            monkeypatch, LOGCORE_ASYNC="yes", LOGCORE_QUEUE_SIZE="256"
        )

        assert config["async_logging"] is True
        assert config["queue_size"] == 256

    def test_bad_boolean_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, caught = self._env_config(monkeypatch, LOGCORE_JSON="maybe")

        assert "json" not in config
        assert any("LOGCORE_JSON" in str(w.message) for w in caught)

    def test_bad_integer_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, caught = self._env_config(monkeypatch, LOGCORE_BACKUP_COUNT="lots")

        assert "backup_count" not in config
        assert any("LOGCORE_BACKUP_COUNT" in str(w.message) for w in caught)

    def test_out_of_range_integer_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, caught = self._env_config(monkeypatch, LOGCORE_MAX_FILE_SIZE="0")

        assert "max_file_size" not in config
        assert any("LOGCORE_MAX_FILE_SIZE" in str(w.message) for w in caught)

    def test_bad_console_stream_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config, caught = self._env_config(monkeypatch, LOGCORE_CONSOLE_STREAM="tty")

        assert "console_stream" not in config
        assert any("LOGCORE_CONSOLE_STREAM" in str(w.message) for w in caught)

    def test_invalid_console_stream_kwarg_raises(self) -> None:
        from logcore.config import create_config

        with pytest.raises(ValueError, match="console_stream"):
            create_config(name="x", console_stream="tty")

    def test_sampler_and_sample_rate_conflict_raises(self) -> None:
        from logcore.config import create_config

        with pytest.raises(ValueError, match="not both"):
            create_config(name="x", sampler=Sampler(rate=0.5), sample_rate=0.5)

    def test_console_stream_stdout_is_used(self) -> None:
        _loggers.clear()
        log = get_logger("stream1", level="INFO", json=True, console_stream="stdout")

        handler = log._logger.handlers[0]
        assert handler.stream is sys.stdout  # type: ignore[attr-defined]


class TestStdlibInterop:
    def teardown_method(self) -> None:
        reset_stdlib()

    def test_third_party_logger_is_captured_as_json(self) -> None:
        buffer = io.StringIO()
        root = configure_stdlib(level="INFO", json=True)
        for handler in root.handlers:
            handler.stream = buffer  # type: ignore[attr-defined]

        logging.getLogger("some.third.party").info("external message")

        data = json.loads(buffer.getvalue().strip().splitlines()[-1])
        assert data["message"] == "external message"
        assert data["logger"] == "some.third.party"

    def test_quiet_lowers_noisy_logger(self) -> None:
        configure_stdlib(level="DEBUG", quiet=["noisy", ("louder", "ERROR")])

        assert logging.getLogger("noisy").level == logging.WARNING
        assert logging.getLogger("louder").level == logging.ERROR

    def test_dict_config_formatter_entry(self) -> None:
        import logging.config

        from logcore import dict_config_formatter

        logging.config.dictConfig(
            {
                "version": 1,
                "formatters": {"logcore": dict_config_formatter(json=True)},
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "formatter": "logcore",
                    }
                },
                "root": {"handlers": ["console"], "level": "INFO"},
            }
        )
        handler = logging.getLogger().handlers[0]

        assert isinstance(handler.formatter, JSONFormatter)

    def test_dict_config_formatter_text_with_redact_fields(self) -> None:
        from logcore import dict_config_formatter

        entry = dict_config_formatter(json=False, redact_fields={"pin"})

        assert entry["()"] == "logcore.formatters.TextFormatter"
        assert entry["redact_fields"] == {"pin"}

    def test_replace_existing_removes_basicconfig_handler(self) -> None:
        root = logging.getLogger()
        marker = logging.StreamHandler(io.StringIO())
        root.addHandler(marker)

        configure_stdlib(level="INFO", replace_existing=True)

        assert marker not in root.handlers


class TestMiddleware:
    def _run_asgi(
        self, middleware: CorrelationIdMiddleware, headers: List[Tuple[bytes, bytes]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        import asyncio

        seen: Dict[str, Optional[str]] = {"cid": None}
        sent: List[Dict[str, Any]] = []

        async def send(message: Dict[str, Any]) -> None:
            sent.append(message)

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request"}

        async def main() -> None:
            await middleware({"type": "http", "headers": headers}, receive, send)

        from logcore.utils import get_correlation_id

        async def app(scope: Any, receive: Any, send: Any) -> None:
            seen["cid"] = get_correlation_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})

        middleware.app = app
        asyncio.run(main())
        return seen["cid"], sent

    def test_asgi_adopts_incoming_request_id(self) -> None:
        middleware = CorrelationIdMiddleware(None)  # type: ignore[arg-type]
        cid, sent = self._run_asgi(middleware, [(b"x-request-id", b"abc-123")])

        assert cid == "abc-123"
        assert (b"X-Request-ID", b"abc-123") in sent[0]["headers"]

    def test_asgi_falls_back_to_traceparent(self) -> None:
        traceparent = b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        middleware = CorrelationIdMiddleware(None)  # type: ignore[arg-type]
        cid, _ = self._run_asgi(middleware, [(b"traceparent", traceparent)])

        assert cid == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_asgi_generates_id_when_absent(self) -> None:
        middleware = CorrelationIdMiddleware(None)  # type: ignore[arg-type]
        cid, _ = self._run_asgi(middleware, [])

        assert cid is not None and len(cid) > 0

    def test_asgi_rejects_unsafe_incoming_id(self) -> None:
        middleware = CorrelationIdMiddleware(None)  # type: ignore[arg-type]
        cid, _ = self._run_asgi(middleware, [(b"x-request-id", b"bad\r\nvalue")])

        assert cid is not None
        assert "\r" not in cid and "\n" not in cid

    def test_wsgi_binds_and_echoes(self) -> None:
        from logcore.utils import get_correlation_id

        seen: Dict[str, Optional[str]] = {"cid": None}
        captured: Dict[str, Any] = {}

        def app(environ: Dict[str, Any], start_response: Any) -> List[bytes]:
            seen["cid"] = get_correlation_id()
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        def start_response(status: str, headers: Any, exc_info: Any = None) -> None:
            captured["headers"] = headers

        wrapped = WSGICorrelationIdMiddleware(app)
        body = wrapped({"HTTP_X_REQUEST_ID": "wsgi-42"}, start_response)

        assert body == [b"ok"]
        assert seen["cid"] == "wsgi-42"
        assert ("X-Request-ID", "wsgi-42") in captured["headers"]

    def test_middleware_releases_sample_buffer(self) -> None:
        _loggers.clear()
        log = get_logger(
            "mw1", level="INFO", json=True, sampler=Sampler(tail_based=True, rate=0.0)
        )
        for handler in log._logger.handlers:
            handler.stream = io.StringIO()  # type: ignore[attr-defined]

        def app(environ: Dict[str, Any], start_response: Any) -> List[bytes]:
            log.info("buffered work")
            return [b"ok"]

        def start_response(status: str, headers: Any, exc_info: Any = None) -> None:
            return None

        wrapped = WSGICorrelationIdMiddleware(app, logger=log)
        wrapped({"HTTP_X_REQUEST_ID": "req-1"}, start_response)

        assert log.sampler is not None
        assert log.sampler.stats().active_buffers == 0


class TestAsyncLogging:
    def test_queue_handler_delivers_records(self) -> None:
        import logcore

        _loggers.clear()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "async.log")
            get_logger(
                "async1",
                level="INFO",
                json=True,
                file=path,
                console=False,
                async_logging=True,
            ).info("queued message", n=1)

            logcore.flush()
            logcore.shutdown()

            with open(path) as handle:
                content = handle.read()
            close_logcore_handlers()

        assert "queued message" in content

    def test_queue_handler_is_single_handler(self) -> None:
        import logcore

        _loggers.clear()
        log = get_logger("async2", level="INFO", json=True, async_logging=True)
        try:
            assert len(log._logger.handlers) == 1
            assert log._logger.handlers[0].__class__.__name__ == "QueueHandler"
        finally:
            logcore.shutdown()


@pytest.mark.slow
class TestPerformance:
    def test_log_call_overhead_is_bounded(self) -> None:
        """Guard against reintroducing per-call getcwd/regex work.

        Measured *relative to stdlib logging in the same process*, because an
        absolute microsecond ceiling is not portable: a shared CI runner is
        several times slower than a developer laptop, and the value would have
        to be so loose to survive that it would catch nothing.

        Timing is taken as the best of several rounds. The minimum is the
        robust estimator here — a run can only be slowed by scheduling noise,
        never speeded up.
        """
        import time

        if sys.gettrace() is not None:
            # coverage.py line-traces logcore and not stdlib logging, so the
            # ratio below would compare an instrumented path against an
            # uninstrumented one. Run this without coverage (see the benchmark
            # job in ci.yml) for a meaningful number.
            pytest.skip("a tracer is active; per-call timing is meaningless")

        devnull = open(os.devnull, "w")
        try:
            stdlib_log = logging.getLogger("perf_stdlib_baseline")
            stdlib_log.handlers = [logging.StreamHandler(devnull)]
            stdlib_log.setLevel(logging.INFO)
            stdlib_log.propagate = False

            _loggers.clear()
            json_log = get_logger("perf_json", level="INFO", json=True)
            for handler in json_log._logger.handlers:
                handler.stream = devnull  # type: ignore[attr-defined]

            text_log = get_logger("perf_text", level="INFO", json=False)
            for handler in text_log._logger.handlers:
                handler.stream = devnull  # type: ignore[attr-defined]

            def measure(fn: Any, iterations: int = 5000) -> float:
                for _ in range(500):  # warm up
                    fn()
                best = float("inf")
                for _ in range(3):
                    start = time.perf_counter()
                    for _ in range(iterations):
                        fn()
                    best = min(best, time.perf_counter() - start)
                return best / iterations * 1_000_000

            baseline = measure(lambda: stdlib_log.info("benchmark message"))
            json_cost = measure(
                lambda: json_log.info("benchmark", user="alice", count=1)
            )
            text_cost = measure(
                lambda: text_log.info("benchmark", user="alice", count=1)
            )
        finally:
            devnull.close()

        # Both formatters are covered because they regress differently.
        #
        # The ceiling was chosen by reintroducing the pre-0.1.7 code and
        # measuring, not by guesswork:
        #   - full-line redaction regex in TextFormatter: text -> 5.5x. Caught.
        #   - per-frame abspath in _find_caller: ~1us/call, ~2.4x. Not caught,
        #     and deliberately so; that is drift, not a regression worth
        #     failing a build over.
        #
        # Both paths sit at ~2.2x here, so 3.5x leaves room for a slower or
        # noisier machine while still catching the 5.5x class of regression.
        for label, cost in (("JSON", json_cost), ("text", text_cost)):
            ratio = cost / baseline
            assert ratio < 3.5, (
                f"logcore {label} is {ratio:.1f}x stdlib "
                f"({cost:.1f} vs {baseline:.1f} us/call) -- expected < 3.5x"
            )
