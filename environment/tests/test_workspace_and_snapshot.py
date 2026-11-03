import io
import tarfile

from atoll_env.snapshot import snapshot_workspace
from atoll_env.workspace import clean_workspace, create_workspace


def test_create_copies_template_and_is_writable(tmp_path):
    template = tmp_path / "template"
    (template / "sub").mkdir(parents=True)
    (template / "a.txt").write_text("A")
    (template / "sub" / "b.txt").write_text("B")

    ws = create_workspace(template, base_dir=tmp_path / "runs")
    assert (ws / "a.txt").read_text() == "A"
    assert (ws / "sub" / "b.txt").read_text() == "B"
    (ws / "new.txt").write_text("agent output")  # writable
    assert (template / "new.txt").exists() is False  # template untouched

    clean_workspace(ws)
    assert not ws.exists()


def test_snapshot_roundtrip(tmp_path):
    ws = tmp_path / "ws"
    (ws / "d").mkdir(parents=True)
    (ws / "d" / "x.txt").write_text("42")

    buffer = snapshot_workspace(ws)
    with tarfile.open(fileobj=io.BytesIO(buffer.getvalue()), mode="r:gz") as tar:
        names = tar.getnames()
        assert "d/x.txt" in names
        assert tar.extractfile("d/x.txt").read() == b"42"
