"""Markdown file generation and vault structure management."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from udemy_sage.config import log_error
from udemy_sage.parser import Course, Lesson, Section


def _yaml_double_quoted(s: str) -> str:
    """YAML double-quoted string; escapes quotes, backslashes, newlines."""
    parts: list[str] = ['"']
    for ch in s:
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif ch == "\n":
            parts.append("\\n")
        elif ch == "\r":
            parts.append("\\r")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def render_course(course: Course, vault_path: Path) -> list[str]:
    """Render all notes for a course. Returns list of skipped lesson IDs."""
    base = vault_path / "resources" / "udemy" / course.slug
    skipped: list[str] = []

    for section in course.sections:
        section_dir = base / section.dirname
        section_dir.mkdir(parents=True, exist_ok=True)

        for lesson in section.lessons:
            lesson_id = f"{course.slug}/{section.dirname}/{lesson.filename}"
            filepath = section_dir / lesson.filename

            if filepath.exists():
                skipped.append(lesson_id)
                continue

            if not lesson.transcript:
                log_error(lesson_id, "No transcript available")
                continue

            content = _render_note(course, section, lesson)
            filepath.write_text(content, encoding="utf-8")

    _render_index(course, base)
    return skipped


def _render_note(course: Course, section: Section, lesson: Lesson) -> str:
    """Render a single lesson note with frontmatter."""
    lesson_label = f"{lesson.index:03d} - {lesson.title}"
    frontmatter = (
        f"---\n"
        f"course: {_yaml_double_quoted(course.title)}\n"
        f"section: {_yaml_double_quoted(section.dirname)}\n"
        f"lesson: {_yaml_double_quoted(lesson_label)}\n"
        f"duration: {_yaml_double_quoted(lesson.duration)}\n"
        f"generated: {date.today().isoformat()}\n"
        f"---\n"
    )

    parent_link = f"\nparent:: [[{course.slug}]]\n"

    # The AI-generated content already contains the ## headings
    body = lesson.transcript if lesson.transcript else ""

    return f"{frontmatter}{parent_link}\n{body}\n"


def _render_index(course: Course, base: Path) -> str:
    """Generate a Dataview-compatible index file for the course."""
    lines = [
        "---",
        f"course: {_yaml_double_quoted(course.title)}",
        "type: course-index",
        f"generated: {date.today().isoformat()}",
        "---",
        "",
        f"# {course.title}",
        "",
        "```dataview",
        "TABLE section, lesson, duration",
        f'FROM "resources/udemy/{course.slug}"',
        "SORT file.name ASC",
        "```",
        "",
        "## Sections",
        "",
    ]

    for section in course.sections:
        lines.append(f"### {section.dirname}")
        for lesson in section.lessons:
            if lesson.transcript.strip():
                lines.append(f"- [[{lesson.filename[:-3]}]]")
            else:
                lines.append("- *(No subtitles — skipped)*")
        lines.append("")

    content = "\n".join(lines)
    index_path = base / f"{course.slug}.md"
    index_path.write_text(content, encoding="utf-8")
    return content
