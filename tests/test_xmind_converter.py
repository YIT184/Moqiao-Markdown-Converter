"""Unit tests for XMind converter using in-memory ZIP fixtures."""

import json
import os
import zipfile
import tempfile
import pathlib
import io
from xml.etree import ElementTree as ET

import pytest

from pdf2mdx.xmind_converter import XmindConverter, XmindConversionError


# ---------------------------------------------------------------------------
# Helpers: build in-memory XMind ZIPs
# ---------------------------------------------------------------------------

def _make_json_xmind(sheets: list) -> bytes:
    """Create an in-memory XMind ZIP with content.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(sheets, ensure_ascii=False))
    return buf.getvalue()


def _make_xml_xmind(xml_str: str) -> bytes:
    """Create an in-memory XMind ZIP with content.xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", xml_str)
    return buf.getvalue()


def _build_legacy_xml(sheets: list) -> str:
    """Build a minimal legacy content.xml string from a list of sheet dicts."""
    root = ET.Element("xmap-content")

    for s in sheets:
        sheet_el = ET.SubElement(root, "sheet", {"title": s.get("title", "")})
        root_topic = s.get("rootTopic")
        if root_topic:
            _add_xml_topic(sheet_el, root_topic)

    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def _add_xml_topic(
    parent: ET.Element, topic: dict, ns: str = ""
) -> ET.Element:
    """Recursively add a topic element."""
    el = ET.SubElement(parent, "topic", {"title": topic.get("title", "")})

    # Notes
    notes = topic.get("notes")
    if notes:
        notes_el = ET.SubElement(el, "notes")
        plain = notes.get("plain", {})
        ET.SubElement(notes_el, "plain").text = plain.get("content", "")

    # Labels
    labels = topic.get("labels", [])
    if labels:
        labels_el = ET.SubElement(el, "labels")
        for lbl in labels:
            ET.SubElement(labels_el, "label").text = lbl

    # Children
    children = topic.get("children", {}).get("attached", [])
    if children:
        children_el = ET.SubElement(el, "children")
        topics_el = ET.SubElement(children_el, "topics")
        for child in children:
            _add_xml_topic(topics_el, child)

    return el


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestModernJsonXmind:
    """Tests for content.json (modern) format."""

    def test_single_sheet_single_topic(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "children": {"attached": []},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "Sheet1" in md
            assert "Root" in md

    def test_nested_topics(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "children": {
                    "attached": [
                        {
                            "title": "Child1",
                            "children": {
                                "attached": [
                                    {"title": "Grandchild", "children": {"attached": []}},
                                ]
                            },
                        },
                        {"title": "Child2", "children": {"attached": []}},
                    ]
                },
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "Child1" in md
            assert "Grandchild" in md
            assert "Child2" in md

    def test_topic_with_notes(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "notes": {"plain": {"content": "Some note text"}},
                "children": {"attached": []},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "Some note text" in md

    def test_topic_with_labels(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "labels": ["重要", "待办"],
                "children": {"attached": []},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "重要" in md
            assert "待办" in md

    def test_topic_with_markers(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "markers": [{"markerId": "priority-1"}, {"markerId": "task-start"}],
                "children": {"attached": []},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "priority-1" in md
            assert "task-start" in md

    def test_topic_with_hyperlink(self):
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "href": "https://example.com",
                "children": {"attached": []},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "https://example.com" in md

    def test_deeply_nested_too_many_headings(self):
        """Verify that >6 levels fall back to bullet lists."""
        # Build a deep chain: Root → child1 → child2 → ... → child7
        current = {"title": "Deep7", "children": {"attached": []}}
        for i in range(6, 0, -1):
            current = {"title": f"Level{i}", "children": {"attached": [current]}}
        data = [{
            "title": "Sheet1",
            "rootTopic": {
                "title": "Root",
                "children": {"attached": [current]},
            },
            "relationships": [],
        }]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            # Level 6 = ######, Level 7 = should use bullet list, not #######
            assert "Deep7" in md
            # Should NOT have "#######" (7 hashes)
            assert "#######" not in md


class TestLegacyXmlXmind:
    """Tests for content.xml (legacy) format."""

    def test_single_sheet_single_topic(self):
        sheets = [{
            "title": "LegacySheet",
            "rootTopic": {"title": "LegacyRoot", "children": {"attached": []}},
        }]
        xml_str = _build_legacy_xml(sheets)
        zip_bytes = _make_xml_xmind(xml_str)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "LegacySheet" in md
            assert "LegacyRoot" in md

    def test_nested_topics(self):
        sheets = [{
            "title": "LegacySheet",
            "rootTopic": {
                "title": "Root",
                "children": {
                    "attached": [
                        {
                            "title": "ChildA",
                            "children": {
                                "attached": [
                                    {"title": "GrandChild", "children": {"attached": []}},
                                ]
                            },
                        },
                    ]
                },
            },
        }]
        xml_str = _build_legacy_xml(sheets)
        zip_bytes = _make_xml_xmind(xml_str)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "ChildA" in md
            assert "GrandChild" in md

    def test_topic_with_notes(self):
        sheets = [{
            "title": "Sheet",
            "rootTopic": {
                "title": "Root",
                "notes": {"plain": {"content": "My note"}},
                "children": {"attached": []},
            },
        }]
        xml_str = _build_legacy_xml(sheets)
        zip_bytes = _make_xml_xmind(xml_str)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "My note" in md

    def test_topic_with_labels(self):
        sheets = [{
            "title": "Sheet",
            "rootTopic": {
                "title": "Root",
                "labels": ["标签1", "标签2"],
                "children": {"attached": []},
            },
        }]
        xml_str = _build_legacy_xml(sheets)
        zip_bytes = _make_xml_xmind(xml_str)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "标签1" in md
            assert "标签2" in md


class TestEdgeCases:
    def test_not_a_zip_file(self):
        with tempfile.TemporaryDirectory() as td:
            bad_path = os.path.join(td, "bad.xmind")
            with open(bad_path, "w") as f:
                f.write("not a zip file")

            conv = XmindConverter()
            with pytest.raises(XmindConversionError):
                conv.convert(bad_path, td)

    def test_no_content_file(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.xml", "<manifest></manifest>")

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "empty.xmind")
            with open(path, "wb") as f:
                f.write(buf.getvalue())

            conv = XmindConverter()
            with pytest.raises(XmindConversionError):
                conv.convert(path, td)

    def test_empty_sheet_list(self):
        zip_bytes = _make_json_xmind([])

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)
            # Should produce header but no sheet content
            assert "#" in md  # at least the title line

    def test_multiple_sheets(self):
        data = [
            {"title": "Sheet1", "rootTopic": {"title": "A", "children": {"attached": []}}, "relationships": []},
            {"title": "Sheet2", "rootTopic": {"title": "B", "children": {"attached": []}}, "relationships": []},
        ]
        zip_bytes = _make_json_xmind(data)

        with tempfile.TemporaryDirectory() as td:
            xmind_path = os.path.join(td, "test.xmind")
            with open(xmind_path, "wb") as f:
                f.write(zip_bytes)

            conv = XmindConverter()
            md = conv.convert(xmind_path, td)

            assert "Sheet1" in md
            assert "Sheet2" in md
            assert "A" in md
            assert "B" in md
