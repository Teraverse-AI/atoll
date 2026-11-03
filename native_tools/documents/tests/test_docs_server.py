"""Behavior and output-format parity tests for the ported docs_server tools."""

import pytest
from atoll_tools.content import ImageContent
from atoll_tools.published import wrap_flat_arguments as _wrap_flat
from docs_server.add_content_text import add_content_text
from docs_server.add_image import add_image
from docs_server.apply_formatting import apply_formatting
from docs_server.comments import comments
from docs_server.create_document import create_document
from docs_server.delete_content_text import delete_content_text
from docs_server.delete_document import delete_document
from docs_server.edit_content_text import edit_content_text
from docs_server.get_document_overview import get_document_overview
from docs_server.header_footer import header_footer
from docs_server.modify_image import modify_image
from docs_server.page_margins import page_margins
from docs_server.page_orientation import page_orientation
from docs_server.read_document_content import read_document_content
from docs_server.read_image import read_image
from docx import Document
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


add_image = _flat(add_image)
apply_formatting = _flat(apply_formatting)
comments = _flat(comments)
create_document = _flat(create_document)
header_footer = _flat(header_footer)
modify_image = _flat(modify_image)
page_margins = _flat(page_margins)
page_orientation = _flat(page_orientation)
read_document_content = _flat(read_document_content)

CONTENT = [
    {"type": "heading", "text": "Q4 Memo", "level": 1},
    {"type": "paragraph", "text": "Revenue grew."},
    {"type": "bullet_list", "items": ["cash", "accruals"]},
    {"type": "table", "rows": [["Account", "Amount"], ["Cash", "100"]]},
]


def make_doc(name="r.docx", content=CONTENT):
    out = create_document(directory="/", file_name=name, content=content)
    assert out == f"Document {name} created at /{name}"
    return f"/{name}"


# ------------------------------------------------------------ create_document


def test_create_document_blocks_and_metadata(workspace):
    out = create_document(
        directory="/",
        file_name="m.docx",
        content=CONTENT + [{"type": "page_break"}, {"type": "section_break"}],
        metadata={"title": "T", "author": "A"},
    )
    assert out == "Document m.docx created at /m.docx"
    doc = Document(workspace / "m.docx")
    assert doc.core_properties.title == "T"
    assert doc.core_properties.author == "A"
    assert len(doc.sections) == 2
    assert doc.paragraphs[0].text == "Q4 Memo"
    assert doc.tables[0].cell(0, 0).text == "Account"


def test_create_document_validation_errors(workspace):
    assert create_document(directory="", file_name="x.docx", content=CONTENT) == "Directory is required"
    assert create_document(directory="x", file_name="x.docx", content=CONTENT) == "Directory must start with /"
    assert create_document(directory="/", file_name="x.txt", content=CONTENT) == "File name must end with .docx"
    assert create_document(directory="/", file_name="a/b.docx", content=CONTENT) == "File name cannot contain /"
    assert create_document(directory="/", file_name="x.docx", content=[]) == "Content must be a non-empty list"
    assert (
        create_document(directory="/", file_name="x.docx", content=[{"type": "paragraph", "text": ""}])
        == "Paragraph text must not be empty"
    )
    assert (
        create_document(
            directory="/", file_name="x.docx",
            content=[{"type": "table", "rows": [["a", "b"], ["c"]]}],
        )
        == "All table rows must have the same number of cells"
    )
    # A block the discriminated union rejects raises, as the gateway did.
    with pytest.raises(Exception):
        create_document(directory="/", file_name="x.docx", content=[{"type": "nope"}])


def test_create_document_in_subdirectory(workspace):
    out = create_document(directory="/reports", file_name="s.docx", content=CONTENT)
    assert out == "Document s.docx created at /reports/s.docx"
    assert (workspace / "reports" / "s.docx").exists()


# ------------------------------------------------- overview / read content


def test_overview_structure_and_pagination(workspace):
    path = make_doc()
    out = get_document_overview(file_path=path)
    assert out.startswith(f"Document Overview: {path}")
    assert "Status: success" in out
    assert "Total Paragraphs: 4" in out
    assert "Total Headings: 1" in out
    assert "Total Pages: 1" in out
    assert "Heading 1: Q4 Memo" in out


