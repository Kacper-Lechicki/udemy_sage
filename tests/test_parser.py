"""Tests for parser module."""

from udemy_sage.parser import (
    Course,
    Lesson,
    Section,
    estimate_duration,
    parse_vtt,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello_world"

    def test_special_chars(self):
        assert slugify("04 - Functions & More!") == "04_functions_more"

    def test_unicode(self):
        assert slugify("Café résumé") == "cafe_resume"

    def test_dashes_and_spaces(self):
        assert slugify("some--thing  weird") == "some_thing_weird"

    def test_empty(self):
        assert slugify("") == ""


class TestParseVtt:
    VTT_SAMPLE = """\
WEBVTT

1
00:00:00.000 --> 00:00:03.500
Welcome to this lesson.

2
00:00:03.500 --> 00:00:07.000
Welcome to this lesson.

3
00:00:07.000 --> 00:00:12.000
Today we'll learn about <b>Python</b> functions.
"""

    def test_extracts_text(self):
        result = parse_vtt(self.VTT_SAMPLE)
        assert "Welcome to this lesson." in result
        assert "Python" in result
        assert "functions" in result

    def test_removes_duplicates(self):
        result = parse_vtt(self.VTT_SAMPLE)
        assert result.count("Welcome to this lesson.") == 1

    def test_strips_html_tags(self):
        result = parse_vtt(self.VTT_SAMPLE)
        assert "<b>" not in result

    def test_removes_timestamps(self):
        result = parse_vtt(self.VTT_SAMPLE)
        assert "-->" not in result

    def test_empty_input(self):
        assert parse_vtt("") == ""
        assert parse_vtt("WEBVTT\n\n") == ""


class TestEstimateDuration:
    def test_basic(self):
        vtt = "00:00:00.000 --> 00:05:30.000\nHello"
        assert estimate_duration(vtt) == "6min"

    def test_short(self):
        vtt = "00:00:00.000 --> 00:00:20.000\nHello"
        assert estimate_duration(vtt) == "1min"

    def test_no_timestamps(self):
        assert estimate_duration("no timestamps here") == "unknown"


class TestDataModels:
    def test_lesson_slug(self):
        lesson = Lesson(index=1, title="Lambda Expressions")
        assert lesson.slug == "lambda_expressions"

    def test_lesson_filename(self):
        lesson = Lesson(index=42, title="Lambda Expressions")
        assert lesson.filename == "042_lambda_expressions.md"

    def test_section_dirname(self):
        section = Section(index=4, title="Functions")
        assert section.dirname == "04_functions"

    def test_course_total_lessons(self):
        course = Course(
            title="Python Bootcamp",
            url="https://udemy.com/course/python",
            sections=[
                Section(index=1, title="Intro", lessons=[
                    Lesson(index=1, title="Welcome"),
                ]),
                Section(index=2, title="Basics", lessons=[
                    Lesson(index=2, title="Variables"),
                    Lesson(index=3, title="Types"),
                ]),
            ],
        )
        assert course.total_lessons == 3
        assert course.slug == "python_bootcamp"

    def test_course_slug_prefers_url_slug(self):
        course = Course(
            title="Python Bootcamp",
            url="https://udemy.com/course/python-mastery-2024",
            url_slug="python-mastery-2024",
            sections=[],
        )
        assert course.slug == "python_mastery_2024"
