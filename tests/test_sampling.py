"""Tests for the LogCore sampler."""

import asyncio
import logging
import os
import random
import warnings
from typing import List
from unittest.mock import patch

import pytest

from logcore import Sampler, get_logger, set_correlation_id
from logcore.sampling import Decision, sampler_from_env


def _make_record(level: str = "INFO", msg: str = "x") -> logging.LogRecord:
    numeric = getattr(logging, level)
    record = logging.LogRecord(
        name="t", level=numeric, pathname="", lineno=0, msg=msg, args=(), exc_info=None
    )
    return record


class _Collector(logging.Handler):
    """Test handler that captures emitted records in memory."""

    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _attach_collector(log) -> _Collector:  # type: ignore[no-untyped-def]
    collector = _Collector()
    log._logger.handlers = [collector]
    log._logger.setLevel(logging.DEBUG)
    return collector


class TestSamplerValidation:
    def test_rate_below_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            Sampler(rate=-0.1)

    def test_rate_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            Sampler(rate=1.5)

    def test_buffer_size_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="tail_buffer_size"):
            Sampler(tail_buffer_size=0)

    def test_defaults_keep_everything(self) -> None:
        s = Sampler()
        assert s.decide(_make_record("INFO")) is Decision.KEEP
        assert s.decide(_make_record("DEBUG")) is Decision.KEEP


class TestRateBasedSampling:
    def test_rate_one_keeps_all(self) -> None:
        s = Sampler(rate=1.0)
        for _ in range(100):
            assert s.decide(_make_record("INFO")) is Decision.KEEP

    def test_rate_zero_drops_all_non_kept_levels(self) -> None:
        s = Sampler(rate=0.0)
        for _ in range(100):
            assert s.decide(_make_record("INFO")) is Decision.DROP

    def test_always_keep_overrides_rate_zero(self) -> None:
        s = Sampler(rate=0.0)
        assert s.decide(_make_record("WARNING")) is Decision.KEEP
        assert s.decide(_make_record("ERROR")) is Decision.KEEP
        assert s.decide(_make_record("CRITICAL")) is Decision.KEEP

    def test_rate_distribution_with_deterministic_rng(self) -> None:
        s = Sampler(rate=0.1, _rng=random.Random(42))
        kept = sum(
            1 for _ in range(10000) if s.decide(_make_record("INFO")) is Decision.KEEP
        )
        # Allow ±3% jitter; with seed=42 should land near 1000.
        assert 700 < kept < 1300

    def test_custom_always_keep_set(self) -> None:
        s = Sampler(rate=0.0, always_keep={"ERROR"})
        # WARNING is no longer protected.
        assert s.decide(_make_record("WARNING")) is Decision.DROP
        assert s.decide(_make_record("ERROR")) is Decision.KEEP

    def test_always_keep_is_case_insensitive(self) -> None:
        # Lowercase input should still match uppercase levelname on records.
        s = Sampler(rate=0.0, always_keep={"warning", "error"})
        assert s.decide(_make_record("WARNING")) is Decision.KEEP
        assert s.decide(_make_record("ERROR")) is Decision.KEEP
        assert s.decide(_make_record("INFO")) is Decision.DROP


