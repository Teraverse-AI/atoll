# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Minimal settings shim for upstream ``runner/utils/settings.py``.

The vendored LLM layer (``runner/utils/llm.py``) reads exactly the surface
exposed here. Atoll's only routing knobs are two environment variables:

* ``LITELLM_BASE_URL`` / ``LITELLM_API_KEY`` — when set, every call is sent to
  the configured OpenAI-compatible endpoint (e.g. a LiteLLM proxy), with the
  same per-call wiring upstream's proxy branch applies (``setdefault`` of
  api_base/api_key, and the call timeout forwarded in ``extra_body`` with a
  client-side response buffer).
* when unset, ``apply_llm_target`` leaves kwargs untouched and litellm routes
  DIRECT to the provider (provider-prefixed model names + provider API keys
  from the environment, e.g. ``GEMINI_API_KEY``).

Gateway routing does not exist in Atoll: ``is_gateway_routed()`` is always
False, so the vendored gateway branches are vendored-but-disabled, never
subtracted.
"""

import os
from contextvars import ContextVar
from enum import Enum
from typing import Any

# Per-unit-of-work id threaded into spend-attribution headers by the vendored
# llm.py (upstream sets it from the trajectory batch id). Atoll never sets it.
work_unit_ctx: ContextVar[str | None] = ContextVar("llm_work_unit", default=None)

# Upstream widens the client-side read timeout past the proxy's own clock so
# the proxy's error is received rather than a local timeout. Same value as
# upstream's DIRECT_PROXY_CLIENT_RESPONSE_BUFFER_S.
DIRECT_PROXY_CLIENT_RESPONSE_BUFFER_S = 30


class Environment(Enum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class Settings:
    """Env-backed stand-in exposing the attributes the vendored code reads.

    Proxy attributes are properties so a test (or a late ``load_dotenv``) that
    changes the environment is observed on the next call.
    """

    ENV = Environment.LOCAL

    @property
    def LITELLM_PROXY_API_BASE(self) -> str | None:  # noqa: N802 — upstream name
        return os.environ.get("LITELLM_BASE_URL") or None

    @property
    def LITELLM_PROXY_API_KEY(self) -> str | None:  # noqa: N802 — upstream name
        # Mirrors the env key; also read by the vendored CAS-denylist check.
        return os.environ.get("LITELLM_API_KEY") or None

    # No gateway in Atoll; attribute read by the vendored CAS-denylist check.
    LLM_GATEWAY_API_BASE: str | None = None
    LLM_GATEWAY_API_KEY: str | None = None

    def is_gateway_routed(self) -> bool:
        """Always False: Atoll has no gateway deployment."""
        return False

    def priority_for_workload(self, workload: str | None) -> int:
        """Upstream's default P3 (middle) — only feeds disabled telemetry here."""
        del workload
        return 3

    def apply_llm_target(
        self,
        kwargs: dict[str, Any],
        *,
        campaign_id: str | None = None,
        workload: str | None = None,
        priority: int | None = None,
    ) -> None:
        """Set api_base / api_key on ``kwargs`` from the environment, or leave
        them untouched so litellm routes direct to the provider.

        Mirrors upstream's LiteLLM-proxy branch (``setdefault``, and the call
        timeout forwarded via ``extra_body`` so the proxy sees it while the
        client waits a little longer). The gateway branch does not exist here.
        """
        del campaign_id, workload, priority
        base = self.LITELLM_PROXY_API_BASE
        if not base:
            return
        kwargs.setdefault("api_base", base)
        key = self.LITELLM_PROXY_API_KEY
        if key:
            kwargs.setdefault("api_key", key)
        # The SDK consumes `timeout` locally, so the proxy never sees it and
        # falls back to its own default. extra_body survives litellm's
        # _map_openai_params, which strips unknown top-level kwargs.
        call_timeout = kwargs.get("timeout")
        if isinstance(call_timeout, (int, float)) and call_timeout > 0:
            body = dict(kwargs.get("extra_body") or {})
            body.setdefault("timeout", int(call_timeout))
            kwargs["extra_body"] = body
            kwargs["timeout"] = call_timeout + DIRECT_PROXY_CLIENT_RESPONSE_BUFFER_S


_settings = Settings()


def get_settings() -> Settings:
    return _settings
