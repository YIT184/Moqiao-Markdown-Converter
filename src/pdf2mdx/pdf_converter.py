"""PDF → Markdown converter using PyMuPDF."""

from __future__ import annotations

import os
import io
import contextlib
import math
from typing import List, Optional, Tuple, Dict, Any, Set
from dataclasses import dataclass, field
import pathlib

import fitz  # PyMuPDF

from .markdown_utils import (
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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum area (in PDF points²) for a vector drawing cluster to be
# considered meaningful — anything smaller is treated as decoration / noise.
MIN_VECTOR_AREA_PT2 = 40 * 40  # roughly a 40pt square (≈ 0.55 inch²)

# Maximum overlap ratio allowed before two vector regions are merged.
VECTOR_OVERLAP_RATIO = 0.7

# Minimum image dimension in pixels to keep (discard tiny inline icons).
MIN_IMAGE_DIM_PX = 16

# Page-region rendering preserves PDF masks, background compositing, rotation,
# and overlaid labels. 300 DPI keeps technical line art readable without the
# much larger files produced by 600 DPI.
REGION_RENDER_DPI = 300
MAX_REGION_DIM_PX = 4096
MAX_REGION_PIXELS = 16_000_000
VECTOR_RENDER_PADDING_PT = 1.5

# Very low effective-DPI images benefit from being re-rendered in their page
# context. Monochrome images are allowed a higher threshold because they are
# commonly diagrams, equations, or scanned line art.
LOW_IMAGE_DPI = 160
LOW_LINE_ART_DPI = 260

# A vector page crop and an embedded image are treated as the same visual when
# one substantially contains the other, their areas are comparable, and their
# centers remain close. This avoids emitting the same figure twice while
# preserving small vector annotations placed over a much larger photograph.
IMAGE_VECTOR_CONTAINMENT_RATIO = 0.82
IMAGE_VECTOR_MAX_AREA_RATIO = 2.25
IMAGE_VECTOR_MAX_CENTER_OFFSET = 0.20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TableRegion:
    """A detected table bounding box on a page."""
    bbox: Tuple[float, float, float, float]  # x0, top, x1, bottom
    rows: List[List[str]] = field(default_factory=list)


@dataclass
class ImageRegion:
    """An extracted raster image on a page."""
    bbox: Tuple[float, float, float, float]
    image_bytes: bytes
    ext: str  # e.g. ".png", ".jpg"
    width: int
    height: int


@dataclass
class VectorRegion:
    """A rendered vector/drawing cluster."""
    bbox: Tuple[float, float, float, float]
    image_bytes: bytes  # PNG rendering


@dataclass
class TextBlock:
    """A text block from PyMuPDF."""
    bbox: Tuple[float, float, float, float]
    text: str
    block_type: int  # 0=text, 1=image (but we handle images separately)


@dataclass
class PageElement:
    """Unified element for reading-order sorting."""
    y_sort: float
    x_sort: float
    kind: str  # "text", "table", "image", "vector"
    data: Any


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

class PdfConversionError(Exception):
    """Raised when PDF conversion fails for a recoverable reason."""


class EncryptedPdfError(PdfConversionError):
    """Raised when the PDF is encrypted and no (or wrong) password is given."""


class PdfConverter:
    """Convert a single PDF file to Markdown with optional assets."""

    def __init__(
        self,
        *,
        preserve_images: bool = True,
        detect_tables: bool = True,
        preserve_vectors: bool = True,
        enhance_line_art: bool = True,
        password: Optional[str] = None,
    ):
        self.preserve_images = preserve_images
        self.detect_tables = detect_tables
        self.preserve_vectors = preserve_vectors
        self.enhance_line_art = enhance_line_art
        self.password = password
        self._image_counter = 0
        self._vector_counter = 0
        self._image_hashes: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convert(self, pdf_path: str, output_dir: str) -> str:
        """Convert *pdf_path* to Markdown, writing assets into *output_dir*.

        Returns the full Markdown string.
        """
        pdf_path = str(pdf_path)
        stem = pathlib.Path(pdf_path).stem
        asset_dir = os.path.join(output_dir, f"{safe_stem(stem)}_assets")
        os.makedirs(asset_dir, exist_ok=True)

        self._image_counter = 0
        self._vector_counter = 0
        self._image_hashes.clear()

        doc = self._open_document(pdf_path)
        try:
            parts: List[str] = []
            parts.append(f"# {safe_stem(stem)}\n")

            for page_idx in range(doc.page_count):
                page = doc.load_page(page_idx)
                page_md = self._convert_page(page, asset_dir, stem, page_idx)
                parts.append(page_md)
        finally:
            doc.close()

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal: document open
    # ------------------------------------------------------------------

    def _open_document(self, pdf_path: str) -> fitz.Document:
        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            raise PdfConversionError(f"无法打开 PDF 文件: {exc}") from exc

        if doc.needs_pass:
            if self.password is not None:
                ok = doc.authenticate(self.password)
                if not ok:
                    doc.close()
                    raise EncryptedPdfError("PDF 密码错误，无法解密文件。")
            else:
                doc.close()
                raise EncryptedPdfError(
                    "PDF 文件已加密，请提供密码。可通过 CLI 的 --password 参数传入。"
                )
        return doc

    # ------------------------------------------------------------------
    # Internal: single page
    # ------------------------------------------------------------------

    def _convert_page(
        self, page: fitz.Page, asset_dir: str, stem: str, page_idx: int
    ) -> str:
        page_num = page_idx + 1
        page_rect = page.rect
        pw, ph = page_rect.width, page_rect.height

        elements: List[PageElement] = []

        # 1. Tables — run first so we can exclude overlapping text
        tables: List[TableRegion] = []
        if self.detect_tables:
            tables = self._detect_tables(page, page_num, pw, ph)

        # 2. Images
        images: List[ImageRegion] = []
        if self.preserve_images:
            images = self._extract_images(page, page_num)

        # 3. Vector/drawing clusters
        vectors: List[VectorRegion] = []
        if self.preserve_vectors:
            vectors = self._extract_vectors(page, page_num)
            vectors = [
                vector
                for vector in vectors
                if not any(
                    box_area(*vector.bbox) > 0
                    and intersection_area(*vector.bbox, *table.bbox)
                    / box_area(*vector.bbox) > 0.65
                    for table in tables
                )
            ]
            images, vectors = self._reconcile_images_and_vectors(
                page,
                images,
                vectors,
            )

        # 4. Text blocks (excluding those inside table regions)
        text_blocks = self._extract_text_blocks(page, tables)

        # 5. Build unified element list in reading order
        for tb in text_blocks:
            x0, y0, x1, y1 = tb.bbox
            elements.append(PageElement(y0, x0, "text", tb))

        for t in tables:
            x0, y0, x1, y1 = t.bbox
            elements.append(PageElement(y0, x0, "table", t))

        for img in images:
            x0, y0, x1, y1 = img.bbox
            elements.append(PageElement(y0, x0, "image", img))

        for vec in vectors:
            x0, y0, x1, y1 = vec.bbox
            elements.append(PageElement(y0, x0, "vector", vec))

        elements.sort(key=lambda e: (e.y_sort, e.x_sort))

        # 6. Render elements to Markdown
        lines: List[str] = []
        lines.append(f"<!-- 第 {page_num} 页 -->")
        for elem in elements:
            rendered = self._render_element(elem, asset_dir, stem)
            if rendered:
                lines.append(rendered)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Table detection
    # ------------------------------------------------------------------

    def _detect_tables(
        self, page: fitz.Page, page_num: int, pw: float, ph: float
    ) -> List[TableRegion]:
        """Extract tables using PyMuPDF's native table finder."""
        if not hasattr(page, "find_tables"):
            return []

        tables: List[TableRegion] = []
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                finder = page.find_tables()
            for tbl in finder.tables:
                rows = tbl.extract() or []
                rows = [
                    [str(c or "") for c in row]
                    for row in rows
                    if any(str(c or "").strip() for c in row)
                ]
                if not rows:
                    continue
                bbox = tuple(tbl.bbox)
                tables.append(TableRegion(bbox=bbox, rows=rows))  # type: ignore[arg-type]
        except Exception:
            # Complex pages can defeat table inference; keep converting text.
            return []

        return tables

    # ------------------------------------------------------------------
    # Image extraction (PyMuPDF)
    # ------------------------------------------------------------------

    def _extract_images(
        self, page: fitz.Page, page_num: int
    ) -> List[ImageRegion]:
        """Extract embedded raster images from the page."""
        images: List[ImageRegion] = []
        try:
            image_list = page.get_images(full=True)
        except Exception:
            return images

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
            except Exception:
                continue

            image_bytes = base_image.get("image")
            if not image_bytes:
                continue

            ext = base_image.get("ext", "png")
            w = base_image.get("width", 0)
            h = base_image.get("height", 0)

            # Skip tiny images (inline icons / decorations)
            if w < MIN_IMAGE_DIM_PX and h < MIN_IMAGE_DIM_PX:
                continue

            # Find where this image is placed on the page
            bboxes = self._image_bboxes_on_page(page, img_info)
            if not bboxes:
                continue

            ch = content_hash(image_bytes)
            if ch in self._image_hashes:
                continue
            self._image_hashes.add(ch)

            for bbox in bboxes:
                rendered = None
                if self._should_render_image_placement(img_info, w, h, bbox):
                    rendered = self._render_page_region(page, bbox)

                if rendered is not None:
                    rendered_bytes, rendered_width, rendered_height = rendered
                    rendered_bytes = self._enhance_line_art_png(rendered_bytes)
                    images.append(ImageRegion(
                        bbox=bbox,
                        image_bytes=rendered_bytes,
                        ext=".png",
                        width=rendered_width,
                        height=rendered_height,
                    ))
                    continue

                images.append(ImageRegion(
                    bbox=bbox,
                    image_bytes=image_bytes,
                    ext=f".{ext}" if not ext.startswith(".") else ext,
                    width=w,
                    height=h,
                ))

        return images

    def _image_bboxes_on_page(
        self, page: fitz.Page, img_info: tuple
    ) -> List[Tuple[float, float, float, float]]:
        """Return bounding boxes on the page where *img_info* is displayed."""
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
            return [
                (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                for rect in rects
                if not rect.is_empty and not rect.is_infinite
            ]
        except Exception:
            return []

    @staticmethod
    def _should_render_image_placement(
        img_info: tuple,
        width: int,
        height: int,
        bbox: Tuple[float, float, float, float],
    ) -> bool:
        """Return whether a placed image should be composited from the page.

        Raw PDF image streams can omit a separate soft mask and can be much
        smaller than their displayed size. Re-rendering only those cases
        restores the visual result while leaving normal photographs untouched.
        """
        x0, y0, x1, y1 = bbox
        placed_width = max(0.0, x1 - x0)
        placed_height = max(0.0, y1 - y0)
        if placed_width <= 0 or placed_height <= 0:
            return False

        smask_xref = int(img_info[1] or 0) if len(img_info) > 1 else 0
        bits_per_component = int(img_info[4] or 8) if len(img_info) > 4 else 8
        colorspace_name = str(img_info[5] or "").lower() if len(img_info) > 5 else ""

        if smask_xref > 0:
            return True

        effective_dpi = min(
            width * 72.0 / placed_width,
            height * 72.0 / placed_height,
        )
        is_probable_line_art = (
            bits_per_component <= 2 or "gray" in colorspace_name
        )
        return (
            effective_dpi < LOW_IMAGE_DPI
            or (is_probable_line_art and effective_dpi < LOW_LINE_ART_DPI)
        )

    # ------------------------------------------------------------------
    # Vector figure extraction
    # ------------------------------------------------------------------

    def _extract_vectors(
        self, page: fitz.Page, page_num: int
    ) -> List[VectorRegion]:
        """Detect meaningful vector/drawing clusters and render them to PNG.

        Feature-detects PyMuPDF's ``get_drawings()`` API (≥1.18.0).
        Falls back gracefully on older versions.
        """
        if not hasattr(page, "get_drawings"):
            return []

        try:
            drawings = page.get_drawings()
        except Exception:
            return []

        if not drawings:
            return []

        # Prefer PyMuPDF's native proximity clustering when available.
        native_clusters: List[Tuple[float, float, float, float]] = []
        if hasattr(page, "cluster_drawings"):
            try:
                for rect in page.cluster_drawings(drawings=drawings):
                    native_clusters.append(
                        (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                    )
            except Exception:
                native_clusters = []

        # Collect all drawing paths with their bounding boxes for fallback.
        path_rects: List[Tuple[float, float, float, float]] = []
        for d in drawings:
            r = d.get("rect")
            if r is None:
                continue
            # rect is a fitz.Rect or tuple-like
            x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
            # Filter out zero-area items
            if x1 - x0 <= 0.5 or y1 - y0 <= 0.5:
                continue
            path_rects.append((x0, y0, x1, y1))

        if not path_rects:
            return []

        clusters = (
            [[bbox] for bbox in native_clusters]
            if native_clusters
            else self._cluster_rects(path_rects)
        )

        regions: List[VectorRegion] = []
        for cluster in clusters:
            bbox = self._union_bbox(cluster)
            area = box_area(*bbox)

            # Skip tiny clusters (decorations, small icons, etc.)
            if area < MIN_VECTOR_AREA_PT2:
                continue

            # Render the cluster region to PNG
            png_bytes = self._render_region_to_png(page, bbox)
            if png_bytes is None:
                continue
            png_bytes = self._enhance_line_art_png(png_bytes)

            regions.append(VectorRegion(bbox=bbox, image_bytes=png_bytes))

        # Deduplicate strongly overlapping regions
        regions = self._deduplicate_vectors(regions)
        return regions

    def _cluster_rects(
        self, rects: List[Tuple[float, float, float, float]]
    ) -> List[List[Tuple[float, float, float, float]]]:
        """Greedy union-find cluster of overlapping rectangles."""
        n = len(rects)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(n):
            for j in range(i + 1, n):
                if rects_overlap(*rects[i], *rects[j]):
                    union(i, j)

        groups: Dict[int, List[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        return [[rects[i] for i in indices] for indices in groups.values()]

    @staticmethod
    def _union_bbox(
        rects: List[Tuple[float, float, float, float]],
    ) -> Tuple[float, float, float, float]:
        """Return the bounding box containing all *rects*."""
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[2] for r in rects)
        y1 = max(r[3] for r in rects)
        return x0, y0, x1, y1

    def _render_region_to_png(
        self, page: fitz.Page, bbox: Tuple[float, float, float, float]
    ) -> Optional[bytes]:
        """Render a vector region to a high-resolution PNG."""
        rendered = self._render_page_region(
            page,
            bbox,
            padding=VECTOR_RENDER_PADDING_PT,
        )
        return rendered[0] if rendered is not None else None

    def _render_page_region(
        self,
        page: fitz.Page,
        bbox: Tuple[float, float, float, float],
        *,
        padding: float = 0.0,
    ) -> Optional[Tuple[bytes, int, int]]:
        """Render a page region at an adaptive high resolution.

        The pixel cap prevents a malformed or full-page drawing cluster from
        consuming excessive memory. ``alpha=False`` composites transparency
        and PDF soft masks onto the page's white background.
        """
        try:
            clip = fitz.Rect(*bbox)
            if padding:
                clip = fitz.Rect(
                    clip.x0 - padding,
                    clip.y0 - padding,
                    clip.x1 + padding,
                    clip.y1 + padding,
                )
            clip &= page.rect
            if clip.is_empty or clip.is_infinite:
                return None

            scale = REGION_RENDER_DPI / 72.0
            target_width = max(1, math.ceil(clip.width * scale))
            target_height = max(1, math.ceil(clip.height * scale))
            if max(target_width, target_height) > MAX_REGION_DIM_PX:
                scale *= MAX_REGION_DIM_PX / max(target_width, target_height)
                target_width = max(1, math.ceil(clip.width * scale))
                target_height = max(1, math.ceil(clip.height * scale))
            if target_width * target_height > MAX_REGION_PIXELS:
                scale *= math.sqrt(
                    MAX_REGION_PIXELS / (target_width * target_height)
                )

            pix = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                clip=clip,
                colorspace=fitz.csRGB,
                alpha=False,
            )
            return pix.tobytes("png"), pix.width, pix.height
        except Exception:
            return None

    def _enhance_line_art_png(self, png_bytes: bytes) -> bytes:
        """Improve contrast and edge clarity for monochrome technical figures.

        Dark-background line drawings are inverted to conventional black on
        white only when the thumbnail is overwhelmingly monochrome and mostly
        dark. Photos and colorful diagrams pass through unchanged.
        """
        if not self.enhance_line_art:
            return png_bytes

        try:
            from PIL import Image, ImageFilter, ImageOps

            with Image.open(io.BytesIO(png_bytes)) as source:
                image = source.convert("RGB")

            sample = image.copy()
            sample.thumbnail((256, 256))
            pixel_bytes = sample.tobytes()
            if not pixel_bytes:
                return png_bytes

            monochrome = 0
            luminances: List[int] = []
            for index in range(0, len(pixel_bytes), 3):
                red, green, blue = pixel_bytes[index:index + 3]
                if max(red, green, blue) - min(red, green, blue) <= 12:
                    monochrome += 1
                luminances.append(
                    round(0.2126 * red + 0.7152 * green + 0.0722 * blue)
                )

            count = len(luminances)
            monochrome_ratio = monochrome / count
            dark_ratio = sum(value < 48 for value in luminances) / count
            light_ratio = sum(value > 207 for value in luminances) / count
            if monochrome_ratio < 0.94 or max(dark_ratio, light_ratio) < 0.55:
                return png_bytes

            if dark_ratio > 0.60 and light_ratio > 0.015:
                image = ImageOps.invert(image)

            image = ImageOps.autocontrast(image, cutoff=0.2)
            image = image.filter(
                ImageFilter.UnsharpMask(radius=1.0, percent=135, threshold=3)
            )
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True, compress_level=7)
            return output.getvalue()
        except Exception:
            return png_bytes

    def _deduplicate_vectors(
        self, regions: List[VectorRegion]
    ) -> List[VectorRegion]:
        """Remove vector regions that overlap heavily with a larger one."""
        if len(regions) <= 1:
            return regions

        # Sort by area descending
        regions = sorted(regions, key=lambda r: box_area(*r.bbox), reverse=True)
        keep: List[VectorRegion] = []
        for r in regions:
            is_dup = False
            for k in keep:
                inter = intersection_area(*r.bbox, *k.bbox)
                r_area = box_area(*r.bbox)
                if r_area > 0 and inter / r_area > VECTOR_OVERLAP_RATIO:
                    is_dup = True
                    break
            if not is_dup:
                keep.append(r)
        return keep

    def _reconcile_images_and_vectors(
        self,
        page: fitz.Page,
        images: List[ImageRegion],
        vectors: List[VectorRegion],
    ) -> Tuple[List[ImageRegion], List[VectorRegion]]:
        """Remove vector crops that duplicate an embedded image placement.

        Before discarding the vector crop, the image region is rendered from
        the page so vector overlays, masks, and borders inside the image bounds
        remain visible in the retained asset.
        """
        if not images or not vectors:
            return images, vectors

        kept_vectors: List[VectorRegion] = []
        recomposited_images: Set[int] = set()

        for vector in vectors:
            matching_index = next(
                (
                    index
                    for index, image in enumerate(images)
                    if self._same_visual_region(image.bbox, vector.bbox)
                ),
                None,
            )
            if matching_index is None:
                kept_vectors.append(vector)
                continue

            if matching_index not in recomposited_images:
                image = images[matching_index]
                rendered = self._render_page_region(page, image.bbox)
                if rendered is not None:
                    image_bytes, width, height = rendered
                    image.image_bytes = self._enhance_line_art_png(image_bytes)
                    image.ext = ".png"
                    image.width = width
                    image.height = height
                recomposited_images.add(matching_index)

        return images, kept_vectors

    @staticmethod
    def _same_visual_region(
        first: Tuple[float, float, float, float],
        second: Tuple[float, float, float, float],
    ) -> bool:
        first_area = box_area(*first)
        second_area = box_area(*second)
        if first_area <= 0 or second_area <= 0:
            return False

        overlap = intersection_area(*first, *second)
        smaller = min(first_area, second_area)
        larger = max(first_area, second_area)
        if overlap / smaller < IMAGE_VECTOR_CONTAINMENT_RATIO:
            return False
        if larger / smaller > IMAGE_VECTOR_MAX_AREA_RATIO:
            return False

        first_center = box_center(*first)
        second_center = box_center(*second)
        max_width = max(first[2] - first[0], second[2] - second[0])
        max_height = max(first[3] - first[1], second[3] - second[1])
        if max_width <= 0 or max_height <= 0:
            return False
        return (
            abs(first_center[0] - second_center[0]) / max_width
            <= IMAGE_VECTOR_MAX_CENTER_OFFSET
            and abs(first_center[1] - second_center[1]) / max_height
            <= IMAGE_VECTOR_MAX_CENTER_OFFSET
        )

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text_blocks(
        self, page: fitz.Page, tables: List[TableRegion]
    ) -> List[TextBlock]:
        """Extract text line-by-line, excluding lines inside detected tables."""
        try:
            page_dict = page.get_text("dict")
        except Exception:
            return []

        text_blocks: List[TextBlock] = []
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(str(span.get("text", "")) for span in spans).strip()
                if not text:
                    continue
                raw_bbox = line.get("bbox") or block.get("bbox")
                if not raw_bbox:
                    continue
                bbox = tuple(float(value) for value in raw_bbox[:4])
                cx, cy = box_center(*bbox)
                if any(
                    table.bbox[0] <= cx <= table.bbox[2]
                    and table.bbox[1] <= cy <= table.bbox[3]
                    for table in tables
                ):
                    continue
                text_blocks.append(TextBlock(
                    bbox=bbox, text=text, block_type=0,
                ))

        return text_blocks

    # ------------------------------------------------------------------
    # Rendering elements to Markdown
    # ------------------------------------------------------------------

    def _render_element(
        self, elem: PageElement, asset_dir: str, stem: str
    ) -> str:
        if elem.kind == "text":
            blk: TextBlock = elem.data
            return blk.text + "\n"

        elif elem.kind == "table":
            tbl: TableRegion = elem.data
            return normalize_table(tbl.rows)

        elif elem.kind == "image":
            img: ImageRegion = elem.data
            self._image_counter += 1
            fname = asset_filename(stem, "img", img.ext, self._image_counter)
            fname = collision_free_name(asset_dir, fname)
            fpath = os.path.join(asset_dir, fname)
            with open(fpath, "wb") as fh:
                fh.write(img.image_bytes)
            rel = os.path.join(os.path.basename(asset_dir), fname).replace("\\", "/")
            return f"![图片 {self._image_counter}]({rel})\n"

        elif elem.kind == "vector":
            vec: VectorRegion = elem.data
            self._vector_counter += 1
            fname = asset_filename(stem, "vec", ".png", self._vector_counter)
            fname = collision_free_name(asset_dir, fname)
            fpath = os.path.join(asset_dir, fname)
            with open(fpath, "wb") as fh:
                fh.write(vec.image_bytes)
            rel = os.path.join(os.path.basename(asset_dir), fname).replace("\\", "/")
            return f"![图形 {self._vector_counter}]({rel})\n"

        return ""
