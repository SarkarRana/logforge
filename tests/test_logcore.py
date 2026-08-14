"""Tests for LogCore logging library."""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from logcore import LogLevel, get_correlation_id, get_logger, set_correlation_id
from logcore.config import LogCoreConfig, create_config
from logcore.formatters import JSONFormatter, TextFormatter
from logcore.handlers import ConsoleHandler, FileHandler

from .conftest import close_logcore_handlers


class TestConfiguration:
    """Test configuration functionality."""

    def test_log_level_enum(self):
        """Test LogLevel enum."""
        assert LogLevel.from_string("DEBUG") == LogLevel.DEBUG
        assert LogLevel.from_string("info") == LogLevel.INFO
        assert LogLevel.from_string("WARN") == LogLevel.WARNING
        assert LogLevel.from_string("warning") == LogLevel.WARNING

    def test_config_creation(self):
        """Test configuration creation."""
        config = create_config("test", level="INFO", json=True)
        assert config.name == "test"
        assert config.level == LogLevel.INFO
        assert config.json is True

    def test_environment_variables(self):
        """Test configuration from environment variables."""
        with patch.dict(
            os.environ,
            {
                "LOGCORE_LEVEL": "DEBUG",
                "LOGCORE_JSON": "true",
                "LOGCORE_FILE": "/tmp/test.log",
                "LOGCORE_REDACT_FIELDS": "password,secret",
            },
        ):
            config = create_config("test")
            assert config.level == LogLevel.DEBUG
            assert config.json is True
            assert config.file == "/tmp/test.log"
            assert "password" in config.redact_fields
            assert "secret" in config.redact_fields


class TestFormatters:
    """Test formatter functionality."""

    def test_json_formatter(self):
        """Test JSON formatter."""
        formatter = JSONFormatter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-id"
        record.user = "alice"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert data["correlation_id"] == "test-id"
        assert data["user"] == "alice"
        assert "timestamp" in data

    def test_text_formatter(self):
        """Test text formatter."""
        formatter = TextFormatter(use_colors=False)

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-id"
        record.user = "alice"

        output = formatter.format(record)

        assert "INFO" in output
        assert "Test message" in output
        assert "[cid=test-id]" in output
        assert "user=alice" in output

    def test_redaction(self):
        """Test field redaction."""
        redact_fields = {"password", "secret"}
        formatter = JSONFormatter(redact_fields=redact_fields)

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Login attempt",
            args=(),
            exc_info=None,
        )
        record.password = "secret123"
        record.username = "alice"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["password"] == "se***"  # "secret123" → prefix 2
        assert data["username"] == "alice"


