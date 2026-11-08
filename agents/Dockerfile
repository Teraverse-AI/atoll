# The Atoll runner image: agent loop + LLM layer + native tools, one eval per
# container; also the grader (atoll-grade entrypoint). Model-written code never runs
# here — it goes to the eval's companion atoll-sandbox container (network-less) over
# the /channel volume.
#
# Build:  scripts/build_runner_image.sh      (tags atoll-runner:latest; context = repo root)
# Run:    scripts/run_eval.sh                (the reference launcher; any pipeline can issue the same commands)
# Grade:  scripts/run_grade.sh
#
# Only the uv workspace packages are copied in — never the repo root — so private
# files (CLAUDE.md, docs/private/, .env, runs/) cannot reach the image.
FROM python:3.12-slim
# LibreOffice, as Archipelago ships it (libreoffice-calc/-core in its environment
# image): sheets_server's read_tab recalculates formula cells with it exactly as
# upstream does, and the grader recalculates every workbook before judging. Writer
# serves the opt-in docs_server conversion. Headless (-nogui) builds; fonts-liberation
# as upstream.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-calc-nogui libreoffice-writer-nogui fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv

WORKDIR /opt/atoll
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
# 1) Third-party dependencies only, from the lockfile + member manifests — a layer
#    that survives source edits; the uv download cache persists across builds.
COPY pyproject.toml uv.lock ./
COPY agents/pyproject.toml agents/
COPY environment/pyproject.toml environment/
COPY native_tools/pyproject.toml native_tools/
COPY grading/pyproject.toml grading/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --python /usr/local/bin/python3.12
# 2) Atoll's own packages.
COPY agents/ agents/
COPY environment/ environment/
COPY native_tools/ native_tools/
COPY grading/ grading/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --python /usr/local/bin/python3.12 \
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
