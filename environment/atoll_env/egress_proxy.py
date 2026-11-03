"""Per-eval egress proxy: the runner's only way out when ``network`` is ``off``.

Runs as ``<id>-egress`` (the atoll-runner image, this module as entrypoint), the
one container bridging the eval's internal network and the outside. It admits
only ``host:port`` pairs in ``$ATOLL_EGRESS_ALLOW`` and refuses everything else
with 403; every decision is logged to stdout (the launcher saves it as
``egress.log`` in the run dir).

- HTTPS: ``CONNECT host:port`` tunnels. TLS is end-to-end — the proxy never sees
  request contents or API keys. The proxy resolves the host itself, so a client
  cannot steer an allowed name to another address.
- Plain HTTP (e.g. a local gateway at ``http://host.docker.internal:4000``):
  absolute-form requests, rewritten to origin-form with ``Connection: close`` so
  every request on a connection is checked.

Stdlib only.
"""

import asyncio
import os
import sys
import time
from urllib.parse import urlsplit

LISTEN_PORT = int(os.environ.get("ATOLL_EGRESS_PORT", "3128"))
_HEAD_LIMIT = 64 * 1024
_HEAD_TIMEOUT = 30
_HOP_HEADERS = {b"connection", b"proxy-connection", b"keep-alive", b"proxy-authorization"}


def _allowed() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ATOLL_EGRESS_ALLOW", "").split(",")
            if e.strip()}


ALLOW = _allowed()


def log(verdict: str, what: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {verdict} {what}", flush=True)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _refuse(writer: asyncio.StreamWriter, status: str, target: str) -> None:
    body = f"atoll egress: {target} is not in the allow-list (network=off)\n".encode()
    writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()


async def _tunnel(client_r, client_w, up_r, up_w) -> None:
    await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_w.close()
        return
    lines = head.split(b"\r\n")
    try:
        method, target, version = lines[0].decode("latin-1").split(" ", 2)
    except ValueError:
        await _refuse(client_w, "400 Bad Request", "malformed request")
        return

    if method.upper() == "CONNECT":
        host, _, port = target.rpartition(":")
        key = f"{host.lower()}:{port}"
        if key not in ALLOW:
            log("DENY", f"CONNECT {target}")
            await _refuse(client_w, "403 Forbidden", target)
            return
        try:
            up_r, up_w = await asyncio.open_connection(host, int(port))
        except (OSError, ValueError) as exc:
            log("FAIL", f"CONNECT {target} ({type(exc).__name__})")
            await _refuse(client_w, "502 Bad Gateway", target)
            return
        log("ALLOW", f"CONNECT {target}")
        client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_w.drain()
        await _tunnel(client_r, client_w, up_r, up_w)
        return

    url = urlsplit(target)
    if url.scheme != "http" or not url.hostname:
        log("DENY", f"{method} {target}")
        await _refuse(client_w, "403 Forbidden", target)
        return
    key = f"{url.hostname.lower()}:{url.port or 80}"
    if key not in ALLOW:
        log("DENY", f"{method} {key}")
        await _refuse(client_w, "403 Forbidden", key)
        return
    try:
        up_r, up_w = await asyncio.open_connection(url.hostname, url.port or 80)
    except OSError as exc:
        log("FAIL", f"{method} {key} ({type(exc).__name__})")
        await _refuse(client_w, "502 Bad Gateway", key)
        return
    log("ALLOW", f"{method} {key}")
    path = url.path or "/"
    if url.query:
        path += f"?{url.query}"
    headers = [h for h in lines[1:] if h and h.split(b":", 1)[0].strip().lower()
               not in _HOP_HEADERS]
    up_w.write(b"\r\n".join([f"{method} {path} {version}".encode("latin-1"), *headers,
                             b"Connection: close", b"", b""]))
    await up_w.drain()
    await _tunnel(client_r, client_w, up_r, up_w)


async def main() -> None:
    if not ALLOW:
        sys.exit("egress proxy: ATOLL_EGRESS_ALLOW is empty — refusing to start")
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT, limit=_HEAD_LIMIT)
    log("START", f"port {LISTEN_PORT} allow={','.join(sorted(ALLOW))}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
