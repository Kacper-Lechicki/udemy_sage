"""Udemy API client for downloading course subtitles."""

from __future__ import annotations

import json
import logging
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import browser_cookie3  # type: ignore[import-untyped]

from udemy_sage.parser import (
    Course,
    Lesson,
    Section,
    estimate_duration,
    parse_vtt,
)

logger = logging.getLogger(__name__)

UDEMY_DOMAIN = ".udemy.com"
UDEMY_API_BASE = "https://www.udemy.com/api-2.0"

BROWSER_LOADERS = {
    "chrome": browser_cookie3.chrome,
    "firefox": browser_cookie3.firefox,
    "safari": browser_cookie3.safari,
    "edge": browser_cookie3.edge,
    "brave": browser_cookie3.brave,
    "opera": browser_cookie3.opera,
}


class FetchError(Exception):
    """Raised when fetching course data fails."""


def _load_browser_cookies(browser: str) -> dict[str, str]:
    """Load Udemy cookies from the specified browser."""
    loader = BROWSER_LOADERS.get(browser)
    if loader is None:
        allowed = list(BROWSER_LOADERS)
        raise FetchError(
            f"Unsupported browser: {browser}. Use one of: {allowed}",
        )

    try:
        jar = loader(domain_name=UDEMY_DOMAIN)
    except OSError as exc:
        raise FetchError(
            f"Failed to load cookies from {browser}: {exc}",
        ) from exc

    return {cookie.name: cookie.value for cookie in jar}


def _extract_cookies(browser: str) -> dict[str, str]:
    """Extract and validate Udemy cookies from the browser."""
    cookies = _load_browser_cookies(browser)
    if "access_token" not in cookies:
        raise FetchError(
            f"No Udemy access_token found in {browser} cookies. "
            "Please log in to Udemy in your browser first."
        )
    return cookies


def _build_headers(cookies: dict[str, str]) -> dict[str, str]:
    """Build authenticated request headers for the Udemy API."""
    token = cookies.get("access_token", "")
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "Authorization": f"Bearer {token}",
        "Cookie": cookie_str,
        "User-Agent": "udemy-sage/0.1",
        "Accept": "application/json",
    }


def _api_get(url: str, cookies: dict[str, str]) -> dict:
    """Perform an authenticated GET request to the Udemy API."""
    headers = _build_headers(cookies)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FetchError(
                    "Udemy API returned a response that is not valid JSON. "
                    "Try again later or check your network connection."
                ) from exc
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise FetchError(
                f"Access denied ({exc.code}). Your session may have expired. "
                "Please log in to Udemy again."
            ) from exc
        raise FetchError(f"API request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Network error: {exc.reason}") from exc


def _extract_course_slug(url: str) -> str:
    """Extract the course slug from a Udemy URL."""
    match = re.search(r"/course/([^/?#]+)", url)
    if not match:
        raise FetchError(f"Cannot extract course slug from URL: {url}")
    return match.group(1)


def _fetch_course_id(slug: str, cookies: dict[str, str]) -> tuple[int, str]:
    """Fetch the course ID and title from the Udemy API."""
    url = f"{UDEMY_API_BASE}/courses/{slug}/?fields[course]=id,title"
    data = _api_get(url, cookies)
    return data["id"], data["title"]


def _fetch_curriculum(course_id: int, cookies: dict[str, str]) -> list[dict]:
    """Fetch the full course curriculum, handling pagination."""
    items: list[dict] = []
    url: str | None = (
        f"{UDEMY_API_BASE}/courses/{course_id}/subscriber-curriculum-items/"
        f"?page_size=200&fields[chapter]=title,object_index"
        f"&fields[lecture]=title,object_index,asset"
        f"&fields[asset]=captions"
    )
    while url:
        data = _api_get(url, cookies)
        items.extend(data.get("results", []))
        url = data.get("next")
    return items


def _find_english_caption(captions: list[dict]) -> str | None:
    """Find the URL for an English caption track."""
    for cap in captions:
        locale = cap.get("locale_id", "") or cap.get("video_label", "")
        if locale.startswith("en"):
            return cap.get("url")
    return None


def _short_url(url: str) -> str:
    """URL without query string for logs."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}" if p.scheme else url


def _is_allowed_caption_url(url: str) -> bool:
    """Only HTTPS to Udemy hosts (defense in depth vs unexpected API data)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host == "udemy.com" or host.endswith(".udemy.com"):
        return True
    if host == "udemycdn.com" or host.endswith(".udemycdn.com"):
        return True
    return False


def _download_vtt(url: str, output_path: Path) -> bool:
    """Download a VTT file. Returns True if a non-empty file was written."""
    if not _is_allowed_caption_url(url):
        logger.warning(
            "Skipping caption download: URL host is not allowed (%s)",
            _short_url(url),
        )
        return False
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "udemy-sage/0.1"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            output_path.write_bytes(resp.read())
    except OSError as exc:
        logger.warning("VTT download failed (%s): %s", _short_url(url), exc)
        return False
    return output_path.exists() and output_path.stat().st_size > 0


# pylint: disable-next=too-many-locals
def build_course(url: str, cookies_browser: str) -> Course:
    """Build a Course from Udemy API data and downloaded subtitles."""
    slug = _extract_course_slug(url)
    cookies = _extract_cookies(cookies_browser)
    course_id, course_title = _fetch_course_id(slug, cookies)
    items = _fetch_curriculum(course_id, cookies)

    course = Course(title=course_title, url=url, url_slug=slug)
    current_section: Section | None = None
    section_index = 0
    lesson_index = 0
    has_captions = False

    with tempfile.TemporaryDirectory(prefix="udemy_sage_") as tmpdir:
        tmp = Path(tmpdir)

        for item in items:
            item_type = item.get("_class")

            if item_type == "chapter":
                section_index += 1
                current_section = Section(
                    index=section_index,
                    title=item.get("title", f"Section {section_index}"),
                )
                course.sections.append(current_section)

            elif item_type == "lecture":
                lesson_index += 1

                if current_section is None:
                    section_index += 1
                    current_section = Section(
                        index=section_index,
                        title="General",
                    )
                    course.sections.append(current_section)

                asset = item.get("asset", {}) or {}
                captions = asset.get("captions", []) or []
                caption_url = _find_english_caption(captions)

                transcript = ""
                duration = "unknown"

                if caption_url:
                    has_captions = True
                    vtt_file = tmp / f"{lesson_index:04d}.vtt"
                    if _download_vtt(caption_url, vtt_file):
                        vtt_content = vtt_file.read_text(encoding="utf-8")
                        transcript = parse_vtt(vtt_content)
                        duration = estimate_duration(vtt_content)

                lesson = Lesson(
                    index=lesson_index,
                    title=item.get("title", f"Lesson {lesson_index}"),
                    transcript=transcript,
                    duration=duration,
                )
                current_section.lessons.append(lesson)

    if not has_captions:
        raise FetchError(
            "No English subtitles found for any lecture in this course.",
        )

    has_text = any(
        lesson.transcript.strip()
        for s in course.sections
        for lesson in s.lessons
    )
    if not has_text:
        raise FetchError(
            "English subtitles are listed for this course but none could "
            "be downloaded or parsed. "
            "Check your network connection and try again.",
        )

    return course
