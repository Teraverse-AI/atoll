"""Per-model request settings from ``model_config.yaml`` (a client-side model_list).

Copy ``model_config.example.yaml`` beside this file to ``model_config.yaml`` and edit
it; without one, every model passes through unchanged. Entries are matched by the
exact name passed as ``--model``:

* **Direct calls** (no endpoint, or a model in ``ATOLL_DIRECT_MODELS``) get the whole
  entry: ``model`` (the provider's model id), ``api_key`` / ``api_base``,
  ``extra_headers`` and any other request settings (``extra_body``, ...). Settings the
  caller passes win.
* **Endpoint calls** get only ``extra_headers`` — the endpoint resolves the model.

A value ``os.environ/NAME`` reads that environment variable at call time; when it is
unset or empty the setting is skipped, and a skipped ``api_key`` skips ``api_base``
too, so the provider's default credentials and base apply.
"""

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).with_name("model_config.yaml")
_ENV_PREFIX = "os.environ/"
_SKIP = object()


@cache
def _entries() -> dict[str, dict[str, Any]]:
    if not CONFIG_PATH.is_file():
        return {}
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {m["model_name"]: m.get("litellm_params") or {} for m in data.get("model_list", [])}


def _value(value: Any) -> Any:
    """``value`` with ``os.environ/NAME`` references resolved; ``_SKIP`` when unset."""
    if isinstance(value, str) and value.startswith(_ENV_PREFIX):
        return os.environ.get(value.removeprefix(_ENV_PREFIX)) or _SKIP
    if isinstance(value, dict):
        resolved = {k: _value(v) for k, v in value.items()}
        return {k: v for k, v in resolved.items() if v is not _SKIP}
    return value


def _merge_headers(extra_args: dict[str, Any], headers: Any) -> dict[str, Any]:
    headers = _value(headers or {})
    if not headers:
        return extra_args
    return {**extra_args, "extra_headers": {**headers, **(extra_args.get("extra_headers") or {})}}


def resolve(model: str, extra_args: dict[str, Any], *, direct: bool) -> tuple[str, dict[str, Any]]:
    """The model name and extra args to send for a call to ``model``."""
    params = _entries().get(model.removeprefix("litellm_proxy/"))
    if params is None:
        return model, extra_args
    if not direct:
        return model, _merge_headers(extra_args, params.get("extra_headers"))
    merged = _merge_headers(dict(extra_args), params.get("extra_headers"))
    key = _value(params.get("api_key"))
    for name, value in params.items():
        if name in ("model", "extra_headers") or (name == "api_base" and key is _SKIP):
            continue
        value = _value(value)
        if value is _SKIP:
            continue
        if name == "extra_body":
            merged["extra_body"] = {**value, **(merged.get("extra_body") or {})}
        elif name == "api_base":
            # litellm's base_url alias: an explicit api_base would make the vendored layer
            # attach its endpoint spend-tag headers to a provider call.
            merged.setdefault("base_url", value)
        else:
            merged.setdefault(name, value)
    return params.get("model", model), merged
