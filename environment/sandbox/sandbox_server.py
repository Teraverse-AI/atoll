"""Atoll companion sandbox server — runs as PID 1 inside an atoll-sandbox container.

One server per eval. The container has no network and sees only that eval's
workspace (/filesystem — Archipelago's APP_FS_ROOT) and a private channel volume (/channel) shared with the
eval's atoll-runner. The runner drops a request file into /channel/req; the server
runs it and writes the result to /channel/resp. Files, not sockets: plain files
work on every Docker Desktop volume backend.

Every call gets the same clean slate a fresh `docker run --rm` would give: after
it finishes (or times out) every other process in the container is killed and
/tmp is emptied. Only /filesystem persists between calls.

Stdlib only; never imports Atoll code.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHANNEL = Path(os.environ.get("ATOLL_CHANNEL_DIR", "/channel"))
WORKSPACE = Path("/filesystem")  # Archipelago's APP_FS_ROOT
TMP = Path("/tmp")
POLL_SECONDS = 0.02


def _write_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _other_pids() -> list[int]:
    return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit() and int(p.name) != 1]


def _kill_everything_else() -> None:
    """Kill every process but this server, then reap them (orphans re-parent to PID 1).

    Only as PID 1 of its own container: anywhere else this would hit unrelated
    processes, so the server refuses to start outside one (see main).
    """
    for pid in _other_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0] > 0:
                pass
        except ChildProcessError:
            pass
        if not _other_pids():
            return
        time.sleep(0.01)


def _empty_tmp() -> None:
    for child in TMP.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def _run(request: dict) -> dict:
    if request.get("kind") == "python":
        argv = ["python", "-I", "-c", request["code"]]
    elif request.get("kind") == "shell":
        argv = ["/bin/sh", "-c", request["code"]]
    else:
        return {"error": f"unknown request kind: {request.get('kind')!r}"}
    timeout = int(request.get("timeout", 30))
    # Output goes to anonymous (already-unlinked) files, not pipes: the call ends
    # when the main process exits — as a `docker run --rm` would — even if it left
    # background children holding stdout, and large output can never block it.
    with tempfile.TemporaryFile(dir=TMP) as out_f, tempfile.TemporaryFile(dir=TMP) as err_f:
        proc = subprocess.Popen(
            argv, cwd=WORKSPACE, stdout=out_f, stderr=err_f, start_new_session=True
        )
        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            timed_out = True
        _kill_everything_else()
        out_f.seek(0)
        err_f.seek(0)
        out, err = out_f.read(), err_f.read()
    result = {
        "stdout": out.decode("utf-8", errors="replace"),
        "stderr": err.decode("utf-8", errors="replace"),
        "exit_code": -1 if timed_out else proc.returncode,
        "timed_out": timed_out,
    }
    if timed_out and not result["stderr"]:
        result["stderr"] = f"timed out after {timeout}s"
    return result


def main() -> None:
    if os.getpid() != 1:
        sys.exit("sandbox_server must run as PID 1 of its own container (atoll-sandbox)")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    req_dir, resp_dir = CHANNEL / "req", CHANNEL / "resp"
    req_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(CHANNEL / "ready", {"pid": os.getpid(), "started": time.time()})
    while True:
        pending = sorted(p for p in req_dir.glob("*.json") if not p.name.startswith("."))
        if not pending:
            time.sleep(POLL_SECONDS)
            continue
        req_path = pending[0]
        try:
            request = json.loads(req_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            request, result = {"id": req_path.stem}, {"error": f"bad request: {exc}"}
        else:
            try:
                result = _run(request)
            except Exception as exc:  # report, never crash the server
                result = {"error": f"{type(exc).__name__}: {exc}"}
        req_path.unlink(missing_ok=True)
        _kill_everything_else()  # also covers bad/unknown requests
        _empty_tmp()
        _write_atomic(resp_dir / f"{request.get('id', req_path.stem)}.json", result)


if __name__ == "__main__":
    main()
