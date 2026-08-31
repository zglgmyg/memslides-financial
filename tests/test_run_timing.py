from __future__ import annotations

import logging

import pytest

from memslides.utils import run_timing as timing


def test_stage_preserves_arguments_and_result_without_creating_files(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING, logger=timing.LOGGER.name)
    clock = iter([10.0, 13.5])
    monkeypatch.setattr(timing.time, "perf_counter", lambda: next(clock))
    argument, result = object(), object()

    @timing.timed_stage("research.test")
    def original(value, *, flag):
        assert value is argument and flag is True
        return result

    assert original(argument, flag=True) is result
    assert original.__name__ == "original"
    assert list(tmp_path.iterdir()) == []
    assert "research.test start" in caplog.text
    assert "research.test returned elapsed_seconds=3.5" in caplog.text


@pytest.mark.parametrize("error", [RuntimeError("business failure"), KeyError("missing"), KeyboardInterrupt()])
def test_business_exception_is_not_swallowed_or_replaced(error, caplog):
    with pytest.raises(type(error)) as caught:
        with timing.timing_span("research.test"):
            raise error
    assert caught.value is error
    assert "research.test raised" in caplog.text
    assert "business failure" not in caplog.text


@pytest.mark.parametrize("component", ["logger", "clock"])
def test_timing_failure_preserves_result_and_business_exception(monkeypatch, component):
    def broken(*args, **kwargs):
        raise OSError("timing failure")

    if component == "logger":
        monkeypatch.setattr(timing.LOGGER, "warning", broken)
    else:
        monkeypatch.setattr(timing.time, "perf_counter", broken)
    result = object()

    @timing.timed_stage("research.test")
    def success():
        return result

    assert success() is result
    original = ValueError("original business error")
    with pytest.raises(ValueError) as caught:
        with timing.timing_span("research.test"):
            raise original
    assert caught.value is original
