"""Output-format parity tests for the ported filesystem_server tools."""

import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest
from filesystem_server.get_directory_tree import get_directory_tree
from filesystem_server.get_file_metadata import get_file_metadata
from filesystem_server.list_files import list_files
from filesystem_server.read_image_file import read_image_file
from filesystem_server.read_text_file import read_text_file
from filesystem_server.search_files import search_files


def populate(workspace):
    (workspace / "a.txt").write_text("hello")
    sub = workspace / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world!!")
    return workspace


# ---------------------------------------------------------------- list_files


def test_list_files_format(workspace):
    populate(workspace)
    out = list_files("/")
    lines = out.splitlines()
    assert "'sub' (folder)" in lines
    assert "'a.txt' (text/plain file) 5 bytes" in lines
    assert out.endswith("\n")
    assert len(lines) == 2


def test_list_files_not_found_and_not_dir(workspace):
    populate(workspace)
    assert list_files("/nope") == "[not found: /nope]\n"
    assert list_files("/a.txt") == "[not a directory: /a.txt]\n"


def test_list_files_empty_dir_messages(workspace):
    root_msg = list_files("/")
    assert root_msg.startswith("Directory is empty.")
    assert "'/documents'" in root_msg
    (workspace / "empty").mkdir()
    assert list_files("/empty") == "No items found in '/empty'"


def test_list_files_traversal(workspace):
    assert list_files("/../") == "[access denied: /../]\n"


# ------------------------------------------------------------ read_text_file


def test_read_text_file_content(workspace):
    populate(workspace)
    assert read_text_file("/a.txt") == "hello"
    assert read_text_file("/sub/b.txt") == "world!!"


def test_read_text_file_unsupported_extension(workspace):
    (workspace / "x.bin").write_bytes(b"\x00")
    with pytest.raises(ValueError, match="Unsupported file type: 'bin'"):
        read_text_file("/x.bin")


def test_read_text_file_not_found(workspace):
    with pytest.raises(FileNotFoundError, match="File not found: /gone.txt"):
        read_text_file("/gone.txt")


def test_read_text_file_max_size(workspace):
    populate(workspace)
    with pytest.raises(
        ValueError, match=r"File too large: 5 bytes exceeds max_size of 3 bytes"
    ):
        read_text_file("/a.txt", max_size=3)
    # exactly max_size is read
    assert read_text_file("/a.txt", max_size=5) == "hello"
    with pytest.raises(ValueError, match=r"max_size must be >= 0"):
        read_text_file("/a.txt", max_size=-1)


def test_read_text_file_traversal(workspace):
    with pytest.raises(ValueError, match=r"Access denied: /\.\./x\.txt"):
        read_text_file("/../x.txt")


def test_read_text_file_relative_path_rejected(workspace):
    with pytest.raises(ValueError, match="File path must start with /"):
        read_text_file("a.txt")


def test_read_text_file_eml_rendering(workspace):
    msg = MIMEMultipart()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = "Quarterly report"
    msg.attach(MIMEText("Hello Bob,\nNumbers attached.", "plain"))
    att = MIMEApplication(b"PDFDATA", _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="report.pdf")
    msg.attach(att)
    (workspace / "mail.eml").write_bytes(msg.as_bytes())

    out = read_text_file("/mail.eml")
    assert "## Headers" in out
    assert "From: alice@example.com" in out
    assert "Subject: Quarterly report" in out
    assert "## Body (text/plain)\nHello Bob,\nNumbers attached." in out
    assert "## Attachments (1)" in out
    assert "- report.pdf (application/pdf, 7 bytes)" in out


def test_read_text_file_eml_malformed(workspace):
    (workspace / "bad.eml").write_bytes(b"\x00\x01notmail")
    with pytest.raises(ValueError, match="Cannot parse email file"):
        read_text_file("/bad.eml")


# ----------------------------------------------------------- read_image_file


def test_read_image_file_roundtrip(workspace):
    data = b"\x89PNG\r\n\x1a\nfakepngdata"
    (workspace / "pic.png").write_bytes(data)
    image = read_image_file("/pic.png")
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.base64_data) == data


def test_read_image_file_unsupported_format(workspace):
    with pytest.raises(ValueError, match="Unsupported image format: bmp"):
        read_image_file("/pic.bmp")


def test_read_image_file_not_found(workspace):
    with pytest.raises(FileNotFoundError, match="File not found: /gone.png"):
        read_image_file("/gone.png")


# -------------------------------------------------------------- search_files


def test_search_files_found_format(workspace):
    populate(workspace)
    out = search_files("*.txt")
    lines = out.splitlines()
    assert lines[0] == "Found 2 file(s) matching '*.txt':"
    assert set(lines[1:]) == {"/a.txt", "/sub/b.txt"}


