"""Small, best-effort research timers; no files, payloads or run state."""

import logging
import time
from contextlib import contextmanager
from functools import wraps

LOGGER = logging.getLogger(__name__)


def _clock():
    try:
        return time.perf_counter()
    except Exception:
        return None


def _log(name, event, seconds=None):
    try:
        LOGGER.warning("[timing] %s %s elapsed_seconds=%s", name, event, seconds)
    except Exception:
        # A broken log handler must not replace a business result or exception.
        pass


def log_validation_failure(stage, attempt, errors):
    """Log concise validation errors without affecting retry behavior."""
    try:
        LOGGER.warning(
            "[retry] stage=%s attempt=%s validation_errors=%s",
            stage,
            attempt,
            " | ".join(str(error) for error in errors[:30]),
        )
    except Exception:
        pass


@contextmanager
def timing_span(name):
    _log(name, "start")
    started = _clock()
    returned = False
    try:
        yield
        returned = True
    finally:
        ended = _clock()
        elapsed = ended - started if started is not None and ended is not None else None
        _log(name, "returned" if returned else "raised", elapsed)


def timed_stage(name):
    """Wrap a synchronous research stage without changing args or its result."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with timing_span(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate
