"""Data models and VTT subtitle parsing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Lesson:
    index: int
    title: str
    vtt_path: str | None = None
    transcript: str = ""
    duration: str = ""

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def filename(self) -> str:
        return f"{self.index:03d}_{self.slug}.md"


@dataclass
class Section:
    index: int
    title: str
    lessons: list[Lesson] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def dirname(self) -> str:
        return f"{self.index:02d}_{self.slug}"


@dataclass
class Course:
    title: str
    url: str
    sections: list[Section] = field(default_factory=list)
    #: Udemy `/course/<slug>/`; used for vault paths (avoids title collisions).
    url_slug: str | None = None

    @property
    def slug(self) -> str:
        if self.url_slug:
            return slugify(self.url_slug)
        return slugify(self.title)

    @property
    def total_lessons(self) -> int:
        return sum(len(s.lessons) for s in self.sections)


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.strip("_")


def parse_vtt(content: str) -> str:
    """Plain text from VTT: drop timestamps, cues, tags; deduplicate lines."""
    lines: list[str] = []
    seen: set[str] = set()

    for line in content.splitlines():
        line = line.strip()
        # Skip empty lines, WEBVTT header, cue identifiers, timestamps
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->", line):
            continue
        # Strip HTML-like tags (e.g., <c>, </c>, <b>)
        cleaned = re.sub(r"<[^>]+>", "", line).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            lines.append(cleaned)

    return " ".join(lines)


def estimate_duration(vtt_content: str) -> str:
    """Estimate lesson duration from VTT timestamps."""
    timestamps = re.findall(r"(\d{2}):(\d{2}):(\d{2})\.\d{3}", vtt_content)
    if not timestamps:
        return "unknown"
    last = timestamps[-1]
    total_seconds = int(last[0]) * 3600 + int(last[1]) * 60 + int(last[2])
    minutes = max(1, round(total_seconds / 60))
    return f"{minutes}min"
