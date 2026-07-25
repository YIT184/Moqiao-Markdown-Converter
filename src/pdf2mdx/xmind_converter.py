"""XMind → Markdown converter.

Treats .xmind as a ZIP archive and supports both the modern ``content.json``
and legacy ``content.xml`` formats.
"""

from __future__ import annotations

import json
import os
import zipfile
import pathlib
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from .markdown_utils import safe_stem, collision_free_name

# Maximum Markdown heading level (###### = level 6)
MAX_HEADING_LEVEL = 6


class XmindConversionError(Exception):
    """Raised when XMind conversion fails."""


class XmindConverter:
    """Convert a single XMind file to Markdown with optional assets."""

    def convert(self, xmind_path: str, output_dir: str) -> str:
        """Convert *xmind_path* to Markdown, writing assets into *output_dir*.

        Returns the full Markdown string.
        """
        xmind_path = str(xmind_path)
        stem = pathlib.Path(xmind_path).stem
        asset_dir = os.path.join(output_dir, f"{safe_stem(stem)}_assets")
        os.makedirs(asset_dir, exist_ok=True)

        content = self._read_content(xmind_path)
        if content is None:
            raise XmindConversionError("XMind 文件中未找到 content.json 或 content.xml。")

        # Determine format and parse
        if isinstance(content, str):
            # Legacy XML format
            sheets = self._parse_content_xml(content)
        elif isinstance(content, list):
            # Modern JSON format (array of sheets)
            sheets = self._parse_content_json(content)
        elif isinstance(content, dict):
            # Modern JSON format (dict wrapper with possible "sheets" key)
            if "sheets" in content:
                sheets = self._parse_content_json(content["sheets"])
            else:
                sheets = self._parse_content_json([content])
        else:
            raise XmindConversionError("XMind 内容格式无法识别。")

        # Extract assets first so topic links can point at collision-safe names.
        self._asset_dir_name = os.path.basename(asset_dir)
        self._asset_map = self._extract_assets(xmind_path, asset_dir)

        # Convert to Markdown
        parts: List[str] = []
        parts.append(f"# {safe_stem(stem)}\n")

        for sheet in sheets:
            parts.append(self._render_sheet(sheet))

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Content loading
    # ------------------------------------------------------------------

    def _read_content(self, xmind_path: str) -> Optional[Any]:
        """Read the content file (JSON or XML) from the XMind ZIP."""
        try:
            with zipfile.ZipFile(xmind_path, "r") as zf:
                names = zf.namelist()
                # Find content files (may be at root or in a subdirectory)
                json_names = [n for n in names if n.endswith("content.json")]
                xml_names = [n for n in names if n.endswith("content.xml")]
                # Prefer content.json (modern format)
                if json_names:
                    with zf.open(json_names[0]) as fh:
                        return json.load(fh)
                elif xml_names:
                    with zf.open(xml_names[0]) as fh:
                        return fh.read().decode("utf-8")
                else:
                    return None
        except zipfile.BadZipFile as exc:
            raise XmindConversionError(f"无法打开 XMind 文件（非有效的 ZIP）: {exc}") from exc
        except Exception as exc:
            raise XmindConversionError(f"读取 XMind 文件失败: {exc}") from exc

    # ------------------------------------------------------------------
    # Modern content.json parser
    # ------------------------------------------------------------------

    def _parse_content_json(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse all sheets from a modern content.json structure.

        content.json is an **array** of sheet objects.
        """
        sheets: List[Dict[str, Any]] = []
        for sheet in data:
            sheet_data: Dict[str, Any] = {
                "title": sheet.get("title", ""),
                "rootTopic": sheet.get("rootTopic", {}),
                "relationships": sheet.get("relationships", []),
            }
            sheets.append(sheet_data)
        return sheets

    # ------------------------------------------------------------------
    # Legacy content.xml parser
    # ------------------------------------------------------------------

    def _parse_content_xml(self, xml_str: str) -> List[Dict[str, Any]]:
        """Parse sheets from legacy content.xml format."""
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as exc:
            raise XmindConversionError(f"XMind content.xml 解析失败: {exc}") from exc

        sheets: List[Dict[str, Any]] = []
        # Namespace may or may not be present
        ns = self._detect_namespace(root.tag)

        for sheet_elem in root.findall(f"{ns}sheet"):
            title_node = sheet_elem.find(f"{ns}title")
            sheet_title = sheet_elem.get("title", "") or (
                title_node.text if title_node is not None and title_node.text else ""
            )
            root_topic = self._parse_xml_topic(
                sheet_elem.find(f"{ns}topic"), ns
            )
            relationships = self._parse_xml_relationships(sheet_elem, ns)
            sheets.append({
                "title": sheet_title,
                "rootTopic": root_topic,
                "relationships": relationships,
            })

        return sheets

    def _detect_namespace(self, tag: str) -> str:
        """Return a namespace prefix string like ``{urn:...}`` or empty string."""
        if tag.startswith("{"):
            end = tag.index("}")
            return tag[:end + 1]
        return ""

    def _parse_xml_topic(
        self, elem: Optional[ET.Element], ns: str
    ) -> Dict[str, Any]:
        """Recursively parse a topic element from legacy XML."""
        if elem is None:
            return {"title": "", "children": {"attached": []}}

        topic: Dict[str, Any] = {
            "title": elem.get("title", ""),
            "href": elem.get("href") or elem.get(f"{ns}href"),
        }
        title_node = elem.find(f"{ns}title")
        if not topic["title"] and title_node is not None and title_node.text:
            topic["title"] = title_node.text

        # Notes
        notes_elem = elem.find(f"{ns}notes")
        if notes_elem is not None:
            plain = notes_elem.find(f"{ns}plain")
            if plain is not None:
                topic["notes"] = {"plain": {"content": plain.text or ""}}

        # Labels
        labels_elem = elem.find(f"{ns}labels")
        if labels_elem is not None:
            label_list: List[str] = []
            for lbl in labels_elem.findall(f"{ns}label"):
                text = lbl.text or ""
                if text:
                    label_list.append(text)
            if label_list:
                topic["labels"] = label_list

        # Markers
        markers_elem = elem.find(f"{ns}markers")
        if markers_elem is not None:
            marker_list: List[Dict[str, str]] = []
            for mk in markers_elem:
                tag = self._strip_ns(mk.tag)
                marker_list.append({"markerId": tag})
            if marker_list:
                topic["markers"] = marker_list

        # Children
        children = {"attached": []}
        children_elem = elem.find(f"{ns}children")
        if children_elem is not None:
            child_items: List[Dict[str, Any]] = []
            for child_group in children_elem.findall(f"{ns}topics"):
                if child_group.get("type", "attached") not in ("attached", "detached"):
                    continue
                for child_topic in child_group.findall(f"{ns}topic"):
                    child_items.append(self._parse_xml_topic(child_topic, ns))
            children["attached"] = child_items
        topic["children"] = children

        # Image / attachment
        for child in elem:
            tag_name = self._strip_ns(child.tag)
            if tag_name in ("xhtml:img", "img"):
                src = child.get("src") or child.get(f"{ns}src", "")
                topic["image"] = {"src": src}

        return topic

    def _parse_xml_relationships(
        self, sheet_elem: ET.Element, ns: str
    ) -> List[Dict[str, Any]]:
        """Parse relationships from the sheet element."""
        rels: List[Dict[str, Any]] = []
        for rel in sheet_elem.findall(f"{ns}relationships/{ns}relationship"):
            rels.append({
                "end1Id": rel.get("end1Id", ""),
                "end2Id": rel.get("end2Id", ""),
                "title": rel.get("title", ""),
            })
        return rels

    @staticmethod
    def _strip_ns(tag: str) -> str:
        """Remove XML namespace prefix from an element tag."""
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def _render_sheet(self, sheet: Dict[str, Any]) -> str:
        """Render a single sheet to Markdown."""
        title = sheet.get("title", "")
        root_topic = sheet.get("rootTopic", {})
        relationships = sheet.get("relationships", [])

        lines: List[str] = []

        # Sheet title as H2; fall back to root topic title if sheet has none
        if title:
            lines.append(f"## {self._escape_text(title)}\n")
            root_level = 3
        elif root_topic.get("title"):
            lines.append(f"## {self._escape_text(root_topic['title'])}\n")
            root_level = 3
        else:
            root_level = 2

        # Render root topic itself at the determined level (its notes/labels/children)
        root_md = self._render_topic(root_topic, level=root_level)
        if root_md.strip():
            lines.append(root_md)

        # Relationships
        if relationships:
            lines.append("### 关系\n")
            for rel in relationships:
                rtitle = rel.get("title", "")
                lines.append(f"- {self._escape_text(rtitle)}")

        return "\n".join(lines)

    def _render_topic(self, topic: Dict[str, Any], level: int) -> str:
        """Render a topic and its children recursively."""
        lines: List[str] = []
        title = topic.get("title", "")

        if level <= MAX_HEADING_LEVEL:
            prefix = "#" * level
            lines.append(f"{prefix} {self._escape_text(title)}\n")
        else:
            # Beyond heading depth, use bullet lists
            indent = "  " * (level - MAX_HEADING_LEVEL)
            lines.append(f"{indent}- **{self._escape_text(title)}**\n")

        # Hyperlink
        href = topic.get("href", "")
        if href:
            lines.append(f"[链接]({href})\n")

        # Notes
        notes = topic.get("notes", {})
        plain_notes = notes.get("plain", {})
        note_content = plain_notes.get("content", "")
        if note_content:
            lines.append(f"> {self._escape_text(note_content)}\n")

        # Labels
        labels = topic.get("labels", [])
        if labels:
            lines.append(f"标签: {', '.join(self._escape_text(l) for l in labels)}\n")

        # Markers
        markers = topic.get("markers", [])
        if markers:
            mk_ids = [m.get("markerId", "?") for m in markers]
            lines.append(f"标记: {', '.join(self._escape_text(m) for m in mk_ids)}\n")

        # Image
        image = topic.get("image", {})
        img_src = image.get("src", "")
        if img_src:
            normalized = str(img_src).removeprefix("xap:").lstrip("/")
            fname = self._asset_map.get(
                normalized,
                self._asset_map.get(pathlib.PurePosixPath(normalized).name, ""),
            )
            if fname:
                rel = f"{self._asset_dir_name}/{fname}".replace("\\", "/")
                lines.append(f"![{self._escape_text(fname)}]({rel})\n")

        # Children
        children = self._get_attached_children(topic)
        for child in children:
            lines.append(self._render_topic(child, level + 1))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Asset extraction
    # ------------------------------------------------------------------

    def _extract_assets(self, xmind_path: str, asset_dir: str) -> Dict[str, str]:
        """Extract image/attachment files from the XMind ZIP archives."""
        extracted: Dict[str, str] = {}
        try:
            with zipfile.ZipFile(xmind_path, "r") as zf:
                for name in zf.namelist():
                    lower = name.lower()
                    # Look for attachments and images
                    if any(
                        lower.startswith(prefix)
                        for prefix in ("attachments/", "images/", "resources/")
                    ):
                        if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp")):
                            fname = pathlib.Path(name).name
                            if fname:
                                fname = collision_free_name(asset_dir, fname)
                                fpath = os.path.join(asset_dir, fname)
                                with zf.open(name) as src, open(fpath, "wb") as dst:
                                    dst.write(src.read())
                                normalized = name.lstrip("/")
                                extracted[normalized] = fname
                                extracted[pathlib.PurePosixPath(normalized).name] = fname
        except Exception:
            # Asset extraction is best-effort
            pass
        return extracted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_attached_children(topic: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get the list of attached child topics."""
        children = topic.get("children", {})
        return children.get("attached", [])

    @staticmethod
    def _escape_text(text: str) -> str:
        """Escape text that might contain Markdown special characters."""
        if not text:
            return ""
        # Escape pipe for table safety, and backslash
        text = text.replace("\\", "\\\\")
        # Don't over-escape — just handle the main issues
        return text