class TestHandlers:
    """Test handler functionality."""

    def test_console_handler(self):
        """Test console handler creation."""
        config = LogCoreConfig(name="test")
        handler = ConsoleHandler(config)

        assert isinstance(handler.get_handler(), logging.StreamHandler)

    def test_file_handler(self):
        """Test file handler with rotation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            config = LogCoreConfig(name="test", max_file_size=1024, backup_count=3)

            handler = FileHandler(config, str(log_file))
            log_handler = handler.get_handler()

            try:
                assert isinstance(log_handler, logging.handlers.RotatingFileHandler)
                assert log_handler.maxBytes == 1024
                assert log_handler.backupCount == 3
            finally:
                # Windows cannot remove the temp dir while the file is open.
                log_handler.close()


class TestUtils:
    """Test utility functions."""

    def test_correlation_id(self):
        """Test correlation ID functionality."""
        # Test setting and getting correlation ID
        cid = set_correlation_id("test-123")
        assert cid == "test-123"
        assert get_correlation_id() == "test-123"

        # Test auto-generation
        auto_cid = set_correlation_id()
        assert auto_cid is not None
        assert len(auto_cid) > 10  # UUID should be longer

    def test_correlation_id_context(self):
        """Test correlation ID context manager."""
        logger = get_logger("test")

        with logger.with_correlation_id("ctx-123"):
            assert get_correlation_id() == "ctx-123"

        # Should be cleared after context
        assert get_correlation_id() != "ctx-123"


class TestLogger:
    """Test main logger functionality."""

    def setup_method(self):
        """Set up test method."""
        # Clear any existing loggers
        from logcore.logger import _loggers

        _loggers.clear()

    def test_basic_logging(self):
        """Test basic logging functionality."""
        logger = get_logger("test", level="DEBUG")

        # Test all log levels
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")

    def test_structured_logging(self):
        """Test structured logging with extra fields."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("test", json=True, file=log_file)
            logger.info("User action", user="alice", action="login", success=True)

            # Read log file and verify JSON structure
            with open(log_file, "r") as f:
                line = f.readline().strip()
                data = json.loads(line)

                assert data["message"] == "User action"
                assert data["user"] == "alice"
                assert data["action"] == "login"
                assert data["success"] is True

        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    def test_exception_logging(self):
        """Test exception logging."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("test", json=True, file=log_file)

            try:
                1 / 0
            except ZeroDivisionError:
                logger.exception("Division error")

            # Read log file and verify exception info
            with open(log_file, "r") as f:
                line = f.readline().strip()
                data = json.loads(line)

                assert data["message"] == "Division error"
                assert "exception" in data
                assert "ZeroDivisionError" in data["exception"]

        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    def test_timer_context_manager(self):
        """Test timer context manager."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("test", json=True, file=log_file)

            with logger.time("test_operation"):
                time.sleep(0.1)  # Sleep for 100ms

            # Read log file and verify timer logs
            with open(log_file, "r") as f:
                lines = f.readlines()

            assert len(lines) == 2  # Start and completion messages

            start_data = json.loads(lines[0])
            end_data = json.loads(lines[1])

            assert "Starting test_operation" in start_data["message"]
            assert "Completed test_operation" in end_data["message"]
            assert "duration_ms" in end_data
            assert end_data["duration_ms"] >= 100  # At least 100ms

        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    def test_file_rotation(self):
        """Test file rotation functionality."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "rotate_test.log"

            logger = get_logger(
                "test", file=str(log_file), max_file_size=1024, backup_count=2  # 1KB
            )

            # Write enough data to trigger rotation
            large_message = "x" * 200
            for i in range(10):
                logger.info(f"Message {i}: {large_message}")

            # Check that rotation occurred
            rotated_files = list(Path(tmpdir).glob("rotate_test.log*"))
            assert len(rotated_files) > 1  # Should have main file + rotated files

            close_logcore_handlers()

    def test_thread_safety(self):
        """Test thread safety of logging."""
        messages = []

        def log_messages(thread_id):
            logger = get_logger("test")
            for i in range(10):
                with logger.with_correlation_id(f"thread-{thread_id}"):
                    logger.info(f"Thread {thread_id} message {i}")
                    messages.append(f"thread-{thread_id}-{i}")

        threads = []
        for i in range(5):
            thread = threading.Thread(target=log_messages, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify all messages were logged
        assert len(messages) == 50  # 5 threads * 10 messages each

    def test_replacement_warning(self):
        """get_logger should warn when replacing a cached logger with new config."""
        import warnings as _warnings

        get_logger("warn_test", level="INFO")
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            get_logger("warn_test", level="DEBUG")
        assert len(w) == 1
        assert issubclass(w[0].category, UserWarning)
        assert "warn_test" in str(w[0].message)

    def test_no_warning_on_cache_hit(self):
        """get_logger should not warn when returning an existing logger unchanged."""
        import warnings as _warnings

        get_logger("no_warn_test", level="INFO")
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            get_logger("no_warn_test")
        assert len(w) == 0

    def test_level_filtering(self):
        """Test log level filtering."""
        logger = get_logger("test", level="WARNING")

        assert not logger.is_enabled_for("DEBUG")
        assert not logger.is_enabled_for("INFO")
        assert logger.is_enabled_for("WARNING")
        assert logger.is_enabled_for("ERROR")
        assert logger.is_enabled_for("CRITICAL")

    def test_redaction_security(self):
        """Test redaction of sensitive fields."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            redact_fields = {"password", "token", "secret"}
            logger = get_logger(
                "test", json=True, file=log_file, redact_fields=redact_fields
            )

            logger.info(
                "User login",
                username="alice",
                password="secret123",
                token="abc123",
                role="admin",
            )

            # Read log and verify redaction
            with open(log_file, "r") as f:
                line = f.readline().strip()
                data = json.loads(line)

                assert data["username"] == "alice"
                assert data["password"] == "se***"  # "secret123" → prefix 2
                assert data["token"] == "a***"  # "abc123" → prefix 1
                assert data["role"] == "admin"

        finally:
            close_logcore_handlers()
            os.unlink(log_file)


