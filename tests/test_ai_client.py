"""Tests for ai_client module."""

import pytest

from udemy_sage.ai_client import (
    AIError,
    _is_retryable_error,
    _with_retry,
    estimate_cost,
)


class TestEstimateCost:
    def test_openai(self):
        cost = estimate_cost("openai", 1_000_000)
        assert cost == pytest.approx(0.15)

    def test_ollama_free(self):
        assert estimate_cost("ollama", 1_000_000) == 0.0

    def test_unknown_provider(self):
        assert estimate_cost("unknown", 1_000_000) == 0.0


class TestRetry:
    def test_success_first_try(self):
        result = _with_retry(lambda: ("content", 100))
        assert result == ("content", 100)

    def test_raises_ai_error_directly(self):
        def fail():
            raise AIError("bad provider")
        with pytest.raises(AIError, match="bad provider"):
            _with_retry(fail)

    def test_non_retryable_fails_immediately(self):
        calls = {"count": 0}

        def fail():
            calls["count"] += 1
            raise ValueError("some random error")

        with pytest.raises(AIError):
            _with_retry(fail)
        assert calls["count"] == 1

    def test_retryable_error_retries(self):
        calls = {"count": 0}

        def fail_then_succeed():
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("HTTP 429 rate limited")
            return ("ok", 50)

        result = _with_retry(fail_then_succeed)
        assert result == ("ok", 50)
        assert calls["count"] == 3

    def test_retryable_by_status_code_without_message_substring(self):
        class QuietHttpError(Exception):
            def __init__(self) -> None:
                super().__init__("")
                self.status_code = 503

        calls = {"n": 0}

        def fail_then_ok():
            calls["n"] += 1
            if calls["n"] < 2:
                raise QuietHttpError()
            return ("ok", 10)

        result = _with_retry(fail_then_ok)
        assert result == ("ok", 10)
        assert calls["n"] == 2


class TestIsRetryableError:
    def test_status_code_429_empty_str(self):
        class StubRateLimitedError(Exception):
            def __init__(self) -> None:
                super().__init__("")
                self.status_code = 429

        assert _is_retryable_error(StubRateLimitedError()) is True

    def test_status_code_400_not_retryable(self):
        class StubBadRequestError(Exception):
            def __init__(self) -> None:
                super().__init__("")
                self.status_code = 400

        assert _is_retryable_error(StubBadRequestError()) is False
