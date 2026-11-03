# The Atoll code-execution sandbox image: python + the SAME preinstalled package
# profile as Archipelago's code server (mcp_servers/code/profiles/hle_tools.txt),
# so python_code_execution behaves identically across the two systems.
#
# Build:  scripts/build_sandbox_image.sh      (tags atoll-sandbox:latest)
#
# Two ways it is used:
# - companion (containerized evals): the default CMD starts sandbox_server.py as
#   PID 1, serving one eval's code calls over /channel (see scripts/run_eval.sh);
# - per-call (host dev runs): `docker run --rm atoll-sandbox python -I -c ...`
#   overrides the CMD, one throwaway container per call.
ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

COPY requirements.txt /opt/atoll/requirements.txt
RUN pip install --no-cache-dir -r /opt/atoll/requirements.txt

COPY sandbox_server.py /opt/atoll/sandbox_server.py

# Unprivileged user; /workspace and /channel exist (owned by it) so fresh named
# volumes mounted there inherit that ownership.
RUN useradd --uid 1000 --create-home --home-dir /home/atoll atoll \
    && mkdir -p /workspace /channel \
    && chown atoll:atoll /workspace /channel
USER 1000:1000
# Writable scratch lives on the /tmp tmpfs (the root filesystem is read-only at runtime).
ENV HOME=/tmp MPLCONFIGDIR=/tmp PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
CMD ["python", "-I", "/opt/atoll/sandbox_server.py"]
