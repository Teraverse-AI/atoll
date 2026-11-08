"""Container sandbox for code execution. REQUIRED for run_python — never optional.

Model-written code never runs directly on the host. Two modes, same results:

- per-call (host dev runs): each call is a short-lived ``docker run --rm`` of the
  atoll-sandbox image, network disabled, only the task workspace bind-mounted;
- companion (containerized evals, ``$ATOLL_SANDBOX_CHANNEL`` set): the eval's own
  long-lived atoll-sandbox container (network-less, sees only this eval's
  workspace) runs the call; requests and results travel as files through the
  channel volume the two containers share. See scripts/run_eval.sh.
"""

import functools
import json
import os
import shutil
import subprocess
import time
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

_MAX_OUTPUT_CHARS = 32_768

NO_RUNTIME_ERROR = (
    "Docker not found. Atoll never runs model-written code directly on "
    "the host: install Docker Desktop (https://www.docker.com/products/docker-desktop/) "
    "so code executes in a network-less container."
)

CHANNEL_ENV = "ATOLL_SANDBOX_CHANNEL"
_CHANNEL_STARTUP_WAIT = 30  # seconds to wait for the companion's ready marker
_CHANNEL_GRACE = 30  # beyond the call's own timeout, before declaring the sandbox gone

NO_COMPANION_ERROR = (
    "Code sandbox is not running (no ready marker in {channel}). The eval's "
    "atoll-sandbox companion container must be started alongside the runner."
)

NO_IMAGE_ERROR = (
    "Sandbox image {image!r} not found. Build it once with "
    "scripts/build_sandbox_image.sh (it preinstalls the same package profile as "
    "Archipelago's code server), or point ATOLL_SANDBOX_IMAGE at an existing image."
)


@functools.cache
def load_manifest() -> dict[str, Any]:
    """The sandbox manifest: default image tag and its preinstalled packages.

    Mirrors Archipelago's manifest mechanism — the code-execution tool description
    names these packages, and environment/sandbox/requirements.txt (baked into the
    image) is generated from the same pins.
    """
    with resources.files("atoll_env").joinpath("sandbox_manifest.json").open() as fh:
        return json.load(fh)


def default_image() -> str:
    """The sandbox image: $ATOLL_SANDBOX_IMAGE, else the manifest's tag."""
    return os.environ.get("ATOLL_SANDBOX_IMAGE") or load_manifest()["image"]


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + f"\n... (truncated {len(text) - _MAX_OUTPUT_CHARS} chars)"


def detect_runtime() -> str | None:
    """The container runtime: ``docker`` when the CLI is on PATH, else None."""
    return "docker" if shutil.which("docker") else None


@functools.cache
def _image_exists(runtime: str, image: str) -> bool:
    """Whether ``image`` is available locally (cached per process)."""
    try:
        probe = subprocess.run(
            [runtime, "image", "inspect", image],
            capture_output=True,
            timeout=30,
        )
        return probe.returncode == 0
    except Exception:
        return False


class ContainerSandbox:
    """Runs Python snippets via ``<runtime> run --rm --network none``."""

    def __init__(self, runtime: str | None = None, image: str | None = None) -> None:
        self.channel = Path(c) if (c := os.environ.get(CHANNEL_ENV)) else None
        self.runtime = runtime or (None if self.channel else detect_runtime())
        self.image = image or default_image()

    def _via_channel(self, kind: str, code: str, timeout: int) -> dict[str, Any]:
        """Hand one call to the companion sandbox and wait for its result.

        The companion sees this eval's workspace at /filesystem (its cwd), so no
        path travels with the request.
        """
        channel = self.channel
        assert channel is not None
        deadline = time.monotonic() + _CHANNEL_STARTUP_WAIT
        while not (channel / "ready").exists():
            if time.monotonic() > deadline:
                return {"error": NO_COMPANION_ERROR.format(channel=channel)}
            time.sleep(0.1)
        call_id = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        req = channel / "req" / f"{call_id}.json"
        tmp = req.with_name(f".{req.name}.tmp")
        tmp.write_text(json.dumps({"id": call_id, "kind": kind, "code": code,
                                   "timeout": timeout}), encoding="utf-8")
        os.replace(tmp, req)
        resp = channel / "resp" / f"{call_id}.json"
        deadline = time.monotonic() + timeout + _CHANNEL_GRACE
        while not resp.exists():
            if time.monotonic() > deadline:
                req.unlink(missing_ok=True)
                return {"stdout": "", "stderr": f"timed out after {timeout}s",
                        "exit_code": -1, "timed_out": True}
            time.sleep(0.02)
        result = json.loads(resp.read_text(encoding="utf-8"))
        resp.unlink(missing_ok=True)
        return result

    def _check_ready(self) -> str | None:
        """None when runnable; otherwise the error text to hand the model."""
        if self.runtime is None:
            return NO_RUNTIME_ERROR
        if not _image_exists(self.runtime, self.image):
            return NO_IMAGE_ERROR.format(image=self.image)
        return None

    def run_python(self, code: str, workspace: str | Path, timeout: int = 30) -> dict[str, Any]:
        """Execute ``code`` with the workspace mounted at /filesystem (its cwd)."""
        if self.channel is not None:
            result = self._via_channel("python", code, timeout)
            for key in ("stdout", "stderr"):
                if key in result:
                    result[key] = _clip(result[key])
            return result
        if (error := self._check_ready()) is not None:
            return {"error": error}
        cmd = [
            self.runtime, "run", "--rm",
            "--network", "none",
            "--memory", "512m",
            "-v", f"{Path(workspace).resolve()}:/filesystem",
            "-w", "/filesystem",
            self.image,
            "python", "-I", "-c", code,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 30  # +30s image/VM startup
            )
            return {
                "stdout": _clip(proc.stdout),
                "stderr": _clip(proc.stderr),
                "exit_code": proc.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"timed out after {timeout}s", "exit_code": -1,
                    "timed_out": True}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def run_shell(self, command: str, workspace: str | Path, timeout: int = 30) -> dict[str, Any]:
        """Execute a shell ``command`` with the workspace mounted at /filesystem (its cwd).

        Same container flags as ``run_python``; the entrypoint is ``/bin/sh -c``.
        Output is returned unclipped — the code_exec tool applies its own
        (upstream-parity) truncation heuristics.
        """
        if self.channel is not None:
            return self._via_channel("shell", command, timeout)
        if (error := self._check_ready()) is not None:
            return {"error": error}
        cmd = [
            self.runtime, "run", "--rm",
            "--network", "none",
            "--memory", "512m",
            "-v", f"{Path(workspace).resolve()}:/filesystem",
            "-w", "/filesystem",
            self.image,
            "/bin/sh", "-c", command,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 30  # +30s image/VM startup
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:

            def _text(raw: Any) -> str:
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", errors="replace")
                return raw or ""

            return {
                "stdout": _text(exc.stdout),
                "stderr": _text(exc.stderr),
                "exit_code": -1,
                "timed_out": True,
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
