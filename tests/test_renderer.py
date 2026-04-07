"""Tests for renderer module."""

import pytest

import udemy_sage.config as cfg
from udemy_sage.parser import Course, Lesson, Section
from udemy_sage.renderer import _yaml_double_quoted, render_course


@pytest.fixture(name="sample_course")
def bootcamp_course_fixture():
    return Course(
        title="Python Bootcamp",
        url="https://udemy.com/course/python-bootcamp",
        sections=[
            Section(
                index=1,
                title="Introduction",
                lessons=[
                    Lesson(
                        index=1,
                        title="Welcome",
                        transcript="## Summary\nThis is a welcome lesson.",
                        duration="5min",
                    ),
                ],
            ),
            Section(
                index=2,
                title="Basics",
                lessons=[
                    Lesson(
                        index=2,
                        title="Variables",
                        transcript="## Summary\nVariables store data.",
                        duration="10min",
                    ),
                ],
            ),
        ],
    )


class TestRenderer:
    def test_creates_files(self, tmp_path, sample_course):
        render_course(sample_course, tmp_path)
        base = tmp_path / "resources" / "udemy" / "python_bootcamp"
        assert (base / "01_introduction" / "001_welcome.md").exists()
        assert (base / "02_basics" / "002_variables.md").exists()

    def test_note_content(self, tmp_path, sample_course):
        render_course(sample_course, tmp_path)
        note = (
            tmp_path / "resources" / "udemy" / "python_bootcamp"
            / "01_introduction" / "001_welcome.md"
        ).read_text()
        assert 'course: "Python Bootcamp"' in note
        assert "parent:: [[python_bootcamp]]" in note
        assert "## Summary" in note

    def test_skip_existing(self, tmp_path, sample_course):
        render_course(sample_course, tmp_path)
        skipped = render_course(sample_course, tmp_path)
        assert len(skipped) == 2

    def test_index_file(self, tmp_path, sample_course):
        render_course(sample_course, tmp_path)
        index_path = (
            tmp_path
            / "resources"
            / "udemy"
            / "python_bootcamp"
            / "python_bootcamp.md"
        )
        index = index_path.read_text()
        assert "type: course-index" in index
        assert "dataview" in index.lower()

    def test_index_skips_wikilink_without_transcript(self, tmp_path):
        course = Course(
            title="Mixed",
            url="https://udemy.com/course/mixed",
            sections=[
                Section(
                    index=1,
                    title="S1",
                    lessons=[
                        Lesson(
                            index=1,
                            title="With subs",
                            transcript="## Summary\nok",
                            duration="1min",
                        ),
                        Lesson(
                            index=2,
                            title="No subs",
                            transcript="",
                            duration="0min",
                        ),
                    ],
                ),
            ],
        )
        render_course(course, tmp_path)
        mixed_index = tmp_path / "resources" / "udemy" / "mixed" / "mixed.md"
        index = mixed_index.read_text()
        assert "[[001_with_subs]]" in index
        assert "No subtitles" in index
        assert "002_no_subs" not in index

    def test_empty_transcript_skipped(self, tmp_path, monkeypatch):
        """Lessons without transcript should be skipped with error logged."""
        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / ".cfg")
        monkeypatch.setattr(cfg, "ERROR_LOG", tmp_path / ".cfg" / "errors.log")

        course = Course(
            title="Test",
            url="https://udemy.com/course/test",
            sections=[
                Section(
                    index=1,
                    title="S1",
                    lessons=[
                        Lesson(
                            index=1,
                            title="Empty",
                            transcript="",
                            duration="0min",
                        ),
                    ],
                ),
            ],
        )
        render_course(course, tmp_path)
        note_path = (
            tmp_path
            / "resources"
            / "udemy"
            / "test"
            / "01_s1"
            / "001_empty.md"
        )
        assert not note_path.exists()


class TestYamlEscaping:
    def test_yaml_double_quoted_escapes_quotes_and_newlines(self):
        s = 'He said "hi"\nline2'
        assert _yaml_double_quoted(s) == r'"He said \"hi\"\nline2"'

    def test_frontmatter_with_special_characters(self, tmp_path):
        course = Course(
            title='Course: "Advanced" tips',
            url="https://udemy.com/course/x",
            sections=[
                Section(
                    index=1,
                    title="Intro",
                    lessons=[
                        Lesson(
                            index=1,
                            title='Lesson "A"',
                            transcript="## Summary\nok",
                            duration="5min",
                        ),
                    ],
                ),
            ],
        )
        render_course(course, tmp_path)
        note = (
            tmp_path / "resources" / "udemy" / "course_advanced_tips"
            / "01_intro" / '001_lesson_a.md'
        ).read_text(encoding="utf-8")
        assert 'course: "Course: \\"Advanced\\" tips"' in note
        assert 'lesson: "001 - Lesson \\"A\\""' in note
