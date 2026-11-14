import socket
import threading

import pytest
from umass_cams import egress

NETSTAT = """Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            link#22            UCSIg               utun4
default            172.31.144.1       UGScIg                en1
127                127.0.0.1          UCS                   lo0
"""


def test_physical_interface_skips_the_tunnel():
    assert egress.physical_interface(NETSTAT) == "en1"
    assert egress.physical_interface("default link#22 UCSIg utun4\n") is None


def test_only_youtube_google_and_dining_hosts_are_allowed():
    for host in (
        "www.youtube.com",
        "rr3---sn-abc.googlevideo.com",
        "i.ytimg.com",
        "youtube.com",
        "www.umassdining.com.",
    ):
        assert egress.allowed_host(host)
    for host in ("example.com", "notyoutube.com", "youtube.com.evil.example", "googlevideo.co"):
        assert not egress.allowed_host(host)


def test_off_means_no_proxy():
    for mode in (None, "", "vpn", "VPN", "off"):
        assert egress.proxy_for(mode) is None


@pytest.fixture
def echo_port():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    seen = []

    def serve():
        while True:
            try:
                conn, peer = server.accept()
            except OSError:
                return
            seen.append(peer[0])
            with conn:
                while data := conn.recv(1024):
                    conn.sendall(data)

    threading.Thread(target=serve, daemon=True).start()
    yield server.getsockname()[1], seen
    server.close()


def _ask(proxy, line):
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
        s.sendall(line.encode() + b"\r\nHost: x\r\n\r\n")
        reply = s.recv(1024)
        if reply.startswith(b"HTTP/1.1 200"):
            s.sendall(b"ping")
            reply += s.recv(1024)
        return reply


def test_proxy_tunnels_from_the_bound_address_and_refuses_the_rest(echo_port):
    port, seen = echo_port
    proxy = egress.BoundProxy(lambda: "127.0.0.1", domains=("localhost",), ports=(port,)).start()
    try:
        assert _ask(proxy, f"CONNECT localhost:{port} HTTP/1.1") == b"HTTP/1.1 200 Connection Established\r\n\r\nping"
        assert seen == ["127.0.0.1"]
        assert _ask(proxy, f"CONNECT example.com:{port} HTTP/1.1").startswith(b"HTTP/1.1 403")
        assert _ask(proxy, "CONNECT localhost:80 HTTP/1.1").startswith(b"HTTP/1.1 403")  # port not allowed
        assert _ask(proxy, "GET http://localhost/ HTTP/1.1").startswith(b"HTTP/1.1 405")
    finally:
        proxy.stop()
    assert proxy.denied == {"example.com", "localhost"}


def test_proxy_answers_502_without_an_address(echo_port):
    port, _ = echo_port
    proxy = egress.BoundProxy(lambda: None, domains=("localhost",), ports=(port,)).start()
    try:
        assert _ask(proxy, f"CONNECT localhost:{port} HTTP/1.1").startswith(b"HTTP/1.1 502")
    finally:
        proxy.stop()