class TestPartialMasking:
    """Test that sensitive fields are partially masked, not fully redacted."""

    def test_long_value_shows_prefix(self):
        formatter = JSONFormatter(redact_fields={"password"})
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="login",
            args=(),
            exc_info=None,
        )
        record.password = "supersecret"  # 11 chars → prefix 2

        data = json.loads(formatter.format(record))
        assert data["password"].endswith("***")
        assert data["password"].startswith("su")
        assert "supersecret" not in data["password"]

    def test_short_value_fully_redacted(self):
        formatter = JSONFormatter(redact_fields={"token"})
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="req",
            args=(),
            exc_info=None,
        )
        record.token = "abc"  # 3 chars → full redact

        data = json.loads(formatter.format(record))
        assert data["token"] == "[REDACTED]"

    def test_boundary_value_four_chars_fully_redacted(self):
        formatter = JSONFormatter(redact_fields={"key"})
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="req",
            args=(),
            exc_info=None,
        )
        record.key = "1234"  # exactly 4 chars → full redact

        data = json.loads(formatter.format(record))
        assert data["key"] == "[REDACTED]"

    def test_text_formatter_partial_masking(self):
        formatter = TextFormatter(redact_fields={"password"}, use_colors=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="login",
            args=(),
            exc_info=None,
        )
        record.password = "mypassword"  # 10 chars → prefix 2

        output = formatter.format(record)
        assert "mypassword" not in output
        # Redaction is structural now, so the masked value is rendered like any
        # other extra (key=value) instead of being re-quoted by a regex pass.
        assert "password=my***" in output

    def test_text_formatter_no_stdlib_noise(self):
        """TextFormatter should not emit stdlib LogRecord internals as extras."""
        formatter = TextFormatter(use_colors=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.user = "alice"

        output = formatter.format(record)
        assert "user=alice" in output
        for noise in ("exc_info", "exc_text", "stack_info", "taskName"):
            assert noise not in output


class TestOpenTelemetry:
    """Test automatic OTel trace/span ID injection."""

    def setup_method(self):
        from logcore.logger import _loggers

        _loggers.clear()

    def test_trace_ids_injected_when_span_active(self):
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("logcore-test")

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("otel_test", json=True, file=log_file)

            with tracer.start_as_current_span("test-span"):
                logger.info("inside span")

            with open(log_file) as f:
                data = json.loads(f.readline())

            assert "trace_id" in data
            assert "span_id" in data
            assert len(data["trace_id"]) == 32  # 128-bit hex
            assert len(data["span_id"]) == 16  # 64-bit hex
        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    def test_no_trace_ids_outside_span(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("no_otel_test", json=True, file=log_file)
            logger.info("outside any span")

            with open(log_file) as f:
                data = json.loads(f.readline())

            assert "trace_id" not in data
            assert "span_id" not in data
        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    def test_trace_id_absent_after_span_exits(self):
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("logcore-test2")

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("otel_scope_test", json=True, file=log_file)

            with tracer.start_as_current_span("scoped-span"):
                logger.info("inside")

            logger.info("outside")  # span no longer active

            with open(log_file) as f:
                lines = [line.strip() for line in f if line.strip()]

            inside = json.loads(lines[0])
            outside = json.loads(lines[1])

            assert "trace_id" in inside
            assert "trace_id" not in outside
        finally:
            close_logcore_handlers()
            os.unlink(log_file)


class TestAsync:
    """Test async timer functionality."""

    def setup_method(self):
        from logcore.logger import _loggers

        _loggers.clear()

    @pytest.mark.asyncio
    async def test_async_timer_logs_start_and_complete(self):
        """AsyncTimer should emit start/complete messages with numeric duration_ms."""
        import asyncio

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("async_test", json=True, file=log_file)

            async with logger.time("async_op"):
                await asyncio.sleep(0.05)

            with open(log_file) as f:
                lines = [line.strip() for line in f if line.strip()]

            assert len(lines) == 2
            start = json.loads(lines[0])
            end = json.loads(lines[1])

            assert "Starting async_op" in start["message"]
            assert "Completed async_op" in end["message"]
            assert isinstance(end["duration_ms"], (int, float))
            assert end["duration_ms"] >= 50
        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    @pytest.mark.asyncio
    async def test_async_timer_logs_error_on_exception(self):
        """AsyncTimer should log at ERROR level and include duration_ms
        when the body raises.
        """
        import asyncio

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as f:
            log_file = f.name

        try:
            logger = get_logger("async_err_test", json=True, file=log_file)

            with pytest.raises(ValueError):
                async with logger.time("failing_op"):
                    await asyncio.sleep(0.01)
                    raise ValueError("boom")

            with open(log_file) as f:
                lines = [line.strip() for line in f if line.strip()]

            assert len(lines) == 2
            error_entry = json.loads(lines[1])
            assert error_entry["level"] == "ERROR"
            assert "Failed failing_op" in error_entry["message"]
            assert isinstance(error_entry["duration_ms"], (int, float))
        finally:
            close_logcore_handlers()
            os.unlink(log_file)

    @pytest.mark.asyncio
    async def test_async_correlation_id_isolated_per_task(self):
        """Correlation IDs set via contextvars must not leak between
        concurrent tasks.
        """
        import asyncio

        results = {}

        async def task(name, cid):
            logger = get_logger("cid_test")
            with logger.with_correlation_id(cid):
                await asyncio.sleep(0.02)
                results[name] = get_correlation_id()

        await asyncio.gather(task("a", "cid-a"), task("b", "cid-b"))

        assert results["a"] == "cid-a"
        assert results["b"] == "cid-b"


if __name__ == "__main__":
    pytest.main([__file__])
