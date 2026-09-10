"""Small dependency-free Prometheus text registry for MVP process metrics."""

from collections import defaultdict
from threading import Lock


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

    def render(self) -> str:
        with self._lock:
            return "".join(f"{name} {value}\n" for name, value in sorted(self._values.items()))


metrics = Metrics()
