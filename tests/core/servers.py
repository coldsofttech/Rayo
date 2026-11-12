"""Small real HTTP servers for tests, so the network code is exercised over actual sockets."""

import datetime
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass
class Seen:
    """A request as the server received it."""

    method: str
    target: str
    path: str
    headers: dict[str, str]


@dataclass
class Reply:
    """What the server answers. ``truncate`` promises twice the body, sends it, and hangs up."""

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    truncate: bool = False


Route = Callable[[Seen], Reply]


class LocalServer:
    """A threaded HTTP/1.1 server with keep-alive, routes by path, and a request log."""

    def __init__(
        self, routes: Mapping[str, Route] | None = None, *, tls: tuple[Path, Path] | None = None
    ) -> None:
        self.routes = dict(routes or {})
        self.requests: list[Seen] = []
        self.connections = 0
        self._tls = tls
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                outer.connections += 1

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                seen = Seen(
                    "GET",
                    self.path,
                    path,
                    {name.lower(): value for name, value in self.headers.items()},
                )
                outer.requests.append(seen)
                route = outer.routes.get(path)
                reply = route(seen) if route else Reply(404, b"not found")
                if reply.delay:
                    time.sleep(reply.delay)
                self.send_response(reply.status)
                length = len(reply.body) * (2 if reply.truncate else 1)
                self.send_header("Content-Length", str(length))
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(reply.body)
                if reply.truncate:
                    self.wfile.flush()
                    self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                return  # keep test output quiet

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        if tls is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(tls[0]), str(tls[1]))
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"{'https' if self._tls else 'http'}://127.0.0.1:{self.port}"

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def make_certificate(directory: Path) -> tuple[Path, Path]:
    """Write a self-signed certificate (valid for 127.0.0.1 and localhost) and its key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path
