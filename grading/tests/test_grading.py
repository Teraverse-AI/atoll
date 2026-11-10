"""Grader: inputs, rendering (with the Atoll patches), judge parsing and the score fold."""

import asyncio
import json
import tarfile
from pathlib import Path

import docx
import openpyxl
import pytest
import xlsxwriter
from atoll_grading import judge as judge_mod
from atoll_grading.inputs import changed_files, load_run, load_task
from atoll_grading.main import main
from atoll_grading.render import FULL_ROWS, RecalcError, render_files
from atoll_grading.score import fold
from atoll_tools.office import find_soffice

needs_soffice = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")

# ------------------------------------------------------------------ score fold


def test_fold_credit_penalty_veto():
    rubric = [{"rubric_weight": 4}, {"rubric_weight": 6}, {"rubric_weight": -5}, {"rubric_weight": 0}]
    s = fold(rubric, {1: 1, 2: 0, 3: 0, 4: 0})
    assert s["score"] == 40.0 and not s["vetoed"]
    assert set(s) == {"score", "vetoed", "earned", "penalty", "pos_total", "veto_nums"}
    s = fold(rubric, {1: 1, 2: 1, 3: 1, 4: 0})  # penalty fires: (10-5)/10
    assert s["score"] == 50.0
    s = fold(rubric, {1: 1, 2: 1, 3: 0, 4: 1})  # veto zeroes the run
    assert s["score"] == 0.0 and s["vetoed"] and s["veto_nums"] == [4]
    assert fold([{"rubric_weight": 2}, {"rubric_weight": -9}], {1: 1, 2: 1})["score"] == 0.0  # clamp


def test_fold_truncates_weights_like_the_rubrics_assume():
    assert fold([{"rubric_weight": 2.9}, {"rubric_weight": 1}], {1: 1, 2: 0})["score"] == pytest.approx(66.6667)

# ------------------------------------------------------------------ inputs


