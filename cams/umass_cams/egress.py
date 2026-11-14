"""Send the frame grabber's traffic out of the Mac's own network instead of the VPN.

Every connection on the Mac mini leaves through a Tailscale exit node (a Mullvad VPN), and
YouTube answers VPN addresses with "Sign in to confirm you're not a bot" instead of the stream.
A socket bound to the Wi-Fi address goes out over the Wi-Fi (macOS scoped routing), the way the
Cloudflare tunnels use --edge-bind-address. So with UMASS_CAMS_EGRESS set, the grabber runs this
small HTTP CONNECT proxy on 127.0.0.1 while its browsers are open, and the proxy opens every
connection from that address. Only YouTube, Google and UMass Dining hosts are let through;
everything else on the Mac keeps using the VPN.

UMASS_CAMS_EGRESS: unset or "vpn" = no proxy; "physical" = the interface of the first default
route that isn't a tunnel (utunN); or an interface name such as "en1".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Collection

log = logging.getLogger("umass_cams.egress")

ALLOWED_DOMAINS = (
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
    "ytimg.com",
    "googlevideo.com",
    "ggpht.com",
    "google.com",
    "gstatic.com",
    "googleapis.com",
    "googleusercontent.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "umassdining.com",
)
HEAD_LIMIT = 16 * 1024
TIMEOUT_S = 15.0
ADDRESS_TTL_S = 60.0


def allowed_host(host: str, domains: Collection[str] = ALLOWED_DOMAINS) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def physical_interface(netstat_output: str) -> str | None:
    """The interface of the first IPv4 default route that isn't a tunnel, from `netstat -rn -f inet`."""
    for line in netstat_output.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[0] == "default" and not cols[-1].startswith("utun"):
            return cols[-1]
    return None


def interface_address(interface: str) -> str | None:
    out = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True, text=True, timeout=5)
    return out.stdout.strip() or None


def resolve_interface(mode: str) -> str | None:
    if mode != "physical":
        return mode
    out = subprocess.run(["/usr/sbin/netstat", "-rn", "-f", "inet"], capture_output=True, text=True, timeout=5)
    return physical_interface(out.stdout)


class SourceAddress:
    """The address to bind to, looked up again every minute (DHCP can change it)."""

    def __init__(self, mode: str, ttl_s: float = ADDRESS_TTL_S):
        self.mode = mode
        self.ttl_s = ttl_s
        self._value: str | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str | None:
        with self._lock:
            if self._value is None or time.monotonic() - self._at > self.ttl_s:
                interface = resolve_interface(self.mode)
                self._value = interface_address(interface) if interface else None
                self._at = time.monotonic()
            return self._value


def _reply(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            dst.close()  # the other direction then ends too


class BoundProxy:
    """HTTP CONNECT proxy on 127.0.0.1 whose upstream sockets are bound to `source()`."""

    def __init__(
        self,
        source: Callable[[], str | None],
        domains: Collection[str] = ALLOWED_DOMAINS,
        ports: Collection[int] = (443,),
    ):
        self.source = source
        self.domains = domains
        self.ports = ports
        self.port = 0
        self.denied: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> BoundProxy:
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            self._server = self._loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, name="cams-egress", daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise RuntimeError("the egress proxy did not start")
        return self

    def stop(self) -> None:
        if self._loop is None:
            return

        async def shutdown() -> None:
            self._server.close()
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)
        if self.denied:
            log.info("egress proxy refused: %s", ", ".join(sorted(self.denied)))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT_S)
        if len(head) > HEAD_LIMIT:
            return _reply(writer, "431 Request Header Fields Too Large")
        method, _, rest = head.split(b"\r\n", 1)[0].decode("latin-1").partition(" ")
        target = rest.rsplit(" ", 1)[0]
        if method.upper() != "CONNECT":
            return _reply(writer, "405 Method Not Allowed")
        host, _, port = target.rpartition(":")
        host = host.strip("[]")
        if not port.isdigit() or int(port) not in self.ports or not allowed_host(host, self.domains):
            self.denied.add(host.lower()[:100])
            return _reply(writer, "403 Forbidden")
        source = self.source()
        if source is None:
            log.warning("egress proxy: no address for the physical interface")
            return _reply(writer, "502 Bad Gateway")
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port), family=socket.AF_INET, local_addr=(source, 0)), TIMEOUT_S
            )
        except (OSError, TimeoutError):
            return _reply(writer, "502 Bad Gateway")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


def proxy_for(mode: str | None) -> BoundProxy | None:
    """A started proxy for UMASS_CAMS_EGRESS = `mode`, or None when traffic should take the default route."""
    if not mode or mode.lower() in ("vpn", "default", "off"):
        return None
    source = SourceAddress(mode)
    if source() is None:
        log.warning("UMASS_CAMS_EGRESS=%s: no address found for it; using the default route", mode)
        return None
    proxy = BoundProxy(source).start()
    log.info("egress: the grabber's browsers go out via %s, not the VPN", mode)
    return proxy
