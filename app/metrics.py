"""Small dependency-free Prometheus text registry for MVP process metrics."""

from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import cast


class Metrics:
    def __init__(self) -> None:
        self._values: dict[str, float] = defaultdict(float)
        self._lock = Lock()

    def increment(self, name: str, value: float = 1) -> None:
        with self._lock:
            self._values[name] += value

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._values[name] = value

    def observe(self, name: str, value: float) -> None:
        """Record a dependency-free Prometheus summary as count and sum series."""
        if value < 0:
            raise ValueError("Observed metric values must be non-negative")
        with self._lock:
            self._values[f"{name}_count"] += 1
            self._values[f"{name}_sum"] += value

    def render(self) -> str:
        with self._lock:
            return "".join(f"{name} {value}\n" for name, value in sorted(self._values.items()))


metrics = Metrics()


class _MetricsHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], registry: Metrics) -> None:
        self.registry = registry
        super().__init__(address, _MetricsHandler)


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        registry = cast(_MetricsHTTPServer, self.server).registry
        payload = registry.render().encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class MetricsServer:
    """Lifecycle wrapper for the worker's scrape-only HTTP endpoint."""

    def __init__(self, registry: Metrics, host: str, port: int) -> None:
        self._server = _MetricsHTTPServer((host, port), registry)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="worker-metrics-server",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
