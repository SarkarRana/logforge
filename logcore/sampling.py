"""Log sampling for LogCore.

Provides a single configurable ``Sampler`` that combines three strategies:

- **Level-aware**: records whose level name is in ``always_keep`` are never
  sampled. Defaults to ``{"WARNING", "ERROR", "CRITICAL"}``, which is
  equivalent to "never sample anything at or above WARNING".
- **Tail-based**: when a correlation_id is set, records are buffered; the
  first record whose level is in ``always_keep`` retroactively flushes the
  buffer (capturing the full request history) and switches the cid into
  pass-through mode. If the request ends cleanly without such a record, the
  buffer is discarded.
- **Rate-based**: a fallback used when tail-based is off or no correlation_id
  is present.

The buffer is bounded per correlation_id (ring buffer) so a misbehaving
request cannot consume unbounded memory.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import warnings
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import Enum
from logging import LogRecord
from typing import Deque, Dict, FrozenSet, List, Optional, Set

from .utils import get_correlation_id

DEFAULT_ALWAYS_KEEP: FrozenSet[str] = frozenset({"WARNING", "ERROR", "CRITICAL"})
DEFAULT_TAIL_BUFFER_SIZE = 100

#: Cap on simultaneously tracked correlation_ids. Reached only by callers that
#: never close out a correlation scope; past it the least recently used buffer
#: is evicted rather than growing forever.
DEFAULT_MAX_ACTIVE_BUFFERS = 1000


def _level_to_number(level: str) -> int:
    """Map a level name to its numeric value, tolerating aliases."""
    resolved = logging.getLevelName(level.upper())
    if isinstance(resolved, int):
        return resolved
    if level.upper() in ("WARN",):  # pragma: no cover - alias safety net
        return logging.WARNING
    return logging.NOTSET


class Decision(Enum):
    """The action a Sampler decides to take for a given LogRecord."""

    KEEP = "keep"
    DROP = "drop"
    BUFFER = "buffer"


@dataclass
class SamplerStats:
    """Snapshot of sampler runtime state.

    Lifetime counters (since sampler creation):

    - ``kept``: records emitted directly (passed through sampling).
    - ``dropped``: records dropped by rate-based sampling.
    - ``buffered``: records that entered the tail-based buffer (regardless of
      eventual fate). ``buffered == flushed + discarded + buffered_records``.
    - ``flushed``: buffered records that were later emitted via an
      ``always_keep`` flush.
    - ``discarded``: buffered records that were dropped at clean request exit
      (no error fired).
    - ``dropped_overflow``: records evicted from a full buffer (ring-buffer
      overflow before flush/discard).
    - ``evicted_buffers``: whole correlation_id buffers dropped because
      ``max_active_buffers`` was exceeded. A non-zero value means correlation
      scopes are not being closed — see ``flush_sample_buffer``.

    Live state:

    - ``active_buffers``: number of correlation_ids currently buffering.
    - ``buffered_records``: total records currently sitting in buffers.
    """

    active_buffers: int
    buffered_records: int
    dropped_overflow: int
    kept: int
    dropped: int
    buffered: int
    flushed: int
    discarded: int
    evicted_buffers: int = 0


class Sampler:
    """Decides whether to emit, drop, or buffer log records.

    The full evaluation order inside :meth:`decide`:

    1. If ``record.levelname`` is in ``always_keep`` → ``KEEP``.
    2. If ``tail_based`` is on AND a correlation_id is set: when the cid has
       already been flushed (an earlier error in this request drained the
       buffer) → ``KEEP`` (pass-through mode); otherwise → ``BUFFER``.
    3. Otherwise, a random draw against ``rate`` → ``KEEP`` or ``DROP``.

    The companion method :meth:`flush_pending` returns any buffered records
    that should be emitted alongside a kept ``always_keep`` record; the caller
    (LogCoreLogger) is expected to invoke it and emit those records before
    emitting the triggering record itself.

    Args:
        rate: Fraction of non-always-keep records to emit when rate-based
            sampling applies. Must be in [0.0, 1.0]. Defaults to 1.0 (keep all).
        always_keep: Level names that are never sampled. Defaults to
            {"WARNING", "ERROR", "CRITICAL"}.
        tail_based: When True, buffer records under the current correlation_id
            and flush on the first always_keep record. Defaults to False.
        tail_buffer_size: Max records buffered per correlation_id before the
            oldest is evicted. Defaults to 100.
        _rng: Optional ``random.Random`` for deterministic tests.
    """

    def __init__(
        self,
        rate: float = 1.0,
        always_keep: Optional[Set[str]] = None,
        tail_based: bool = False,
        tail_buffer_size: int = DEFAULT_TAIL_BUFFER_SIZE,
        max_active_buffers: int = DEFAULT_MAX_ACTIVE_BUFFERS,
        _rng: Optional[random.Random] = None,
    ) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"rate must be in [0.0, 1.0], got {rate}")
        if tail_buffer_size < 1:
            raise ValueError(f"tail_buffer_size must be >= 1, got {tail_buffer_size}")
        if max_active_buffers < 1:
            raise ValueError(
                f"max_active_buffers must be >= 1, got {max_active_buffers}"
            )

        self.rate = rate
        # Normalize to uppercase so `always_keep={"warning"}` matches
        # `record.levelname == "WARNING"`. Without this, a lowercase entry
        # would silently never match — a sharp footgun.
        self.always_keep: FrozenSet[str] = (
            frozenset(level.upper() for level in always_keep)
            if always_keep is not None
            else DEFAULT_ALWAYS_KEEP
        )
        # Numeric levels: comparing ints beats comparing strings on the hot
        # path, and it makes custom/aliased level names work.
        self._always_keep_levelnos: FrozenSet[int] = frozenset(
            _level_to_number(level) for level in self.always_keep
        )
        self.tail_based = tail_based
        self.tail_buffer_size = tail_buffer_size
        self.max_active_buffers = max_active_buffers
        self._rng = _rng if _rng is not None else random.Random()

        # Bounded and insertion-ordered so the oldest correlation_id can be
        # evicted. Plain dicts here grew without limit for any caller using
        # set_correlation_id() instead of the with_correlation_id() context
        # manager, i.e. one leaked deque per request, forever.
        self._buffers: "OrderedDict[str, Deque[LogRecord]]" = OrderedDict()
        self._flushed: "OrderedDict[str, None]" = OrderedDict()
        self._lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "kept": 0,
            "dropped": 0,
            "buffered": 0,
            "flushed": 0,
            "discarded": 0,
            "dropped_overflow": 0,
            "evicted_buffers": 0,
        }

    def decide_early(self, levelno: int, cid: Optional[str]) -> Decision:
        """Return the sampling decision without needing a built LogRecord.

        Called before the record exists so a DROP costs nothing beyond a level
        comparison and possibly one RNG draw — previously a dropped record
        still paid for frame inspection, makeRecord and field coercion.

        Takes the lock at most once; the counter bumps used to acquire it a
        second and third time per call.
        """
        if levelno in self._always_keep_levelnos:
            with self._lock:
                self._stats["kept"] += 1
            return Decision.KEEP

        if self.tail_based and cid is not None:
            with self._lock:
                if cid in self._flushed:
                    self._flushed.move_to_end(cid)
                    self._stats["kept"] += 1
                    return Decision.KEEP
            return Decision.BUFFER

        if self.rate >= 1.0:
            keep = True
        elif self.rate <= 0.0:
            keep = False
        else:
            keep = self._rng.random() < self.rate

        with self._lock:
            self._stats["kept" if keep else "dropped"] += 1
        return Decision.KEEP if keep else Decision.DROP

    def decide(self, record: LogRecord) -> Decision:
        """Return the sampling decision for ``record``.

        Retained for callers holding a record; :meth:`decide_early` is the hot
        path. Note this does *not* bump the kept/dropped counters, matching the
        original contract where the logger bumped them separately.
        """
        levelno = getattr(record, "levelno", None)
        if levelno is None:  # pragma: no cover - defensive
            levelno = _level_to_number(record.levelname)

        if levelno in self._always_keep_levelnos:
            return Decision.KEEP

        cid = get_correlation_id()

        if self.tail_based and cid is not None:
            with self._lock:
                if cid in self._flushed:
                    return Decision.KEEP
            return Decision.BUFFER

        if self.rate >= 1.0:
            return Decision.KEEP
        if self.rate <= 0.0:
            return Decision.DROP
        return Decision.KEEP if self._rng.random() < self.rate else Decision.DROP

    def buffer(self, record: LogRecord, cid: Optional[str] = None) -> None:
        """Append ``record`` to the ring buffer for ``cid``.

        Silently does nothing if no correlation_id is set (defensive — the
        logger should only call this after a BUFFER decision). Drops the oldest
        record if the buffer is full, and evicts the least recently used
        correlation_id once ``max_active_buffers`` is reached.
        """
        if cid is None:
            cid = get_correlation_id()
        if cid is None:
            return

        with self._lock:
            buf = self._buffers.get(cid)
            if buf is None:
                buf = deque(maxlen=self.tail_buffer_size)
                self._buffers[cid] = buf
                self._evict_locked()
            else:
                self._buffers.move_to_end(cid)
            if len(buf) == self.tail_buffer_size:
                self._stats["dropped_overflow"] += 1
            buf.append(record)
            self._stats["buffered"] += 1

    def _evict_locked(self) -> None:
        """Drop least-recently-used buffers past the cap. Caller holds the lock."""
        while len(self._buffers) > self.max_active_buffers:
            _, evicted = self._buffers.popitem(last=False)
            self._stats["discarded"] += len(evicted)
            self._stats["evicted_buffers"] += 1
        while len(self._flushed) > self.max_active_buffers:
            self._flushed.popitem(last=False)

    def flush_pending(
        self, record: LogRecord, cid: Optional[str] = None
    ) -> List[LogRecord]:
        """Return buffered records to emit alongside ``record``.

        Returns a non-empty list only when tail-based is on, the record's
        level is in ``always_keep``, and there are buffered records for the
        current correlation_id. Marks the cid as flushed so subsequent
        records pass through directly.
        """
        if not self.tail_based:
            return []

        levelno = getattr(record, "levelno", None)
        if levelno is None:  # pragma: no cover - defensive
            levelno = _level_to_number(record.levelname)
        if levelno not in self._always_keep_levelnos:
            return []

        if cid is None:
            cid = get_correlation_id()
        if cid is None:
            return []

        with self._lock:
            buf = self._buffers.pop(cid, None)
            self._flushed[cid] = None
            self._flushed.move_to_end(cid)
            self._evict_locked()
            if buf is None:
                return []
            records = list(buf)
            self._stats["flushed"] += len(records)
            return records

    def discard_buffer(self, correlation_id: str) -> int:
        """Drop the buffer for ``correlation_id`` without emitting.

        Called by :meth:`LogCoreLogger.with_correlation_id` on clean exit.
        Returns the number of records discarded. Safe to call when no buffer
        exists.
        """
        with self._lock:
            buf = self._buffers.pop(correlation_id, None)
            self._flushed.pop(correlation_id, None)
            n = len(buf) if buf is not None else 0
            self._stats["discarded"] += n
            return n

    def _record_kept(self) -> None:
        """Internal hook: increment the 'kept' stat (called by LogCoreLogger)."""
        with self._lock:
            self._stats["kept"] += 1

    def _record_dropped(self) -> None:
        """Internal hook: increment the 'dropped' stat (called by LogCoreLogger)."""
        with self._lock:
            self._stats["dropped"] += 1

    def stats(self) -> SamplerStats:
        """Return a snapshot of sampler counters and buffer state."""
        with self._lock:
            return SamplerStats(
                active_buffers=len(self._buffers),
                buffered_records=sum(len(b) for b in self._buffers.values()),
                dropped_overflow=self._stats["dropped_overflow"],
                kept=self._stats["kept"],
                dropped=self._stats["dropped"],
                buffered=self._stats["buffered"],
                flushed=self._stats["flushed"],
                discarded=self._stats["discarded"],
                evicted_buffers=self._stats["evicted_buffers"],
            )


def sampler_from_env() -> Optional[Sampler]:
    """Construct a Sampler from ``LOGCORE_SAMPLE_*`` env vars.

    Returns None if no sampling env vars are set. Recognises:

    - ``LOGCORE_SAMPLE_RATE`` — float in [0.0, 1.0]
    - ``LOGCORE_SAMPLE_TAIL`` — truthy enables tail-based
    - ``LOGCORE_SAMPLE_BUFFER_SIZE`` — int, max records per correlation_id
    - ``LOGCORE_SAMPLE_ALWAYS_KEEP`` — comma-separated level names
    """
    rate_env = os.getenv("LOGCORE_SAMPLE_RATE")
    tail_env = os.getenv("LOGCORE_SAMPLE_TAIL")
    buffer_env = os.getenv("LOGCORE_SAMPLE_BUFFER_SIZE")
    always_keep_env = os.getenv("LOGCORE_SAMPLE_ALWAYS_KEEP")

    if not any([rate_env, tail_env, buffer_env, always_keep_env]):
        return None

    kwargs: Dict[str, object] = {}
    if rate_env is not None:
        try:
            kwargs["rate"] = float(rate_env)
        except ValueError:
            # Silently defaulting here means shipping 100% of logs in
            # production because of a typo, with nothing to point at.
            warnings.warn(
                f"Ignoring invalid LOGCORE_SAMPLE_RATE={rate_env!r}: "
                "expected a float in [0.0, 1.0]. Using the default.",
                UserWarning,
                stacklevel=3,
            )
    if tail_env is not None:
        kwargs["tail_based"] = tail_env.lower() in ("true", "1", "yes", "on")
    if buffer_env is not None:
        try:
            kwargs["tail_buffer_size"] = int(buffer_env)
        except ValueError:
            warnings.warn(
                f"Ignoring invalid LOGCORE_SAMPLE_BUFFER_SIZE={buffer_env!r}: "
                "expected an integer. Using the default.",
                UserWarning,
                stacklevel=3,
            )
    if always_keep_env is not None:
        kwargs["always_keep"] = {
            level.strip().upper() for level in always_keep_env.split(",")
        }

    return Sampler(**kwargs)  # type: ignore[arg-type]
