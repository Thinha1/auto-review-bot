import httpx
import pytest

from app.metrics import Metrics, MetricsServer


def test_observe_renders_prometheus_count_and_sum_series() -> None:
    registry = Metrics()

    registry.observe("request_duration_seconds", 1.25)
    registry.observe("request_duration_seconds", 0.75)

    assert registry.render() == (
        "request_duration_seconds_count 2.0\nrequest_duration_seconds_sum 2.0\n"
    )


def test_observe_rejects_negative_durations() -> None:
    registry = Metrics()

    with pytest.raises(ValueError, match="non-negative"):
        registry.observe("request_duration_seconds", -0.01)


def test_worker_metrics_server_exposes_registry_on_scrape_endpoint() -> None:
    registry = Metrics()
    registry.increment("worker_jobs_total", 2)
    server = MetricsServer(registry, "127.0.0.1", 0)
    server.start()

    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{server.port}/metrics")
            missing = client.get(f"http://127.0.0.1:{server.port}/missing")
    finally:
        server.close()

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert response.text == "worker_jobs_total 2.0\n"
    assert missing.status_code == 404
