"""Tests for the pdf_server tool family (ported from Archipelago's pdf_server)."""

import base64
import io

import pymupdf as fitz
import pytest
from atoll_tools.content import ImageContent
from atoll_tools.published import wrap_flat_arguments as _wrap_flat
from pdf_helper import minimal_pdf
from pdf_server._image_cache import IMAGE_CACHE
from pdf_server.create_pdf import create_pdf
from pdf_server.read_image import read_image
from pdf_server.read_page_as_image import read_page_as_image
from pdf_server.read_pdf_pages import read_pdf_pages
from pdf_server.search_pdf import search_pdf
from PIL import Image


def _flat(fn):
    """Call an input-enveloped tool with flat kwargs, as the provider's
    EnvelopeCompatMiddleware port allows a model to do."""

    spec = fn.__published_spec__["parameters"]["properties"]
    field_order = list(next(iter(spec.values()))["properties"])

    def call(*args, **kwargs):
        kwargs.update(zip(field_order, args, strict=False))
        return fn(**_wrap_flat(fn, kwargs))

    return call


create_pdf = _flat(create_pdf)
read_pdf_pages = _flat(read_pdf_pages)

@pytest.fixture
def text_pdf(workspace):
    (workspace / "doc.pdf").write_bytes(
        minimal_pdf(["Hello World", "Second line of text"])
    )
    return "/doc.pdf"


@pytest.fixture
def multipage_pdf(workspace):
    doc = fitz.open()
    for n in (1, 2, 3):
        page = doc.new_page()
        page.insert_text((72, 100), f"Content of page {n}")
    (workspace / "multi.pdf").write_bytes(doc.tobytes())
    doc.close()
    return "/multi.pdf"


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def image_pdf(workspace):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Figure page")
    page.insert_image(fitz.Rect(72, 100, 172, 180), stream=_png_bytes())
    (workspace / "img.pdf").write_bytes(doc.tobytes())
    doc.close()
    return "/img.pdf"


@pytest.fixture
def encrypted_pdf(workspace):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "classified content")
    data = doc.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_128, user_pw="s3cret", owner_pw="s3cret"
    )
    doc.close()
    (workspace / "enc.pdf").write_bytes(data)
    return "/enc.pdf"


# ---------------------------------------------------------------- read_pdf_pages


def test_read_pdf_pages_text(text_pdf):
    result = read_pdf_pages(text_pdf)
    assert result.startswith("[pdf: pages=1, read=1]")
    assert "\n[page 1]\n" in result
    assert "Hello World" in result
    assert "Second line of text" in result


def test_read_pdf_pages_page_range(multipage_pdf):
    result = read_pdf_pages(multipage_pdf, pages=[2])
    assert result.startswith("[pdf: pages=3, read=1]")
    assert "[page 2]" in result
    assert "Content of page 2" in result
    assert "[page 1]" not in result
    assert "Content of page 1" not in result


def test_read_pdf_pages_filters_out_of_range_pages(multipage_pdf):
    result = read_pdf_pages(multipage_pdf, pages=[3, 99])
    assert result.startswith("[pdf: pages=3, read=1]")
    assert "Content of page 3" in result


def test_read_pdf_pages_error_strings(workspace, text_pdf):
    assert read_pdf_pages("doc.pdf") == "File path must start with /"
    assert read_pdf_pages("/doc.txt") == "File path must end with .pdf"
    assert read_pdf_pages("/missing.pdf") == "File not found: /missing.pdf"
    assert read_pdf_pages("/../escape.pdf") == "Invalid path: /../escape.pdf"
    (workspace / "adir.pdf").mkdir()
    assert read_pdf_pages("/adir.pdf") == "Not a file: /adir.pdf"


def test_read_pdf_pages_password(encrypted_pdf):
    no_pw = read_pdf_pages(encrypted_pdf)
    assert no_pw == (
        "Cannot read /enc.pdf: the document is password-protected and the empty "
        "password did not open it; pass the document's password in the `password` "
        "argument."
    )
    wrong = read_pdf_pages(encrypted_pdf, password="nope")
    assert wrong == (
        "Cannot read /enc.pdf: the document is password-protected and the password "
        "supplied in the `password` argument is incorrect."
    )
    unlocked = read_pdf_pages(encrypted_pdf, password="s3cret")
    assert "classified content" in unlocked


# -------------------------------------------------------------------- search_pdf


def test_search_pdf_hits_and_formatting(text_pdf):
    result = search_pdf(text_pdf, "world")  # case-insensitive by default
    lines = result.split("\n")
    assert lines[0] == 'Found 1 match(es) for "world":'
    assert lines[1] == ""
    assert lines[2].startswith("1. [Page 1, Line 1, Chars 6-11]: ")
    assert "Hello World" in lines[2]


