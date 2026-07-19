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


def test_public_nginx_serves_universal_link_preview_metadata_to_crawlers():
    config = NGINX_TEMPLATE.read_text(encoding="utf-8")

    assert "map $http_user_agent $discocs_link_preview_crawler" in config
    assert "TelegramBot" in config
    assert "Valve/Steam\\ HTTP\\ Client" in config
    assert "Discordbot" in config
    assert "/api/v1/public/shares/$discocs_share_token/preview" in config


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
    gate = (
        "aquasec/trivy image --skip-db-update --cache-backend memory "
        "--ignore-unfixed"
    )
    assert gate in pipeline
    assert "--severity HIGH,CRITICAL --exit-code 1" in pipeline
    # HTML-вкладка публикуется до блокирующего гейта — иначе падение на
    # HIGH/CRITICAL оставило бы билд без отчёта, в котором видны находки.
    assert pipeline.index('reportName: "Trivy: ${svc}"') < pipeline.index(gate)


def test_independent_checks_run_in_one_parallel_stage():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    checks = pipeline.index("stage('Checks')")
    analyze = pipeline.index("stage('Analyze & Build')")
    scan = pipeline.index("stage('Security Scan')")
    assert checks < analyze < scan

    # Тесты трёх частей и скан зависимостей не связаны друг с другом: если их
    # снова растащить по последовательным стадиям, стадия начнёт стоить сумму,
    # а не максимум.
    assert pipeline.index("parallel {", checks) < analyze
    for branch in (
        "stage('Tests: backend')",
        "stage('Tests: bot')",
        "stage('Tests: ui')",
        "stage('Deps CVE')",
    ):
        assert checks < pipeline.index(branch) < analyze

    # trivy fs читает только lock-файлы — ждать сборки образов ему незачем.
    assert checks < pipeline.index("deploy/ci/Dockerfile.trivy-fs") < analyze

    # failFast обрывал бы соседние ветки на первом падении, и junit/coverage
    # почти доехавших наборов снова терялись бы (см. билд #53).
    assert "failFast" not in pipeline[checks:analyze]

    # Каждая ветка публикует свой junit сама — общий `junit 'junit-*.xml'`
    # после параллельной стадии подхватил бы отчёты только полностью успешного прогона.
    for report in ("junit-backend.xml", "junit-bot.xml", "junit-ui.xml"):
        assert f"junit '{report}'" in pipeline


def test_sonar_runs_alongside_image_build():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    analyze = pipeline.index("stage('Analyze & Build')")
    scan = pipeline.index("stage('Security Scan')")

    # Сканеру нужны только coverage-отчёты из Checks, сборке — только исходники.
    assert pipeline.index("parallel {", analyze) < scan
    for branch in ("stage('Sonar')", "stage('Build & Push')", "stage('Docker Login')"):
        assert analyze < pipeline.index(branch) < scan


def test_image_scans_share_one_vulnerability_db_refresh():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    warmup = pipeline.index("aquasec/trivy image --download-db-only")
    fan_out = pipeline.index("['backend', 'frontend', 'bot'].collectEntries")

    # Три ветки на общем volume trivy-db-cache качали бы и распаковывали БД
    # конкурентно в один каталог, поэтому обновление — один раз до fan-out,
    # а сами сканы идут с --skip-db-update.
    assert warmup < fan_out
    assert pipeline.count("--download-db-only") == 1
    assert pipeline.count("aquasec/trivy image --skip-db-update") == 3
    # Image branches share the read-only vulnerability DB, but their mutable
    # layer scan cache must be process-local: filesystem cache uses a BoltDB
    # lock and cannot serve parallel Trivy processes.
    assert pipeline.count("--cache-backend memory") == 3