def test_overview_errors(workspace):
    assert get_document_overview(file_path="/nope.docx") == "File not found: /nope.docx"
    assert get_document_overview(file_path="x.docx") == "File path must start with /"
    assert get_document_overview(file_path="/x.txt") == "File path must end with .docx"


def test_read_document_content_identifiers(workspace):
    path = make_doc()
    out = read_document_content(file_path=path)
    assert f"Document Content: {path}" in out
    assert "PAGINATION: Total 1 pages (use page_index 0 to 0)" in out
    assert "[body.p.0] (Heading 1): Q4 Memo" in out
    assert "body.tbl.0" in out
    assert "Revenue grew." in out


def test_read_document_content_bad_page(workspace):
    path = make_doc()
    out = read_document_content(file_path=path, page_index=5)
    assert out == "Invalid page_index: 5. Document has 1 pages (0 to 0)."


# ------------------------------------------------------ text edit tools


def test_add_edit_delete_text_round_trip(workspace):
    path = make_doc()
    out = add_content_text(file_path=path, identifier="body.p.1", text=" Costs fell.", position="end")
    assert "Status: success" in out and "Revenue grew. Costs fell." in out

    out = edit_content_text(file_path=path, identifier="body.p.1", new_text="Rewritten.")
    assert "Old Text: Revenue grew. Costs fell." in out
    assert "New Text: Rewritten." in out

    out = delete_content_text(file_path=path, identifier="body.p.1", scope="content")
    assert "Status: success" in out
    assert Document(workspace / "r.docx").paragraphs[1].text == ""


def test_text_tools_error_paths(workspace):
    path = make_doc()
    assert add_content_text(file_path=path, identifier="", text="x") == "Identifier is required"
    out = add_content_text(file_path=path, identifier="bogus.p.0", text="x")
    assert out.startswith("Failed to parse identifier 'bogus.p.0'.")
    out = add_content_text(file_path=path, identifier="body.p.0", text="x", position="middle")
    assert out == "Position must be one of: before, after, start, end"
    out = delete_content_text(file_path=path, identifier="body.tbl.0.r.0.c.0", scope="element")
    assert out == "Cell elements cannot be deleted. Use scope='content' to clear cell contents."


def test_edit_cell_text(workspace):
    path = make_doc()
    out = edit_content_text(file_path=path, identifier="body.tbl.0.r.1.c.1", new_text="250")
    assert "Status: success" in out
    assert Document(workspace / "r.docx").tables[0].cell(1, 1).text == "250"


# --------------------------------------------------------- apply_formatting


def test_apply_formatting(workspace):
    path = make_doc()
    out = apply_formatting(file_path=path, identifier="body.p.1", bold=True, font_size=14, font_color="#FF0000")
    assert "Runs Updated: 1" in out and "bold: True" in out and "font_size: 14.0" in out
    run = Document(workspace / "r.docx").paragraphs[1].runs[0]
    assert run.bold is True and run.font.size.pt == 14

    assert (
        apply_formatting(file_path=path, identifier="body.p.1", font_color="xyz")
        == "font_color must be a 6-hex RGB string like 'FF0000' or '#FF0000'"
    )


# --------------------------------------------------- margins / orientation


def test_page_margins_read_and_set(workspace):
    path = make_doc()
    out = page_margins(file_path=path, action="read")
    assert "Page Margins" in out and "Status: success" in out
    out = page_margins(file_path=path, action="set", top=1.5)
    assert "Top: 1.0 -> 1.5" in out
    assert page_margins(file_path=path, action="set") == (
        "At least one margin (top, bottom, left, right) is required for 'set' action"
    )
    assert page_margins(file_path=path, action="set", top=-1.0) == "Margin 'top' cannot be negative: -1.0"
    out = page_margins(file_path=path, action="grow")
    assert out == "Invalid action: grow. Must be one of: read, set"


def test_page_orientation_set_swaps_dimensions(workspace):
    path = make_doc()
    doc = Document(workspace / "r.docx")
    w, h = doc.sections[0].page_width, doc.sections[0].page_height
    out = page_orientation(file_path=path, action="set", orientation="landscape")
    assert "Orientation: portrait -> landscape" in out
    doc = Document(workspace / "r.docx")
    assert doc.sections[0].page_width == h and doc.sections[0].page_height == w
    assert page_orientation(file_path=path, action="set") == "Orientation is required for 'set' action"


