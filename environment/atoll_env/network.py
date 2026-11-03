"""The runner's network setting for containerized evals, and its egress allow-list.

``network`` is resolved like the loop's stop conditions (max_steps, ...):
CLI flag > task.toml ``[atoll] network`` > ``$ATOLL_NETWORK`` > default ``off``.

- ``off`` (default): the runner sits on a Docker *internal* network with no route
  out; its only exit is the eval's egress proxy, which admits nothing but the
  allow-listed LLM endpoint(s).
- ``on``: the runner gets ordinary outbound access (other evals still unreachable).

The code sandbox never has a network, whatever this says.

Allow-list: ``$ATOLL_EGRESS_ALLOW`` (comma-separated ``host:port``) if set, else the
host of ``$LITELLM_BASE_URL``. Direct-to-provider runs must list the provider
hosts explicitly (e.g. ``api.openai.com:443``).

CLI (run by scripts/run_eval.sh in a throwaway, network-less runner container)::

    python -m atoll_env.network --task /task [--network on|off]
    # prints:  network=off
    #          allow=gw.example.com:443
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_NETWORK = "off"
_TRUE = {"on", "true", "yes", "1"}
_FALSE = {"off", "false", "no", "0"}


class NetworkConfigError(ValueError):
    """An invalid network setting, or ``off`` with nothing to allow."""


def _normalize(value: object, source: str) -> str:
    text = str(value).strip().lower()
    if text in _TRUE:
        return "on"
    if text in _FALSE:
        return "off"
    raise NetworkConfigError(f"invalid network setting {value!r} from {source} (use on/off)")


def resolve_network(task_dir: str | Path, cli_value: str | None = None,
                    env: dict[str, str] | None = None) -> str:
    """'on' or 'off', by precedence CLI > task.toml [atoll].network > $ATOLL_NETWORK > off."""
    env = os.environ if env is None else env
    if cli_value is not None:
        return _normalize(cli_value, "--network")
    toml_path = Path(task_dir) / "task.toml"
    if toml_path.exists():
        atoll = tomllib.loads(toml_path.read_text(encoding="utf-8")).get("atoll", {})
        if "network" in atoll:
            return _normalize(atoll["network"], "task.toml [atoll].network")
    if env.get("ATOLL_NETWORK"):
        return _normalize(env["ATOLL_NETWORK"], "$ATOLL_NETWORK")
    return DEFAULT_NETWORK


def _host_port(entry: str, source: str) -> str:
    entry = entry.strip()
    if "://" in entry:
        parts = urlsplit(entry)
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    else:
        host, _, port_text = entry.rpartition(":")
        if not host or not port_text.isdigit():
            raise NetworkConfigError(f"allow-list entry {entry!r} from {source} must be host:port")
        port = int(port_text)
    if not host:
        raise NetworkConfigError(f"no host in {entry!r} from {source}")
    return f"{host.lower()}:{port}"


def egress_allow_list(env: dict[str, str] | None = None) -> list[str]:
    """The host:port pairs the egress proxy admits when network is off."""
    env = os.environ if env is None else env
    if env.get("ATOLL_EGRESS_ALLOW", "").strip():
        entries = [e for e in env["ATOLL_EGRESS_ALLOW"].split(",") if e.strip()]
        return sorted({_host_port(e, "$ATOLL_EGRESS_ALLOW") for e in entries})
    if env.get("LITELLM_BASE_URL", "").strip():
        return [_host_port(env["LITELLM_BASE_URL"], "$LITELLM_BASE_URL")]
    raise NetworkConfigError(
        "network is off but no LLM endpoint to allow: set LITELLM_BASE_URL, or list the "
        "provider hosts in ATOLL_EGRESS_ALLOW (e.g. api.openai.com:443) for direct mode"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m atoll_env.network", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--network", default=None, help="on|off (overrides task.toml/env)")
    args = parser.parse_args()
    try:
        network = resolve_network(args.task, args.network)
        allow = egress_allow_list() if network == "off" else []
    except NetworkConfigError as exc:
        sys.exit(f"error: {exc}")
    print(f"network={network}")
    print(f"allow={','.join(allow)}")


if __name__ == "__main__":
    main()
