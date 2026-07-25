"""Unit tests for markdown_utils."""

import pytest
import tempfile
import os
from pdf2mdx.markdown_utils import (
    escape_cell,
    normalize_table,
    safe_stem,
    asset_filename,
    collision_free_name,
    rects_overlap,
    intersection_area,
    box_area,
    box_center,
    content_hash,
)


class TestEscapeCell:
    def test_plain_text(self):
        assert escape_cell("hello") == "hello"

    def test_pipe_escape(self):
        assert escape_cell("a|b") == r"a\|b"

    def test_newline_escape(self):
        assert escape_cell("line1\nline2") == "line1 line2"

    def test_carriage_return_escape(self):
        assert escape_cell("a\rb") == "a b"

    def test_pipe_and_newline(self):
        assert escape_cell("col1|col2\ncol3") == r"col1\|col2 col3"

    def test_non_string(self):
        assert escape_cell(123) == "123"


class TestNormalizeTable:
    def test_empty(self):
        assert normalize_table([]) == ""

    def test_simple_table(self):
        rows = [["A", "B"], ["1", "2"]]
        result = normalize_table(rows)
        assert "| A | B |" in result
        assert "| --- | --- |" in result
        assert "| 1 | 2 |" in result

    def test_uneven_rows(self):
        rows = [["A", "B", "C"], ["1"]]
        result = normalize_table(rows)
        # Should have 3 columns, second row padded
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header, separator, data
        assert lines[2] == "| 1 |  |  |"

    def test_pipe_escape_in_cell(self):
        rows = [["A|B"], ["1"]]
        result = normalize_table(rows)
        assert r"\|" in result

    def test_newline_escape_in_cell(self):
        rows = [["A\nB"], ["1"]]
        result = normalize_table(rows)
        assert "\n" not in result.split("|")[1]  # no raw newline in cell

    def test_trailing_empty_rows_stripped(self):
        rows = [["A"], ["1"], ["", ""], ["", ""]]
        result = normalize_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header, sep, data (trailing empties removed)


class TestSafeStem:
    def test_simple_name(self):
        assert safe_stem("hello") == "hello"

    def test_invalid_chars(self):
        assert safe_stem("test:file") == "test_file"

    def test_whitespace(self):
        assert safe_stem("  hello world  ") == "hello world"

    def test_long_name(self):
        long_name = "a" * 100
        result = safe_stem(long_name)
        assert len(result) <= 80

    def test_empty_gives_unnamed(self):
        assert safe_stem("<>:?") == "unnamed"


class TestAssetFilename:
    def test_basic(self):
        name = asset_filename("doc", "img", ".png", 5)
        assert name == "doc_img_005.png"

    def test_stem_sanitized(self):
        name = asset_filename("my:doc", "vec", ".png", 1)
        assert ":" not in name
        assert "my_doc" in name


class TestCollisionFreeName:
    def test_no_collision(self):
        with tempfile.TemporaryDirectory() as td:
            result = collision_free_name(td, "test.png")
            assert result == "test.png"

    def test_with_collision(self):
        with tempfile.TemporaryDirectory() as td:
            # Create the file first
            with open(os.path.join(td, "test.png"), "w") as f:
                f.write("x")
            result = collision_free_name(td, "test.png")
            assert result == "test_1.png"

    def test_multiple_collisions(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "test.png"), "w") as f:
                f.write("x")
            with open(os.path.join(td, "test_1.png"), "w") as f:
                f.write("x")
            result = collision_free_name(td, "test.png")
            assert result == "test_2.png"


class TestRectsOverlap:
    def test_overlapping(self):
        assert rects_overlap(0, 0, 10, 10, 5, 5, 15, 15) is True

    def test_non_overlapping(self):
        assert rects_overlap(0, 0, 5, 5, 10, 10, 15, 15) is False

    def test_touching_edge_no_overlap(self):
        assert rects_overlap(0, 0, 5, 5, 5, 0, 10, 5) is False

    def test_contained(self):
        assert rects_overlap(0, 0, 10, 10, 2, 2, 4, 4) is True


class TestBoxArea:
    def test_simple(self):
        assert box_area(0, 0, 10, 20) == 200.0


class TestBoxCenter:
    def test_center(self):
        cx, cy = box_center(0, 0, 10, 20)
        assert cx == 5.0
        assert cy == 10.0


class TestContentHash:
    def test_deterministic(self):
        h1 = content_hash(b"hello")
        h2 = content_hash(b"hello")
        assert h1 == h2

    def test_different(self):
        h1 = content_hash(b"hello")
        h2 = content_hash(b"world")
        assert h1 != h2
