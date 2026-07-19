"""Deployment boundary regression tests for the public multiuser surface."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = ROOT / "deploy" / "nginx" / "default.conf.template"
JENKINSFILE = ROOT / "Jenkinsfile"
WORKER_COMPOSE = ROOT / "docker-compose.worker.yml"
PRODUCTION_COMPOSE = ROOT / "deploy" / "prod" / "docker-compose.yml"
BACKEND_DOCKERFILE = ROOT / "deploy" / "backend" / "Dockerfile"
BOT_DOCKERFILE = ROOT / "deploy" / "bot" / "Dockerfile"


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


def test_public_nginx_preserves_external_origin_for_backend_csrf_checks():
    config = NGINX_TEMPLATE.read_text(encoding="utf-8")
    proxy_count = config.count("proxy_pass ")

    # $host drops a non-default port (for example the production :5173),
    # which makes a genuinely same-origin browser POST look cross-origin to
    # the backend. An edge TLS proxy also reaches this container over HTTP, so
    # replacing its X-Forwarded-Proto with $scheme loses the public HTTPS
    # origin and prevents Secure session cookies from being set.
    assert "proxy_set_header Host $host;" not in config
    assert config.count("proxy_set_header Host $http_host;") == proxy_count
    assert config.count("proxy_set_header X-Forwarded-Host $http_host;") == proxy_count
    assert "map $http_x_forwarded_proto $discocs_public_scheme" in config
    assert "default $scheme;" in config
    assert "http http;" in config
    assert "https https;" in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" not in config
    assert (
        config.count(
            "proxy_set_header X-Forwarded-Proto $discocs_public_scheme;"
        )
        == proxy_count
    )


def test_public_nginx_masks_share_tokens_and_hardens_share_pages():
    config = NGINX_TEMPLATE.read_text(encoding="utf-8")

    assert "/share/[redacted]" in config
    assert "/api/v1/public/shares/[redacted]" in config
    assert "access_log /var/log/nginx/access.log discocs_masked;" in config
    assert "~^/share/[A-Za-z0-9_-]+ no-referrer;" in config
    assert '"noindex, nofollow, noarchive"' in config
    assert 'proxy_set_header X-Discocs-Service-Token "";' in config


def test_local_worker_receives_service_token_from_environment():
    config = WORKER_COMPOSE.read_text(encoding="utf-8")

    assert "DISCOCS_SERVICE_TOKEN: ${DISCOCS_SERVICE_TOKEN:-}" in config


def test_production_sharing_is_enabled_unless_explicitly_disabled():
    config = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    assert "DISCOCS_SHARING_ENABLED: ${DISCOCS_SHARING_ENABLED:-true}" in config


def test_automated_deploy_ignores_stale_rollback_tag():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    assert "TAG=latest docker compose -p discocs --env-file .env pull" in pipeline
    assert (
        "TAG=latest docker compose -p discocs --env-file .env up -d --force-recreate"
    ) in pipeline


def test_production_images_refresh_system_security_updates():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")
    assert "docker build --pull ${refreshArg}" in pipeline
    assert "--build-arg SECURITY_REFRESH=${GIT_SHA}" in pipeline

    for dockerfile_path in (BACKEND_DOCKERFILE, BOT_DOCKERFILE):
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        assert "ARG SECURITY_REFRESH=local" in dockerfile
        assert "apt-get upgrade -y" in dockerfile


def test_trivy_blocks_only_fixable_high_or_critical_findings():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")
    assert "aquasec/trivy image --ignore-unfixed" in pipeline
    assert "--severity HIGH,CRITICAL --exit-code 1" in pipeline
    assert pipeline.index("reportName: 'Trivy: bot'") < pipeline.index(
        "aquasec/trivy image --ignore-unfixed"
    )