class TestTailBasedSampling:
    def test_buffers_when_cid_present(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-1")
        assert s.decide(_make_record("INFO")) is Decision.BUFFER

    def test_falls_back_to_rate_when_no_cid(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        # No correlation_id set (fresh contextvar).
        set_correlation_id(None)  # type: ignore[arg-type]
        # set_correlation_id(None) actually generates a uuid, so use a clean context.
        import contextvars

        ctx = contextvars.copy_context()

        def _check() -> Decision:
            from logcore.utils import correlation_context

            correlation_context.set(None)
            return s.decide(_make_record("INFO"))

        decision = ctx.run(_check)
        assert decision is Decision.DROP

    def test_buffer_stores_then_flush_pending_returns_them(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-flush")
        for i in range(3):
            r = _make_record("INFO", msg=f"line-{i}")
            assert s.decide(r) is Decision.BUFFER
            s.buffer(r)

        err = _make_record("ERROR", msg="boom")
        pending = s.flush_pending(err)
        assert [r.msg for r in pending] == ["line-0", "line-1", "line-2"]

    def test_after_flush_same_cid_passes_through(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-passthrough")
        s.buffer(_make_record("INFO"))
        s.flush_pending(_make_record("ERROR"))
        # Next INFO should pass through directly (no buffering).
        assert s.decide(_make_record("INFO")) is Decision.KEEP

    def test_buffer_eviction_when_full(self) -> None:
        s = Sampler(rate=0.0, tail_based=True, tail_buffer_size=3)
        set_correlation_id("req-evict")
        for i in range(5):
            r = _make_record("INFO", msg=f"r-{i}")
            s.buffer(r)
        # Only the last 3 should remain; first 2 evicted.
        pending = s.flush_pending(_make_record("ERROR"))
        assert [r.msg for r in pending] == ["r-2", "r-3", "r-4"]
        assert s.stats().dropped_overflow == 2

    def test_discard_buffer_drops_without_emitting(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-discard")
        s.buffer(_make_record("INFO"))
        s.buffer(_make_record("INFO"))
        n = s.discard_buffer("req-discard")
        assert n == 2
        assert s.stats().active_buffers == 0

    def test_flush_pending_returns_empty_when_no_buffer(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-empty")
        # No prior buffering.
        assert s.flush_pending(_make_record("ERROR")) == []

    def test_flush_pending_ignores_non_always_keep_levels(self) -> None:
        s = Sampler(rate=0.0, tail_based=True)
        set_correlation_id("req-noflush")
        s.buffer(_make_record("INFO"))
        # INFO is not in always_keep → no flush.
        assert s.flush_pending(_make_record("INFO")) == []


class TestLoggerIntegration:
    def test_rate_zero_drops_info_keeps_warning(self) -> None:
        log = get_logger("test-int-1", sampler=Sampler(rate=0.0))
        collector = _attach_collector(log)
        for _ in range(5):
            log.info("noise")
        log.warning("signal")
        assert len(collector.records) == 1
        assert collector.records[0].getMessage() == "signal"
        assert log.sampler.stats().dropped == 5
        assert log.sampler.stats().kept == 1

    def test_tail_based_flush_on_error_emits_buffered_records(self) -> None:
        log = get_logger("test-int-2", sampler=Sampler(rate=0.0, tail_based=True))
        collector = _attach_collector(log)

        with log.with_correlation_id("trace-A"):
            log.info("step 1")
            log.info("step 2")
            log.error("boom")
            log.info("step 3 after error")

        messages = [r.getMessage() for r in collector.records]
        assert messages == ["step 1", "step 2", "boom", "step 3 after error"]

    def test_tail_based_clean_exit_drops_buffer(self) -> None:
        log = get_logger("test-int-3", sampler=Sampler(rate=0.0, tail_based=True))
        collector = _attach_collector(log)

        with log.with_correlation_id("trace-B"):
            log.info("step 1")
            log.info("step 2")
        # No error → buffer discarded on exit.
        assert collector.records == []
        assert log.sampler.stats().active_buffers == 0

    def test_tail_based_isolated_across_correlation_ids(self) -> None:
        log = get_logger("test-int-4", sampler=Sampler(rate=0.0, tail_based=True))
        collector = _attach_collector(log)

        with log.with_correlation_id("req-x"):
            log.info("from x")
        with log.with_correlation_id("req-y"):
            log.info("from y")
            log.error("y-failed")

        messages = [r.getMessage() for r in collector.records]
        # Only req-y emitted (because of the error); req-x dropped silently.
        assert messages == ["from y", "y-failed"]

    def test_sample_rate_shortcut(self) -> None:
        log = get_logger("test-int-5", sample_rate=0.5)
        assert log.sampler is not None
        assert log.sampler.rate == 0.5

    def test_sampler_and_sample_rate_both_passed_raises(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            get_logger("test-int-6", sampler=Sampler(rate=0.1), sample_rate=0.5)

    def test_flush_sample_buffer_for_bare_set_correlation_id(self) -> None:
        log = get_logger("test-int-7", sampler=Sampler(rate=0.0, tail_based=True))
        collector = _attach_collector(log)
        set_correlation_id("bare-cid")
        log.info("buffered 1")
        log.info("buffered 2")
        n = log.flush_sample_buffer("bare-cid")
        assert n == 2
        assert collector.records == []  # Discarded, not emitted.


class TestAsyncIsolation:
    def test_concurrent_tasks_have_separate_buffers(self) -> None:
        log = get_logger("test-async", sampler=Sampler(rate=0.0, tail_based=True))
        collector = _attach_collector(log)

        async def request(cid: str, should_error: bool) -> None:
            with log.with_correlation_id(cid):
                log.info(f"start-{cid}")
                await asyncio.sleep(0)
                log.info(f"work-{cid}")
                if should_error:
                    log.error(f"fail-{cid}")

        async def main() -> None:
            await asyncio.gather(
                request("A", should_error=False),
                request("B", should_error=True),
                request("C", should_error=False),
            )

        asyncio.run(main())

        messages = [r.getMessage() for r in collector.records]
        # Only request B should produce output (its error flushed its buffer).
        assert "fail-B" in messages
        assert "start-B" in messages
        assert "work-B" in messages
        # A and C exited cleanly → their records discarded.
        assert "start-A" not in messages
        assert "start-C" not in messages


class TestEnvVars:
    def test_env_rate_only(self) -> None:
        with patch.dict(os.environ, {"LOGCORE_SAMPLE_RATE": "0.25"}, clear=False):
            s = sampler_from_env()
        assert s is not None
        assert s.rate == 0.25
        assert s.tail_based is False

    def test_env_tail_based(self) -> None:
        with patch.dict(
            os.environ,
            {"LOGCORE_SAMPLE_TAIL": "true", "LOGCORE_SAMPLE_BUFFER_SIZE": "50"},
            clear=False,
        ):
            s = sampler_from_env()
        assert s is not None
        assert s.tail_based is True
        assert s.tail_buffer_size == 50

    def test_env_always_keep_uppercased(self) -> None:
        with patch.dict(
            os.environ, {"LOGCORE_SAMPLE_ALWAYS_KEEP": "error,critical"}, clear=False
        ):
            s = sampler_from_env()
        assert s is not None
        assert s.always_keep == frozenset({"ERROR", "CRITICAL"})

    def test_no_env_vars_returns_none(self) -> None:
        keys = (
            "LOGCORE_SAMPLE_RATE",
            "LOGCORE_SAMPLE_TAIL",
            "LOGCORE_SAMPLE_BUFFER_SIZE",
            "LOGCORE_SAMPLE_ALWAYS_KEEP",
        )
        with patch.dict(os.environ, {}, clear=False):
            for k in keys:
                os.environ.pop(k, None)
            assert sampler_from_env() is None

    def test_invalid_rate_warns_and_falls_back(self) -> None:
        with patch.dict(
            os.environ, {"LOGCORE_SAMPLE_RATE": "not-a-number"}, clear=False
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                s = sampler_from_env()

        assert s is not None
        assert s.rate == 1.0  # default
        # Falling back silently meant a typo shipped 100% of logs in
        # production with nothing to point at.
        assert any("LOGCORE_SAMPLE_RATE" in str(w.message) for w in caught)


class TestStats:
    def test_stats_track_kept_dropped_buffered_flushed(self) -> None:
        log = get_logger("test-stats", sampler=Sampler(rate=0.0, tail_based=True))
        _attach_collector(log)
        with log.with_correlation_id("s-1"):
            log.info("a")
            log.info("b")
            log.error("boom")
        stats = log.sampler.stats()
        assert stats.buffered == 2  # two INFOs buffered
        assert stats.flushed == 2  # both flushed on error
        assert stats.kept == 1  # the ERROR itself
        assert stats.discarded == 0  # nothing dropped at exit (already flushed)
        assert stats.active_buffers == 0

    def test_stats_track_discarded_on_clean_exit(self) -> None:
        log = get_logger(
            "test-stats-discard", sampler=Sampler(rate=0.0, tail_based=True)
        )
        _attach_collector(log)
        with log.with_correlation_id("s-clean"):
            log.info("a")
            log.info("b")
            log.info("c")
        stats = log.sampler.stats()
        assert stats.buffered == 3
        assert stats.flushed == 0
        assert stats.discarded == 3  # all dropped at clean exit
        # Invariant: buffered == flushed + discarded + buffered_records
        assert (
            stats.buffered == stats.flushed + stats.discarded + stats.buffered_records
        )
