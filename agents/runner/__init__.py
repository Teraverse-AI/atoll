"""Atoll agent runner (mirrors Archipelago's agents/runner/)."""

import os

# litellm fetches a remote model-cost map at import time by default, which
# fails offline (and in sandboxed test runs). The packaged local map is what
# upstream containers pin anyway; setdefault keeps an explicit override possible.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
