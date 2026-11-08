"""Every file PATCHES.md lists as vendored verbatim is byte-identical to the pinned upstream."""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "agents" / "runner" / "utils" / "PATCHES.md"
ROW = re.compile(r"^\| `[^`]+` \| `(?P<local>[^`]+)` \| `(?P<sha>[0-9a-f]{64})` \|$", re.M)
ROWS = ROW.findall(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_lists_files():
    assert len(ROWS) >= 28  # the LLM layer + the grader's file-extraction layer


@pytest.mark.parametrize("local,sha", ROWS, ids=[r[0] for r in ROWS])
def test_vendored_file_is_unchanged(local, sha):
    assert hashlib.sha256((ROOT / local).read_bytes()).hexdigest() == sha