def test_search_pdf_case_sensitive_and_no_match(text_pdf):
    result = search_pdf(text_pdf, "world", case_sensitive=True)
    assert result.startswith('No matches found for "world"')
    result = search_pdf(text_pdf, "zebra")
    assert result.startswith('No matches found for "zebra"')


def test_search_pdf_whole_word(text_pdf):
    assert search_pdf(text_pdf, "Worl", whole_word=True).startswith(
        'No matches found for "Worl"'
    )
    assert search_pdf(text_pdf, "World", whole_word=True).startswith(
        'Found 1 match(es) for "World":'
    )


def test_search_pdf_error_strings(workspace):
    assert (
        search_pdf("/missing.pdf", "x") == "Search failed: File not found: /missing.pdf"
    )
    assert (
        search_pdf("/../escape.pdf", "x")
        == "Search failed: Path traversal detected: path cannot escape PDF root"
    )
    assert search_pdf("relative.pdf", "x") == "Search failed: File path must start with /"
    assert search_pdf("/doc.txt", "x") == "Search failed: File path must end with .pdf"
    assert search_pdf("/doc.pdf", "") == "Search failed: Search query is required"


# -------------------------------------------------------------------- create_pdf


def test_create_pdf_roundtrip(workspace):
    result = create_pdf(
        directory="/out",
        file_name="report.pdf",
        content=[
            {"type": "heading", "text": "Quarterly Report", "level": 1},
            {"type": "paragraph", "text": "Revenue grew by twelve percent."},
            {"type": "bullet_list", "items": ["First point", "Second point"]},
            {"type": "table", "rows": [["Account", "Total"], ["Cash", "1200"]]},
        ],
        metadata={"title": "Report", "author": "Tester"},
    )
    assert result == "PDF report.pdf created at /out/report.pdf"
    assert (workspace / "out" / "report.pdf").is_file()

    reread = read_pdf_pages("/out/report.pdf")
    assert "Quarterly Report" in reread
    assert "Revenue grew by twelve percent." in reread
    assert "First point" in reread
    assert "Cash" in reread


def test_create_pdf_error_strings(workspace):
    blocks = [{"type": "paragraph", "text": "x"}]
    assert create_pdf("out", "a.pdf", blocks) == "Directory must start with /"
    assert create_pdf("/out", "a.txt", blocks) == "File name must end with .pdf"
    assert create_pdf("/out", "a/b.pdf", blocks) == "File name cannot contain /"
    assert create_pdf("/out", "a.pdf", []) == "Content must be a non-empty list"
    assert (
        create_pdf("/out", "a.pdf", [{"type": "bogus"}]) == "Unknown block type: bogus"
    )
    assert (
        create_pdf("/out", "a.pdf", [{"type": "paragraph", "text": ""}])
        == "Paragraph text must not be empty"
    )
    assert (
        create_pdf("/../out", "a.pdf", blocks)
        == "Path traversal detected: directory cannot escape PDF root"
    )


# ------------------------------------------------------------ read_page_as_image


def test_read_page_as_image(text_pdf):
    result = read_page_as_image(text_pdf, 1)
    assert isinstance(result, ImageContent)
    assert result.mime_type in ("image/jpeg", "image/png")
    data = base64.b64decode(result.base64_data)
    assert data[:2] == b"\xff\xd8"  # JPEG magic


def test_read_page_as_image_errors(text_pdf):
    assert (
        read_page_as_image(text_pdf, 5) == "Page 5 is out of range (PDF has 1 page(s))"
    )
    assert read_page_as_image("/missing.pdf", 1) == "File not found: /missing.pdf"
    assert read_page_as_image("/../x.pdf", 1) == "Invalid path: /../x.pdf"


# -------------------------------------------------------------------- read_image


def test_read_image_via_emitted_key(image_pdf):
    listing = read_pdf_pages(image_pdf)
    assert "[images: count=1]" in listing
    assert "ref=@page1_img0" in listing

    result = read_image(image_pdf, "page1_img0")
    assert isinstance(result, ImageContent)
    assert result.mime_type == "image/jpeg"
    data = base64.b64decode(result.base64_data)
    assert data[:2] == b"\xff\xd8"  # JPEG magic


def test_read_image_on_demand_after_cache_clear(image_pdf):
    IMAGE_CACHE.clear()
    result = read_image(image_pdf, "@page1_img0")  # leading @ is stripped
    assert isinstance(result, ImageContent)
    assert result.mime_type == "image/jpeg"


def test_read_image_errors(image_pdf):
    with pytest.raises(ValueError, match="not in the expected"):
        read_image(image_pdf, "not_a_key")
    with pytest.raises(ValueError, match=r"page 9 is out of range"):
        read_image(image_pdf, "page9_img0")
    with pytest.raises(ValueError, match=r"page 1 contains 1 embedded image\(s\)"):
        read_image(image_pdf, "page1_img5")
    with pytest.raises(ValueError, match="Invalid path"):
        read_image("/../img.pdf", "page1_img0")
