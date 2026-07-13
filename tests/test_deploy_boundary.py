"""Deployment boundary regression tests for the public multiuser surface."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = ROOT / "deploy" / "nginx" / "default.conf.template"


def _location_body(config: str, selector: str) -> str:
    marker = f"location {selector} {{"
    start = config.index(marker)
    return config[start : config.index("}", start) + 1]


def test_public_nginx_denies_operational_surfaces():
    config = NGINX_TEMPLATE.read_text(encoding="utf-8")
    denied = (
        "^~ /admin",
        "^~ /api/map",
        "^~ /api/v1/workers",
        "^~ /api/v1/settings",
        "^~ /api/v1/jobs",
        "= /api/v1/mixes/settings",
        "= /api/v1/instant-mix/settings",
        "= /api/v1/instant-mix/requests",
        "= /api/v1/albums/settings",
        "= /api/v1/navidrome/ping",
        "= /api/v1/navidrome/plugin-event",
        "= /api/v1/models/download-head-pack",
        "= /api/v1/index/rebuild",
    )

    for selector in denied:
        assert "return 404;" in _location_body(config, selector)

    assert "location ~ ^/(api|health)(/|$)" in config
    assert "location ~ ^/(api|health|admin)(/|$)" not in config


def test_public_nginx_keeps_personal_maintenance_routes():
    config = NGINX_TEMPLATE.read_text(encoding="utf-8")
    personal_routes = (
        "= /api/v1/jobs/albums-for-you",
        "= /api/v1/jobs/albums-for-you/status",
        "= /api/v1/jobs/flow-profile",
        "= /api/v1/jobs/flow-profile/status",
    )

    for selector in personal_routes:
        assert "proxy_pass" in _location_body(config, selector)
