# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Shim for upstream ``runner/utils/redis.py``.

Upstream builds a Redis client when REDIS_HOST/PORT/USER/PASSWORD are all set
and leaves it ``None`` otherwise. Atoll never configures Redis, so this is the
permanent "unconfigured" state; the vendored ``runner/utils/llm.py`` already
guards this import and no-ops the batch-spend budget meter on a None client.
"""

redis_client = None
