"""Tests for fetcher module."""

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from udemy_sage.fetcher import (
    FetchError,
    _api_get,
    _download_vtt,
    _extract_cookies,
    _extract_course_slug,
    _fetch_course_id,
    _fetch_curriculum,
    _find_english_caption,
    _is_allowed_caption_url,
    build_course,
)


class TestExtractCourseSlug:
    def test_standard_url(self):
        url = "https://www.udemy.com/course/complete-python-bootcamp/"
        assert _extract_course_slug(url) == "complete-python-bootcamp"

    def test_url_with_lecture_path(self):
        url = "https://www.udemy.com/course/my-course/learn/lecture/12345"
        assert _extract_course_slug(url) == "my-course"

    def test_invalid_url_raises(self):
        with pytest.raises(FetchError, match="Cannot extract course slug"):
            _extract_course_slug("https://example.com/not-a-course")


class TestExtractCookies:
    @patch("udemy_sage.fetcher._load_browser_cookies")
    def test_success(self, mock_load):
        mock_load.return_value = {"access_token": "abc123", "csrftoken": "xyz"}
        cookies = _extract_cookies("chrome")
        assert cookies["access_token"] == "abc123"
        mock_load.assert_called_once_with("chrome")

    @patch("udemy_sage.fetcher._load_browser_cookies")
    def test_missing_token_raises(self, mock_load):
        mock_load.return_value = {"csrftoken": "xyz"}
        with pytest.raises(FetchError, match="No Udemy access_token"):
            _extract_cookies("chrome")


class TestFetchCourseId:
    @patch("udemy_sage.fetcher._api_get")
    def test_returns_id_and_title(self, mock_api):
        mock_api.return_value = {"id": 12345, "title": "Python Mastery"}
        course_id, title = _fetch_course_id(
            "python-mastery", {"access_token": "t"}
        )
        assert course_id == 12345
        assert title == "Python Mastery"

    @patch("udemy_sage.fetcher._api_get")
    def test_403_raises(self, mock_api):
        mock_api.side_effect = FetchError("Access denied (403)")
        with pytest.raises(FetchError, match="Access denied"):
            _fetch_course_id("locked-course", {"access_token": "t"})


class TestFetchCurriculum:
    @patch("udemy_sage.fetcher._api_get")
    def test_single_page(self, mock_api):
        mock_api.return_value = {
            "results": [{"_class": "chapter", "title": "Intro"}],
            "next": None,
        }
        items = _fetch_curriculum(1, {"access_token": "t"})
        assert len(items) == 1
        assert items[0]["title"] == "Intro"

    @patch("udemy_sage.fetcher._api_get")
    def test_pagination(self, mock_api):
        mock_api.side_effect = [
            {
                "results": [{"_class": "chapter", "title": "Part 1"}],
                "next": "https://www.udemy.com/api-2.0/next-page",
            },
            {
                "results": [{"_class": "lecture", "title": "Lesson 1"}],
                "next": None,
            },
        ]
        items = _fetch_curriculum(1, {"access_token": "t"})
        assert len(items) == 2
        assert items[0]["title"] == "Part 1"
        assert items[1]["title"] == "Lesson 1"


class TestFindEnglishCaption:
    def test_en_us_found(self):
        captions = [
            {"locale_id": "es_ES", "url": "https://www.udemy.com/es.vtt"},
            {"locale_id": "en_US", "url": "https://www.udemy.com/en.vtt"},
        ]
        assert _find_english_caption(captions) == (
            "https://www.udemy.com/en.vtt"
        )

    def test_no_english_returns_none(self):
        captions = [
            {"locale_id": "fr_FR", "url": "https://www.udemy.com/fr.vtt"},
        ]
        assert _find_english_caption(captions) is None


class TestIsAllowedCaptionUrl:
    def test_udemy_com_https(self):
        url = "https://www.udemy.com/captions/x.vtt"
        assert _is_allowed_caption_url(url) is True

    def test_udemycdn_https(self):
        url = "https://video.udemycdn.com/a/b/c.vtt"
        assert _is_allowed_caption_url(url) is True

    def test_http_rejected(self):
        assert _is_allowed_caption_url("http://www.udemy.com/x.vtt") is False

    def test_foreign_host_rejected(self):
        assert _is_allowed_caption_url("https://example.com/sub.vtt") is False


class TestDownloadVtt:
    @patch("udemy_sage.fetcher.urllib.request.urlopen")
    def test_success(self, mock_urlopen, tmp_path):
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello"
        )
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        out = tmp_path / "test.vtt"
        assert _download_vtt("https://www.udemy.com/sub.vtt", out) is True
        assert out.exists()
        assert b"WEBVTT" in out.read_bytes()

    @patch("udemy_sage.fetcher.urllib.request.urlopen")
    def test_network_error_returns_false(self, mock_urlopen, tmp_path):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        out = tmp_path / "test.vtt"
        assert _download_vtt("https://www.udemy.com/sub.vtt", out) is False
        assert not out.exists()

    def test_disallowed_host_skips_download(self, tmp_path):
        out = tmp_path / "test.vtt"
        assert _download_vtt("https://evil.example/sub.vtt", out) is False
        assert not out.exists()


