"""Tests for CLI helpers."""

import pytest

from udemy_sage.cli import _is_valid_udemy_https_url, _validate_udemy_url_text


class TestUdemyUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.udemy.com/course/foo/",
            "https://udemy.com/course/bar/learn/lecture/1",
        ],
    )
    def test_valid_udemy_urls(self, url):
        assert _is_valid_udemy_https_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://www.udemy.com/course/foo/",
            "https://example.com/course/foo/",
            "https://notudemy.com/course/foo/",
            "ftp://www.udemy.com/course/foo/",
        ],
    )
    def test_invalid_urls(self, url):
        assert _is_valid_udemy_https_url(url) is False

    def test_validate_text_accepts_good(self):
        url = "https://www.udemy.com/course/x/"
        assert _validate_udemy_url_text(url) is True

    def test_validate_text_rejects_non_https(self):
        msg = _validate_udemy_url_text("http://www.udemy.com/course/x/")
        assert msg is not True
        assert "https" in str(msg).lower()

    def test_validate_text_rejects_wrong_host(self):
        msg = _validate_udemy_url_text("https://example.com/")
        assert msg is not True