def _tar(path: Path, files: dict[str, bytes]) -> None:
    import io

    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_atoll_run_uses_the_snapshot_diff(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _tar(run / "initial_snapshot.tar.gz", {"in.txt": b"a", "same.txt": b"s"})
    _tar(run / "final_snapshot.tar.gz", {"in.txt": b"b", "same.txt": b"s", "new/out.txt": b"n",
                                          "~$lock.xlsx": b"x"})
    (run / "result.json").write_text(json.dumps({"final_answer": " done "}))
    r = load_run(run, tmp_path / "work")
    assert r.final_answer == "done" and sorted(r.files) == ["in.txt", "new/out.txt"]
    assert r.files["new/out.txt"].read_bytes() == b"n"


def test_snapshot_member_cannot_escape(tmp_path):
    _tar(tmp_path / "a.tgz", {})
    _tar(tmp_path / "b.tgz", {"../evil": b"x"})
    assert changed_files(tmp_path / "a.tgz", tmp_path / "b.tgz", tmp_path / "dest") == {}  # dot parts skipped
    _tar(tmp_path / "c.tgz", {"/abs/evil": b"x"})
    with pytest.raises(ValueError, match="escapes"):
        changed_files(tmp_path / "a.tgz", tmp_path / "c.tgz", tmp_path / "dest")
    assert not (tmp_path.parent / "evil").exists()


def test_delivery_layout(tmp_path):
    t = tmp_path / "T-0001"
    (t / "workspace").mkdir(parents=True)
    (t / "reference" / "outputs").mkdir(parents=True)
    (t / "workspace" / "prompt.md").write_text("do it")
    (t / "reference" / "rubrics.json").write_text(json.dumps([{"rubric_number": 1, "rubric_weight": 2}]))
    (t / "reference" / "outputs" / "gold.txt").write_text("g")
    run = t / "results" / "m" / "run_1"
    (run / "agent_outputs").mkdir(parents=True)
    (run / "agent_outputs" / "a.txt").write_text("x")
    (run / "cli.json").write_text(json.dumps({"events": [{"type": "result", "result": "final"}]}))
    task = load_task(t)
    assert task.prompt == "do it" and list(task.reference_files) == ["gold.txt"]
    r = load_run(run, tmp_path / "w")
    assert r.final_answer == "final" and list(r.files) == ["a.txt"]


def test_atoll_delivery_task(tmp_path):
    t = tmp_path / "1700000000-1"  # e.g. a pipeline's temp working copy
    (t / "filesystem").mkdir(parents=True)
    (t / "reference" / "outputs").mkdir(parents=True)
    (t / "prompt.md").write_text("do it")
    (t / "metadata.json").write_text(json.dumps({"task_id": "AG-0001-ACC-v1"}))
    (t / "reference" / "rubrics.json").write_text(json.dumps([{"rubric_number": 1, "rubric_weight": 2}]))
    (t / "reference" / "outputs" / "gold.txt").write_text("g")
    task = load_task(t)
    assert task.id == "AG-0001-ACC-v1" and task.prompt == "do it"
    assert list(task.reference_files) == ["gold.txt"]


def test_atoll_bundle(tmp_path):
    (tmp_path / "reference").mkdir()
    (tmp_path / "task.toml").write_text('[metadata]\nid = "q9"\n[atoll]\nrubrics = "reference/rubric.json"\n')
    (tmp_path / "instruction.md").write_text("hi")
    (tmp_path / "reference" / "rubric.json").write_text(json.dumps({"rubrics": [{"rubric_weight": 1}]}))
    (tmp_path / "reference" / "answer.xlsx").write_bytes(b"x")
    task = load_task(tmp_path)
    assert task.id == "q9" and list(task.reference_files) == ["answer.xlsx"]  # rubric file excluded

# ------------------------------------------------------------------ rendering


def _placeholder_workbook(path: Path, rows: int = 3) -> None:
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("S")
    ws.write_row(0, 0, ["code", "qty", "double"])
    for r in range(1, rows):
        ws.write_row(r, 0, [f"CODE{r:05d}", r])
        ws.write_formula(r, 2, f"=B{r + 1}*2")  # xlsxwriter caches 0
    money = wb.add_format({"num_format": "0.00"})
    ws.write_number(rows, 1, 1.5, money)
    wb.close()


@needs_soffice
def test_render_recalculates_placeholder_caches_and_shows_formats(tmp_path):
    _placeholder_workbook(tmp_path / "b.xlsx")
    r = asyncio.run(render_files({"b.xlsx": tmp_path / "b.xlsx"}))
    assert "=== File: b.xlsx (recalculated by LibreOffice) ===" in r.text
    assert "CODE00002\t2\t4" in r.text  # 2*2, not the cached placeholder 0
    assert "C3: =B3*2" in r.text  # authored formula text
    assert "B4: 1.50 [fmt: 0.00]" in r.text  # plain decimal format shown (Atoll patch)


@needs_soffice
def test_large_sheet_summary_and_related_rows(tmp_path):
    n = FULL_ROWS + 100
    _placeholder_workbook(tmp_path / "big.xlsx", rows=n)
    r = asyncio.run(render_files({"big.xlsx": tmp_path / "big.xlsx"}))
    assert len(r.large_sheets) == 1
    assert "【数据概要】" in r.text and f"数据行 {n}" in r.text
    assert f"合计 {sum(2 * i for i in range(1, n)):.4f}" in r.text  # summary over every recalculated row
    assert "CODE00300" not in r.text  # middle rows are not shown...
    rel = r.related("核对 CODE00300 的数量")
    assert "row 301: CODE00300\t300\t600" in rel and "C301: =B301*2" in rel  # ...unless named


def test_recalculation_failure_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOLL_SOFFICE", str(tmp_path / "missing-soffice"))
    _placeholder_workbook(tmp_path / "b.xlsx")
    with pytest.raises(RecalcError):
        asyncio.run(render_files({"b.xlsx": tmp_path / "b.xlsx"}))


def test_render_docx_and_unsupported(tmp_path):
    d = docx.Document()
    d.add_paragraph("结论：冲减成本")
    d.save(tmp_path / "m.docx")
    (tmp_path / "x.bin").write_bytes(b"\0")
    r = asyncio.run(render_files({"m.docx": tmp_path / "m.docx", "x.bin": tmp_path / "x.bin"}))
    assert "=== File: m.docx ===" in r.text and "结论：冲减成本" in r.text
    assert "(not rendered: unsupported file type .bin)" in r.text


def test_cached_workbook_is_not_recalculated(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    wb.active["A1"] = 3
    wb.save(tmp_path / "v.xlsx")  # no formulas: no LibreOffice needed
    monkeypatch.setenv("ATOLL_SOFFICE", str(tmp_path / "missing-soffice"))
    r = asyncio.run(render_files({"v.xlsx": tmp_path / "v.xlsx"}))
    assert "recalculated" not in r.text and "=== Sheet: Sheet ===" in r.text

# ------------------------------------------------------------------ judge


def test_parse_verdict():
    assert judge_mod.parse_verdict('xx {"verdict": 1, "rationale": "依据"} yy', 3)[:2] == (1, "依据")
    v, _, extra = judge_mod.parse_verdict(
        '{"deduction_for": "冲减成本", "reference_has_error": false, "verdict": 1, "rationale": "r"}', -20)
    assert v == 1 and extra == {"deduction_for": "冲减成本", "reference_has_error": False}
    with pytest.raises(ValueError):
        judge_mod.parse_verdict('{"verdict": 1, "rationale": "r"}', -5)  # penalty fields required
    with pytest.raises(ValueError):
        judge_mod.parse_verdict('{"verdict": 0.5}', 2)
    with pytest.raises(ValueError):
        judge_mod.parse_verdict("no json", 2)


def test_user_message_puts_the_item_last():
    p = judge_mod.Prompts.load()
    msg = judge_mod.build_user_message(p.item, task_prompt="TASK", final_answer="", candidate_files="CAND",
                                       reference_files="REF {{rubric_item}}", related_rows="",
                                       rubric_item='{"rubric_number": 7}')
    assert msg.index("TASK") < msg.index("CAND") < msg.index("REF") < msg.rindex('{"rubric_number": 7}')
    assert "REF {{rubric_item}}" in msg  # placeholders inside earlier blocks are not substituted
    assert "(无最终回复)" in msg and "（无）" in msg


def test_judge_item_reasks_on_unparseable_output(monkeypatch):
    replies = iter(["not json", '{"verdict": 1, "rationale": "ok"}'])

    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Resp:
        def __init__(self, c):
            self.choices = [type("C", (), {"message": _Msg(c)})()]
            self.usage = {"prompt_tokens": 10, "completion_tokens": 2}

    async def fake_generate_response(**kwargs):
        return _Resp(next(replies))

    import runner.llm

    monkeypatch.setattr(runner.llm, "generate_response", fake_generate_response)
    res = asyncio.run(judge_mod.judge_item("m", judge_mod.Prompts("S", "U"), "msg",
                                           {"rubric_number": 3, "rubric_weight": 2}, timeout=5, extra_args={}))
    assert (res.rubric_number, res.verdict, res.rationale) == (3, 1, "ok")
    assert res.usage == {"prompt_tokens": 20, "completion_tokens": 4}


def test_summarize_mean_score_per_model(tmp_path):
    def put(model, run, score):
        d = tmp_path / "results" / model / run
        d.mkdir(parents=True)
        (d / "score.json").write_text(json.dumps(
            {"score": score, "graded_at": "t"}))

    put("m1", "run_1", 40.0)
    put("m1", "run_2", 60.0)
    put("m1", "run_10", 20.0)
    put("m2", "run_1", 75.5)
    (tmp_path / "results" / "m3" / "run_1").mkdir(parents=True)  # ungraded: left out
    assert main(["--summarize", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "results" / "summary.json").read_text())
    assert set(summary) == {"m1", "m2"}
    assert [r["run"] for r in summary["m1"]["runs"]] == ["run_1", "run_2", "run_10"]
    assert summary["m1"]["mean_score"] == 40.0
    assert summary["m2"]["mean_score"] == 75.5