class TestApiGet:  # pylint: disable=too-few-public-methods
    @patch("udemy_sage.fetcher.urllib.request.urlopen")
    def test_invalid_json_raises(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json {"
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(FetchError, match="not valid JSON"):
            _api_get(
                "https://www.udemy.com/api-2.0/test",
                {"access_token": "t"},
            )


class TestBuildCourse:
    VTT_CONTENT = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world.\n\n"
        "00:00:05.000 --> 00:00:10.000\nThis is a test.\n"
    )

    @patch("udemy_sage.fetcher._download_vtt")
    @patch("udemy_sage.fetcher._fetch_curriculum")
    @patch("udemy_sage.fetcher._fetch_course_id")
    @patch("udemy_sage.fetcher._extract_cookies")
    def test_full_build(
        self, mock_cookies, mock_course_id, mock_curriculum, mock_dl
    ):
        mock_cookies.return_value = {"access_token": "tok"}
        mock_course_id.return_value = (42, "Test Course")
        mock_curriculum.return_value = [
            {
                "_class": "chapter",
                "title": "Getting Started",
                "object_index": 1,
            },
            {
                "_class": "lecture",
                "title": "Welcome",
                "object_index": 1,
                "asset": {
                    "captions": [
                        {
                            "locale_id": "en_US",
                            "url": "https://www.udemy.com/en.vtt",
                        },
                    ]
                },
            },
            {
                "_class": "lecture",
                "title": "Setup",
                "object_index": 2,
                "asset": {
                    "captions": [
                        {
                            "locale_id": "en_US",
                            "url": "https://www.udemy.com/en2.vtt",
                        },
                    ]
                },
            },
        ]

        def fake_download(_url, path):
            path.write_text(self.VTT_CONTENT, encoding="utf-8")
            return True

        mock_dl.side_effect = fake_download

        course = build_course(
            "https://www.udemy.com/course/test-course/",
            "chrome",
        )

        assert course.title == "Test Course"
        assert course.url == "https://www.udemy.com/course/test-course/"
        assert course.url_slug == "test-course"
        assert course.slug == "test_course"
        assert len(course.sections) == 1
        assert course.sections[0].title == "Getting Started"
        assert len(course.sections[0].lessons) == 2
        assert course.sections[0].lessons[0].title == "Welcome"
        assert course.sections[0].lessons[0].transcript  # not empty
        assert course.sections[0].lessons[1].title == "Setup"
        assert course.total_lessons == 2

    @patch("udemy_sage.fetcher._download_vtt")
    @patch("udemy_sage.fetcher._fetch_curriculum")
    @patch("udemy_sage.fetcher._fetch_course_id")
    @patch("udemy_sage.fetcher._extract_cookies")
    def test_no_captions_raises(
        self, mock_cookies, mock_course_id, mock_curriculum, _mock_dl
    ):
        mock_cookies.return_value = {"access_token": "tok"}
        mock_course_id.return_value = (42, "Empty Course")
        mock_curriculum.return_value = [
            {"_class": "chapter", "title": "Intro", "object_index": 1},
            {
                "_class": "lecture",
                "title": "Silent Lesson",
                "object_index": 1,
                "asset": {"captions": []},
            },
        ]

        with pytest.raises(FetchError, match="No English subtitles"):
            build_course(
                "https://www.udemy.com/course/empty-course/",
                "chrome",
            )

    @patch("udemy_sage.fetcher._download_vtt")
    @patch("udemy_sage.fetcher._fetch_curriculum")
    @patch("udemy_sage.fetcher._fetch_course_id")
    @patch("udemy_sage.fetcher._extract_cookies")
    def test_caption_urls_but_download_fails_raises(
        self, mock_cookies, mock_course_id, mock_curriculum, mock_dl
    ):
        mock_cookies.return_value = {"access_token": "tok"}
        mock_course_id.return_value = (42, "Broken Course")
        mock_curriculum.return_value = [
            {"_class": "chapter", "title": "Intro", "object_index": 1},
            {
                "_class": "lecture",
                "title": "L1",
                "object_index": 1,
                "asset": {
                    "captions": [
                        {
                            "locale_id": "en_US",
                            "url": "https://www.udemy.com/en.vtt",
                        },
                    ]
                },
            },
        ]
        mock_dl.return_value = False

        with pytest.raises(FetchError, match="none could be downloaded"):
            build_course(
                "https://www.udemy.com/course/broken/",
                "chrome",
            )
