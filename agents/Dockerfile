# The Atoll runner image: agent loop + LLM layer + native tools, one eval per
# container. Model-written code never runs here — it goes to the eval's companion
# atoll-sandbox container (network-less) over the /channel volume.
#
# Build:  scripts/build_runner_image.sh      (tags atoll-runner:latest; context = repo root)
# Run:    scripts/run_eval.sh                (the reference launcher; any pipeline can issue the same commands)
#
# Only the three uv workspace packages are copied in — never the repo root — so
# private files (CLAUDE.md, docs/private/, .env, runs/) cannot reach the image.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv

WORKDIR /opt/atoll
COPY pyproject.toml uv.lock ./
COPY agents/ agents/
COPY environment/ environment/
COPY native_tools/ native_tools/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.12 \
    && rm /usr/local/bin/uv

RUN useradd --uid 1000 --create-home --home-dir /home/atoll atoll \
    && mkdir -p /eval /task /channel \
    && chown atoll:atoll /eval /channel
USER 1000:1000
# Writable scratch lives on the /tmp tmpfs (the root filesystem is read-only at runtime).
ENV HOME=/tmp PATH=/opt/atoll/.venv/bin:$PATH \
    ATOLL_SANDBOX_CHANNEL=/channel PYTHONDONTWRITEBYTECODE=1

WORKDIR /eval
ENTRYPOINT ["atoll-run"]
