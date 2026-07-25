"""Smoke tests for PDF converter (dependency-gated)."""

import os
import tempfile
import pytest

# These tests require PyMuPDF (fitz)
fitz = pytest.importorskip("fitz")

from pdf2mdx.pdf_converter import PdfConverter, EncryptedPdfError


def _create_minimal_pdf(path: str, text: str = "Hello World") -> None:
    """Create a minimal PDF with one page of text using PyMuPDF."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


class TestPdfConverterSmoke:
    def test_basic_conversion(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "test.pdf")
            _create_minimal_pdf(pdf_path, "Hello PDF World")

            conv = PdfConverter()
            md = conv.convert(pdf_path, td)

            assert "Hello PDF World" in md
            # Should have page separator
            assert "第 1 页" in md

    def test_output_file_written(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "test.pdf")
            _create_minimal_pdf(pdf_path, "Test content")

            conv = PdfConverter()
            md = conv.convert(pdf_path, td)

            # Write the md output
            out_path = os.path.join(td, "test.md")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(md)

            assert os.path.exists(out_path)
            with open(out_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "Test content" in content

    def test_encrypted_pdf_with_wrong_password(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "enc.pdf")
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Secret")
            doc.save(pdf_path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
            doc.close()

            conv = PdfConverter(password="wrong")
            with pytest.raises(EncryptedPdfError):
                conv.convert(pdf_path, td)

    def test_encrypted_pdf_with_correct_password(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "enc.pdf")
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Decrypted")
            doc.save(pdf_path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
            doc.close()

            conv = PdfConverter(password="secret")
            md = conv.convert(pdf_path, td)
            assert "Decrypted" in md

    def test_multi_page(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "multi.pdf")
            doc = fitz.open()
            for i in range(3):
                page = doc.new_page()
                page.insert_text((72, 72), f"Page {i + 1}")
            doc.save(pdf_path)
            doc.close()

            conv = PdfConverter()
            md = conv.convert(pdf_path, td)

            assert "Page 1" in md
            assert "Page 2" in md
            assert "Page 3" in md
            assert "第 1 页" in md
            assert "第 2 页" in md
            assert "第 3 页" in md

    def test_disabling_tables(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "test.pdf")
            _create_minimal_pdf(pdf_path, "Simple text")

            conv = PdfConverter(detect_tables=False)
            md = conv.convert(pdf_path, td)
            assert "Simple text" in md

    def test_disabling_images(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "test.pdf")
            _create_minimal_pdf(pdf_path, "Text only")

            conv = PdfConverter(preserve_images=False)
            md = conv.convert(pdf_path, td)
            assert "Text only" in md

    def test_disabling_vectors(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "test.pdf")
            _create_minimal_pdf(pdf_path, "Text only, no vectors")

            conv = PdfConverter(preserve_vectors=False)
            md = conv.convert(pdf_path, td)
            assert "Text only, no vectors" in md

    def test_low_resolution_grayscale_image_is_composited_at_high_dpi(self):
        doc = fitz.open()
        page = doc.new_page(width=240, height=140)
        source = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 60, 30), False)
        source.clear_with(255)
        for x in range(5, 55):
            source.set_pixel(x, 15, (0,))
        page.insert_image(
            fitz.Rect(20, 20, 200, 110),
            stream=source.tobytes("png"),
        )

        images = PdfConverter()._extract_images(page, 1)
        doc.close()

        assert len(images) == 1
        assert images[0].ext == ".png"
        assert images[0].width >= 700
        assert images[0].height >= 350

    def test_vector_region_is_rendered_at_300_dpi(self):
        doc = fitz.open()
        page = doc.new_page(width=220, height=120)
        page.draw_rect(fitz.Rect(20, 20, 200, 100), color=(0, 0, 0), width=1)

        png = PdfConverter()._render_region_to_png(
            page,
            (20.0, 20.0, 200.0, 100.0),
        )
        doc.close()

        assert png is not None
        rendered = fitz.Pixmap(png)
        assert rendered.width >= 750
        assert rendered.height >= 330

    def test_dark_monochrome_line_art_is_inverted_for_readability(self):
        source = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 100, 50), False)
        source.clear_with(0)
        for y in range(15, 30):
            for x in range(10, 90):
                source.set_pixel(x, y, (255,))
        original = source.tobytes("png")

        enhanced = PdfConverter()._enhance_line_art_png(original)
        rendered = fitz.Pixmap(enhanced)

        assert enhanced != original
        assert min(rendered.pixel(0, 0)[:3]) > 240
        assert max(rendered.pixel(50, 20)[:3]) < 15

    def test_line_art_enhancement_can_be_disabled(self):
        source = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 40, 20), False)
        source.clear_with(0)
        original = source.tobytes("png")

        unchanged = PdfConverter(
            enhance_line_art=False
        )._enhance_line_art_png(original)

        assert unchanged == original

    def test_overlapping_image_and_vector_crop_emit_one_figure(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "duplicate-figure.pdf")
            doc = fitz.open()
            page = doc.new_page(width=420, height=220)
            source = fitz.Pixmap(
                fitz.csGRAY,
                fitz.IRect(0, 0, 300, 100),
                False,
            )
            source.clear_with(255)
            for x in range(30, 270):
                source.set_pixel(x, 50, (0,))
            page.insert_text((30, 40), "Question header", fontsize=12)
            page.insert_image(
                fitz.Rect(30, 60, 390, 190),
                stream=source.tobytes("png"),
            )
            page.draw_rect(
                fitz.Rect(20, 20, 400, 200),
                color=(0, 0, 0),
                width=1,
            )
            doc.save(pdf_path)
            doc.close()

            markdown = PdfConverter(detect_tables=False).convert(pdf_path, td)
            asset_dir = os.path.join(td, "duplicate-figure_assets")
            asset_names = os.listdir(asset_dir)

            assert "Question header" in markdown
            assert "![图片 1]" in markdown
            assert "![图形" not in markdown
            assert len(asset_names) == 1

    def test_small_vector_annotation_is_not_treated_as_duplicate(self):
        assert not PdfConverter._same_visual_region(
            (0.0, 0.0, 200.0, 200.0),
            (10.0, 10.0, 40.0, 40.0),
        )