def test_search_files_non_recursive(workspace):
    populate(workspace)
    out = search_files("*.txt", recursive=False)
    assert out == "Found 1 file(s) matching '*.txt':\n/a.txt"


def test_search_files_truncation(workspace):
    populate(workspace)
    out = search_files("*.txt", max_results=1)
    assert out.startswith("Found 1 file(s) matching '*.txt':\n")
    assert out.endswith("\n\n(Results limited to 1)")


def test_search_files_no_match(workspace):
    populate(workspace)
    assert search_files("*.md", path="/sub") == "No files matching '*.md' found in /sub"
    root = search_files("*.md")
    assert root.startswith("No files matching '*.md' found in /. ")
    assert "sandbox root" in root


def test_search_files_errors(workspace):
    populate(workspace)
    assert search_files("*", path="/nope") == "[not found: /nope]"
    assert search_files("*", path="/a.txt") == "[not a directory: /a.txt]"
    assert search_files("*", path="/../") == "[access denied: /../]"
    with pytest.raises(ValueError, match="Pattern is required"):
        search_files("")
    with pytest.raises(ValueError, match="Path must start with /"):
        search_files("*", path="sub")


# -------------------------------------------------------- get_directory_tree


def test_get_directory_tree_rendering(workspace):
    populate(workspace)
    assert get_directory_tree("/") == "/\n├── sub/\n│   └── b.txt\n└── a.txt"


def test_get_directory_tree_show_size_and_dirs_only(workspace):
    populate(workspace)
    out = get_directory_tree("/", show_size=True)
    assert "└── a.txt (5 bytes)" in out
    assert get_directory_tree("/", include_files=False) == "/\n└── sub/"


def test_get_directory_tree_subdir_header_and_depth(workspace):
    populate(workspace)
    assert get_directory_tree("/sub") == "/sub/\n└── b.txt"
    assert get_directory_tree("/", max_depth=1) == "/\n├── sub/\n└── a.txt"


def test_get_directory_tree_empty_and_errors(workspace):
    (workspace / "empty").mkdir()
    assert get_directory_tree("/empty") == "/empty/\n(empty)"
    root = get_directory_tree("/../")
    assert root == "[access denied: /../]"
    assert get_directory_tree("/nope") == "[not found: /nope]"
    assert get_directory_tree("/empty/..") .startswith("/\n")
    empty_root = get_directory_tree("/empty").splitlines()
    assert empty_root[-1] == "(empty)"


def test_get_directory_tree_empty_root_message(workspace):
    out = get_directory_tree("/")
    assert out.splitlines()[0] == "/"
    assert "(empty - the sandbox root contains no visible files or folders." in out


# -------------------------------------------------------- get_file_metadata


def test_get_file_metadata_file_fields(workspace):
    populate(workspace)
    out = get_file_metadata("/a.txt")
    lines = out.splitlines()
    assert lines[0] == "Path: /a.txt"
    assert lines[1] == "Type: file"
    assert lines[2] == "MIME type: text/plain"
    assert lines[3] == "Size: 5 bytes (5 B)"
    assert lines[4].startswith("Permissions: ") and lines[4].endswith(")")
    assert lines[5].startswith("Modified: ")
    assert lines[6].startswith("Accessed: ")
    assert lines[7].startswith("Created/Changed: ")
    assert lines[8].startswith("Inode: ")
    assert lines[9].startswith("Device: ")
    assert lines[10] == "Hard links: 1"
    assert "+00:00" in lines[5]  # ISO 8601 UTC


def test_get_file_metadata_directory(workspace):
    populate(workspace)
    out = get_file_metadata("/sub")
    lines = out.splitlines()
    assert lines[0] == "Path: /sub"
    assert lines[1] == "Type: directory"
    assert "MIME type:" not in out


def test_get_file_metadata_symlink(workspace):
    populate(workspace)
    (workspace / "link.txt").symlink_to(workspace / "a.txt")
    out = get_file_metadata("/link.txt")
    lines = out.splitlines()
    assert lines[0] == "Path: /link.txt"
    assert lines[1] == "Type: symlink"
    assert lines[2] == "Symlink target: /a.txt"
    assert "Type: file" not in out
    assert "Size: 5 bytes (5 B)" in out  # stat of the resolved target


def test_get_file_metadata_symlink_outside(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("secret")
    (workspace / "evil").symlink_to(outside)
    out = get_file_metadata("/evil")
    assert out == (
        "Path: /evil\n"
        "Type: symlink (target outside sandbox - access denied)\n"
        "Symlink target: (hidden - outside sandbox)"
    )


def test_get_file_metadata_errors(workspace):
    assert get_file_metadata("/nope") == "[not found: /nope]"
    assert get_file_metadata("/../etc") == "[access denied: /../etc]"
    with pytest.raises(ValueError, match="File path must start with /"):
        get_file_metadata("a.txt")
    with pytest.raises(ValueError, match="File path is required"):
        get_file_metadata("")
