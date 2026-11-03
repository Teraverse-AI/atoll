"""Unit tests for the runner's network setting and egress allow-list (no Docker)."""

import pytest
from atoll_env.network import NetworkConfigError, egress_allow_list, resolve_network


def _task(tmp_path, atoll_table: str = "") -> str:
    (tmp_path / "task.toml").write_text(f'[metadata]\nid = "t"\n[atoll]\n{atoll_table}\n')
    return str(tmp_path)


def test_default_is_off(tmp_path):
    assert resolve_network(_task(tmp_path), env={}) == "off"


def test_precedence_cli_over_toml_over_env(tmp_path):
    task = _task(tmp_path, 'network = "on"')
    assert resolve_network(task, env={"ATOLL_NETWORK": "off"}) == "on"  # toml > env
    assert resolve_network(task, cli_value="off", env={}) == "off"  # cli > toml
    assert resolve_network(_task(tmp_path), env={"ATOLL_NETWORK": "on"}) == "on"  # env > default


@pytest.mark.parametrize("value,expected", [(True, "on"), (False, "off"), ("YES", "on"),
                                            ("0", "off")])
def test_toml_accepts_booleans_and_words(tmp_path, value, expected):
    literal = str(value).lower() if isinstance(value, bool) else f'"{value}"'
    assert resolve_network(_task(tmp_path, f"network = {literal}"), env={}) == expected


def test_invalid_value_is_rejected(tmp_path):
    with pytest.raises(NetworkConfigError):
        resolve_network(_task(tmp_path), cli_value="maybe", env={})


def test_allow_list_from_litellm_base_url():
    assert egress_allow_list({"LITELLM_BASE_URL": "https://GW.Example.com/"}) == [
        "gw.example.com:443"]
    assert egress_allow_list({"LITELLM_BASE_URL": "http://host.docker.internal:4000"}) == [
        "host.docker.internal:4000"]


def test_explicit_allow_list_wins_and_is_normalized():
    env = {"LITELLM_BASE_URL": "https://gw.example.com",
           "ATOLL_EGRESS_ALLOW": "api.openai.com:443, https://generativelanguage.googleapis.com"}
    assert egress_allow_list(env) == ["api.openai.com:443",
                                      "generativelanguage.googleapis.com:443"]


def test_off_without_endpoint_is_an_error():
    with pytest.raises(NetworkConfigError, match="ATOLL_EGRESS_ALLOW"):
        egress_allow_list({})
    with pytest.raises(NetworkConfigError):
        egress_allow_list({"ATOLL_EGRESS_ALLOW": "no-port-here"})
