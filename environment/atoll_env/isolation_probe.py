"""Isolation probe: run INSIDE an atoll-runner container started by scripts/run_eval.sh.

Checks, from both sides of one eval's container pair, that the eval is sealed:
the runner and the companion sandbox each see only this eval's files, the sandbox
has no network and never sees the runner's credentials, neither has the Docker
socket, root, or a writable root filesystem, and every code call starts clean.

Writes /eval/run/probe.json and exits 1 if any check fails. Driven by
environment/tests/test_isolation.py (opt-in: ATOLL_ISOLATION_TESTS=1).
"""

import json
import os
import sys
import time
from pathlib import Path

from atoll_env.sandbox import ContainerSandbox
from atoll_env.workspace import create_workspace

RUN = Path("/eval/run")
WORKSPACE = RUN / "workspace"

_SANDBOX_REPORT = r"""
import json, os, socket
r = {"uid": os.getuid(), "env": dict(os.environ)}
def net(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return "connected"
    except Exception as e:
        return type(e).__name__
r["net_ip"] = net("1.1.1.1", 53)
r["net_dns"] = net("host.docker.internal", 4000)
markers, gold = [], []
for top in ("/",):
    for d, subdirs, files in os.walk(top):
        if d.startswith(("/proc", "/sys", "/dev")):
            subdirs[:] = []
            continue
        markers += [os.path.join(d, f) for f in files if f.startswith("marker_")]
        gold += [os.path.join(d, f) for f in files if f == "gold_answer.json"]
r["markers"], r["gold"] = markers, gold
r["task_visible"] = os.path.exists("/task")
r["docker_sock"] = os.path.exists("/var/run/docker.sock")
def writable(p):
    try:
        open(p, "w").close(); os.remove(p); return True
    except OSError:
        return False
r["root_writable"] = writable("/usr/probe")
r["workspace_writable"] = writable("/filesystem/.probe")
print(json.dumps(r))
"""


def _direct(host: str, port: int) -> str:
    """Open a plain TCP connection, bypassing any proxy: 'connected' or the error name."""
    import socket

    try:
        socket.create_connection((host, port), timeout=4).close()
        return "connected"
    except OSError as exc:
        return type(exc).__name__


def _via_proxy(target: str) -> str:
    """Ask the egress proxy to CONNECT to host:port; return its status line."""
    import socket
    from urllib.parse import urlsplit

    proxy = urlsplit(os.environ["HTTPS_PROXY"])
    with socket.create_connection((proxy.hostname, proxy.port), timeout=10) as conn:
        conn.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return conn.recv(200).split(b"\r\n", 1)[0].decode(errors="replace")


def _litellm_through(allowed: str) -> str:
    """A real LiteLLM call to the allowed host: any HTTP-level error proves the
    request left through the proxy; a connection error means it could not."""
    import asyncio

    # As runner/__init__.py does: no cost-map download at import (network is off).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    host = allowed.rsplit(":", 1)[0]

    async def call() -> str:
        try:
            await litellm.acompletion(
                model="openai/probe", api_base=f"https://{host}/v1", api_key="sk-probe",
                messages=[{"role": "user", "content": "hi"}], max_retries=0, timeout=20,
            )
            return "ok"
        except litellm.APIConnectionError as exc:
            return f"CONNECTION_ERROR {str(exc)[:120]}"
        except Exception as exc:
            return f"HTTP_LEVEL {type(exc).__name__}"

    return asyncio.run(call())


def _check_runner_network(check) -> None:
    mode = os.environ.get("ATOLL_NETWORK_MODE", "")
    allow = [a for a in os.environ.get("ATOLL_EGRESS_ALLOW", "").split(",") if a]
    check("runner: network mode known", mode in ("on", "off"), mode)
    # The same targets in both modes: the network-on eval proves they are reachable
    # from this machine, so the network-off evals failing to reach them is meaningful.
    host, _, port = (allow[0] if allow else "example.com:443").rpartition(":")
    targets = [(host, int(port)), ("8.8.8.8", 53)]
    if mode == "on":
        reach = {f"{h}:{p}": _direct(h, p) for h, p in targets}
        check("runner(network on): direct internet reachable",
              all(v == "connected" for v in reach.values()), reach)
        return
    reach = {f"{h}:{p}": _direct(h, p) for h, p in targets}
    check("runner(network off): no direct internet (by name and by IP)",
          all(v != "connected" for v in reach.values()), reach)
    check("runner(network off): host machine unreachable",
          _direct("host.docker.internal", 22) != "connected"
          and _direct("host.docker.internal", 4000) != "connected")
    check("runner(network off): private LAN unreachable",
          all(_direct(ip, 80) != "connected" for ip in ("192.168.1.1", "10.0.0.1", "172.17.0.1")))
    check("runner(network off): proxy refuses non-allow-listed host",
          " 403 " in f" {_via_proxy('github.com:443')} ", _via_proxy("github.com:443"))
    check("runner(network off): proxy refuses raw IP",
          " 403 " in f" {_via_proxy('1.1.1.1:443')} ", _via_proxy("1.1.1.1:443"))
    if allow:
        check("runner(network off): proxy admits the allow-listed endpoint",
              " 200 " in f" {_via_proxy(allow[0])} ", _via_proxy(allow[0]))
        result = _litellm_through(allow[0])
        check("runner(network off): LiteLLM reaches the endpoint via the proxy",
              result.startswith(("HTTP_LEVEL", "ok")), result)


