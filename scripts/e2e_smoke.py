"""End-to-end smoke: run the real atoll-run CLI path on q001 with a scripted LLM.

Exercises the ported parity tools for real: filesystem list, PDF read (PyMuPDF),
sheet read (openpyxl), and a genuine sandboxed container write via
python_code_execution — then verifies every artifact.
"""

import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import runner.llm


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        reasoning_content=None,
        thinking_blocks=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=30,
            completion_tokens_details=None,
            prompt_tokens_details=None,
        ),
    )


def _call(cid, name, args):
    return SimpleNamespace(
        id=cid,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


entry = {
    "date": "2025-12-31",
    "memo": "Record 3 months of expired insurance (Oct-Dec 2025)",
    "entries": [
        {"account": "Insurance Expense", "debit": 900.0, "credit": 0.0},
        {"account": "Prepaid Insurance", "debit": 0.0, "credit": 900.0},
    ],
}
write_code = (
    "import json\n"
    f"entry = json.loads({json.dumps(json.dumps(entry))})\n"
    "with open('adjusting_entry.json', 'w') as f:\n"
    "    json.dump(entry, f, indent=2)\n"
    "print('written')\n"
)

script = [
    _response(tool_calls=[_call("c1", "filesystem_server_list_files", {"path": "/"})]),
    _response(tool_calls=[_call("c2", "pdf_server_read_pdf_pages",
                                {"file_path": "/invoice.pdf"})]),
    _response(tool_calls=[_call("c3", "sheets_server_read_tab",
                                {"file_path": "/ledger.xlsx", "tab_index": 0})]),
    _response(tool_calls=[_call("c4", "code_execution_server_python_code_execution",
                                {"code": write_code})]),
    _response(content="Expired insurance: 900.00. Remaining prepaid: 2700.00. "
                      "Wrote adjusting_entry.json."),
]
calls_seen = []


def fake_generate_response(model, messages, tools, llm_response_timeout, extra_args, **kwargs):
    calls_seen.append(bool(tools))
    return script.pop(0)


runner.llm.generate_response = fake_generate_response

out_dir = Path(sys.argv[1])
sys.argv = ["atoll-run", "--task", "examples/q001_adjusting_entry", "--model", "mock-model",
            "--output", str(out_dir)]
from runner.main import main  # noqa: E402 (must import after the llm patch)

main()

# ---- assertions on artifacts ----
result = json.loads((out_dir / "result.json").read_text())
assert result["status"] == "completed", result["status"]
assert result["final_answer"].startswith("Expired insurance: 900.00"), result["final_answer"]
assert result["error"] is None and result["fault"] is None
usage = result["usage"]
assert len(usage["call_log"]) == 5, len(usage["call_log"])
assert usage["total_tokens"] == 650, usage["total_tokens"]
assert usage["accounting_mode"] == "token_accounting"  # task.toml token_budget promotes it
assert usage["token_budget"] == 128000

lines = [json.loads(ln) for ln in (out_dir / "trajectory.jsonl").read_text().splitlines()]
assert lines[-1]["type"] == "result"
assert lines[0]["role"] == "system"
tool_msgs = [ln for ln in lines if ln.get("role") == "tool"]
assert len(tool_msgs) == 4
assert all(m.get("name") for m in tool_msgs)

ls_obs = tool_msgs[0]["content"]
assert "'invoice.pdf'" in ls_obs and "'ledger.xlsx'" in ls_obs, ls_obs[:200]

pdf_obs = tool_msgs[1]["content"]
assert "3,600.00" in pdf_obs, pdf_obs[:400]
assert "2025-10-01" in pdf_obs

tab_obs = tool_msgs[2]["content"]
assert "Prepaid Insurance" in tab_obs, tab_obs[:400]

exec_obs = json.loads(tool_msgs[3]["content"])
assert exec_obs["success"] is True, exec_obs
assert "written" in exec_obs["stdout"]

ws = next(d for d in out_dir.iterdir() if d.is_dir() and d.name.startswith("ws_"))
written = json.loads((ws / "adjusting_entry.json").read_text())
assert written["entries"][0]["debit"] == 900.0

with tarfile.open(out_dir / "initial_snapshot.tar.gz") as tar:
    assert "adjusting_entry.json" not in tar.getnames()
with tarfile.open(out_dir / "final_snapshot.tar.gz") as tar:
    assert "adjusting_entry.json" in tar.getnames()

print("\nE2E MOCK RUN (parity tools, real container write): ALL ARTIFACT CHECKS PASSED")