# ------------------------------------------------------------------ comments


def test_comments_add_read_delete(workspace):
    path = make_doc()
    out = comments(file_path=path, action="add", identifier="body.p.1", text="check", author="Rev")
    assert "Comment ID: 0" in out
    out = comments(file_path=path, action="read")
    assert "Comment Count: 1" in out and "check" in out
    out = comments(file_path=path, action="delete", comment_id=0)
    assert "Status: success" in out
    assert "Comment Count: 0" in comments(file_path=path, action="read")
    assert comments(file_path=path, action="delete") == "comment_id is required for 'delete' action"
    assert comments(file_path=path, action="add", text="x") == "Identifier is required for 'add' action"


# -------------------------------------------------------------- header/footer


def test_header_footer_set_read_clear_link(workspace):
    path = make_doc()
    out = header_footer(
        file_path=path, action="set", area="footer",
        content=[{"type": "paragraph", "text": "Confidential"}],
    )
    assert "Blocks Added: 1" in out
    out = header_footer(file_path=path, action="read", area="footer")
    assert "Confidential" in out and "Status: success" in out
    out = header_footer(file_path=path, action="clear", area="footer")
    assert "Status: success" in out
    assert header_footer(file_path=path, action="set", area="footer") == "Content is required for 'set' action"
    assert (
        header_footer(file_path=path, action="link", area="header", link_to_previous=True)
        == "Cannot link section 0 to previous - it is the first section"
    )
    # Header/footer tables require an explicit width.
    out = header_footer(
        file_path=path, action="set", area="header",
        content=[{"type": "table", "rows": [["a"]]}],
    )
    assert out == "Table 'width' (in inches) is required for header/footer tables"


# -------------------------------------------------------------------- images


def _png(workspace, name="logo.png"):
    Image.new("RGB", (40, 30), (200, 10, 10)).save(workspace / name)
    return f"/{name}"


def test_image_round_trip(workspace):
    path = make_doc()
    img = _png(workspace)
    out = add_image(file_path=path, image_path=img, identifier="body.p.1", width=1.0)
    assert out == "Image added to body.p.1 at position end"

    out = read_document_content(file_path=path)
    assert "Images: 1" in out
    annotation = next(w for w in out.split() if w.startswith("@")).lstrip("@")

    result = read_image(file_path=path, annotation=annotation)
    assert isinstance(result, ImageContent)
    assert result.mime_type == "image/jpeg"
    assert len(result.base64_data) > 100

    out = modify_image(file_path=path, image_index=0, operation="rotate", rotation=90)
    assert out.endswith("rotated 90°")
    out = modify_image(file_path=path, image_index=3, operation="flip", flip="horizontal")
    assert out == "Image index 3 is out of range (0-0). Found 1 image(s) in document"


def test_add_image_errors(workspace):
    path = make_doc()
    assert (
        add_image(file_path=path, image_path="/x.gif", identifier="body.p.0")
        == "Unsupported image format: gif. Supported formats: jpg, jpeg, png"
    )
    img = _png(workspace)
    assert (
        add_image(file_path=path, image_path=img, identifier="body.p.0", position="top")
        == "Position must be 'start' or 'end', got: top"
    )


def test_read_image_uncached_error(workspace):
    path = make_doc()
    with pytest.raises(ValueError, match="Image not found in cache"):
        read_image(file_path=path, annotation="body_p_9_r9")


# ------------------------------------------------------------ delete_document


def test_delete_document(workspace):
    path = make_doc()
    assert delete_document(file_path=path) == f"Document {path} deleted successfully"
    assert not (workspace / "r.docx").exists()
    assert delete_document(file_path=path) == f"File not found: {path}"
    assert delete_document(file_path="/x.txt") == "File path must end with .docx"


# -------------------------------------------------------------- path safety


def test_path_traversal_rejected(workspace):
    make_doc()
    assert get_document_overview(file_path="/../r.docx") != "Status: success"
    assert delete_document(file_path="/../escape.docx") == "Invalid path: /../escape.docx"