def main() -> int:
    eval_id = os.environ["ATOLL_EVAL_ID"]
    secret = os.environ.get("ATOLL_PROBE_SECRET", "")
    checks: dict[str, tuple[bool, str]] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        checks[name] = (bool(ok), str(detail)[:300])

    # Prepare the workspace exactly as atoll-run does in a container (--workspace).
    create_workspace("/task/workspace", target_dir=WORKSPACE)
    (WORKSPACE / f"marker_{eval_id}.txt").write_text(eval_id)

    # --- runner side ---
    check("runner: no docker socket", not Path("/var/run/docker.sock").exists())
    check("runner: not root", os.getuid() != 0, os.getuid())
    for name, path in (("runner: root fs read-only", "/opt/atoll/probe"),
                       ("runner: task mount read-only", "/task/probe")):
        try:
            Path(path).write_text("x")
            check(name, False, f"wrote {path}")
        except OSError as exc:
            check(name, True, type(exc).__name__)
    other = [p for p in Path("/eval").rglob("marker_*") if p.name != f"marker_{eval_id}.txt"]
    check("runner: sees only its own eval", sorted(os.listdir("/eval")) == ["run"] and not other,
          other)

    # --- sandbox side (the real code-execution path, via the channel) ---
    sb = ContainerSandbox()
    check("runner: companion mode active", sb.channel is not None, sb.channel)
    res = sb.run_python(_SANDBOX_REPORT, WORKSPACE, timeout=60)
    try:
        rep = json.loads(res["stdout"])
    except (KeyError, ValueError):
        check("sandbox: report", False, res)
        rep = None
    if rep is not None:
        check("sandbox: no network (IP)", rep["net_ip"] != "connected", rep["net_ip"])
        check("sandbox: no network (DNS/host)", rep["net_dns"] != "connected", rep["net_dns"])
        check("sandbox: sees only its own workspace",
              rep["markers"] == [f"/filesystem/marker_{eval_id}.txt"], rep["markers"])
        check("sandbox: no task bundle / gold answer",
              not rep["task_visible"] and not rep["gold"], rep["gold"])
        check("sandbox: runner credentials not visible",
              bool(secret) and all(secret not in v for v in rep["env"].values())
              and "ATOLL_PROBE_SECRET" not in rep["env"], sorted(rep["env"]))
        check("sandbox: no docker socket", not rep["docker_sock"])
        check("sandbox: not root", rep["uid"] != 0, rep["uid"])
        check("sandbox: root fs read-only", not rep["root_writable"])
        check("sandbox: workspace writable", rep["workspace_writable"])

    t0 = time.monotonic()
    res = sb.run_shell("sleep 300 & echo started; echo scratch > /tmp/leftover", WORKSPACE,
                       timeout=20)
    took = time.monotonic() - t0
    check("sandbox: background child does not block the call",
          took < 5 and not res.get("timed_out") and "started" in res.get("stdout", ""),
          f"{took:.1f}s {res}")
    res = sb.run_python(
        "import os\n"
        "names = [open(f'/proc/{p}/comm').read().strip() for p in os.listdir('/proc')"
        " if p.isdigit() and os.path.exists(f'/proc/{p}/comm')]\n"
        "print('SLEEPERS', names.count('sleep'))\n"
        "print('TMP_KEPT' if os.path.exists('/tmp/leftover') else 'TMP_CLEAN')",
        WORKSPACE, timeout=10)
    out = res.get("stdout", "")
    check("sandbox: background processes killed between calls", "SLEEPERS 0" in out, out)
    check("sandbox: /tmp reset between calls", "TMP_CLEAN" in out, out)

    res = sb.run_shell("sleep 30", WORKSPACE, timeout=2)
    check("sandbox: timeout enforced", res.get("timed_out") is True, res)

    res = sb.run_python("b = bytearray(900 * 1024 * 1024); print('allocated')", WORKSPACE,
                        timeout=60)
    check("sandbox: memory cap enforced", "allocated" not in res.get("stdout", ""), res)

    res = sb.run_python(
        "import subprocess\n"
        "n = 0\n"
        "try:\n"
        "    for _ in range(300):\n"
        "        subprocess.Popen(['sleep', '30']); n += 1\n"
        "except OSError as e:\n"
        "    print('LIMIT', n, type(e).__name__)\n"
        "else:\n"
        "    print('NOLIMIT', n)",
        WORKSPACE, timeout=30)
    out = res.get("stdout", "").split()
    check("sandbox: process count capped",
          len(out) >= 2 and out[0] == "LIMIT" and int(out[1]) < 128, res)

    res = sb.run_python("print('still alive')", WORKSPACE, timeout=30)
    check("sandbox: recovers after abuse", res.get("stdout", "").strip() == "still alive", res)

    # --- runner network (the eval's `network` setting) ---
    _check_runner_network(check)

    # --- the model-facing tool, end to end ---
    from atoll_tools.context import set_workspace_root
    from atoll_tools.registry import load_tools

    set_workspace_root(WORKSPACE)
    tools = load_tools(["code_execution_server"])
    name = next(n for n in tools if n.endswith("python_code_execution"))
    tool_out = tools[name](code="print(6 * 7)")
    check("tool: python_code_execution works", "42" in json.dumps(tool_out, default=str),
          tool_out)

    failed = [k for k, (ok, _) in checks.items() if not ok]
    report = {"eval_id": eval_id, "failed": failed,
              "checks": {k: {"ok": ok, "detail": d} for k, (ok, d) in checks.items()}}
    (RUN / "probe.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"eval_id": eval_id, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
