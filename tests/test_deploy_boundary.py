"""Deployment boundary regression tests for the public multiuser surface."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = ROOT / "deploy" / "nginx" / "default.conf.template"
JENKINSFILE = ROOT / "Jenkinsfile"
WORKER_COMPOSE = ROOT / "docker-compose.worker.yml"
PRODUCTION_COMPOSE = ROOT / "deploy" / "prod" / "docker-compose.yml"
BACKEND_DOCKERFILE = ROOT / "deploy" / "backend" / "Dockerfile"
BOT_DOCKERFILE = ROOT / "deploy" / "bot" / "Dockerfile"
BACKEND_TEST_DOCKERFILE = ROOT / "deploy" / "ci" / "Dockerfile.test"
BOT_TEST_DOCKERFILE = ROOT / "deploy" / "ci" / "Dockerfile.bot-test"


def _pipeline_code() -> str:
    """Jenkinsfile без строк-комментариев.

    Подсчёты команд обязаны считать только исполняемый код: комментарий,
    упоминающий `trivy convert`, не должен ломать тест на количество сканов.
    """
    pipeline = JENKINSFILE.read_text(encoding="utf-8")
    return "\n".join(
        line for line in pipeline.splitlines() if not line.lstrip().startswith("//")
    )


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
        "= /api/v1/similar/by-audio",
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
    assert 'location ~ "^/share/(?<discocs_share_token>[A-Za-z0-9_-]{40,64})/?$"' in config
    assert "/api/v1/public/shares/$discocs_share_token/preview" in config
    assert 'map "$uri:$arg_preview" $discocs_robots_policy' in config
    assert "/preview:" in config
    assert "/cover:1" in config


def test_production_waits_for_public_frontend_health():
    config = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    frontend = config.split("  frontend:", 1)[1].split("\n  # Бот", 1)[0]
    assert "healthcheck:" in frontend
    assert "http://127.0.0.1:8080/health" in frontend


def test_local_worker_receives_service_token_from_environment():
    config = WORKER_COMPOSE.read_text(encoding="utf-8")

    assert "DISCOCS_SERVICE_TOKEN: ${DISCOCS_SERVICE_TOKEN:-}" in config


def test_local_worker_bounds_cpu_and_request_parallelism():
    config = WORKER_COMPOSE.read_text(encoding="utf-8")

    assert "DISCOCS_WORKER_CLAIM_BATCH_SIZE:-2" in config
    assert "DISCOCS_WORKER_MAX_INFLIGHT_TASKS:-2" in config
    assert "DISCOCS_WORKER_CPU_WORKERS:-2" in config
    assert "DISCOCS_WORKER_DOWNLOAD_CONCURRENCY:-1" in config
    assert "DISCOCS_WORKER_SUBMIT_BATCH_SIZE:-1" in config


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

    # Ключ инвалидации — сутки, а не коммит: на ${GIT_SHA} слой apt-upgrade
    # пересобирался в каждом билде и тянул за собой весь зависимый стек
    # (69.6с у backend, 50.1с у бота на билде #239), хотя системные пакеты
    # между коммитами обычно не меняются.
    assert "env.SECURITY_REFRESH = sh(script: 'date -u +%Y-%m-%d'" in pipeline
    assert "--build-arg SECURITY_REFRESH=${SECURITY_REFRESH}" in pipeline
    assert "--build-arg SECURITY_REFRESH=${GIT_SHA}" not in pipeline

    for dockerfile_path in (BACKEND_DOCKERFILE, BOT_DOCKERFILE):
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        assert "ARG SECURITY_REFRESH=local" in dockerfile
        assert "apt-get upgrade -y" in dockerfile


def test_python_images_install_dependencies_before_copying_sources():
    """Тяжёлый слой зависимостей не должен инвалидироваться правкой кода."""
    cases = (
        (BACKEND_DOCKERFILE, "COPY app ./app"),
        (BOT_DOCKERFILE, "COPY discocs_bot/bot ./bot"),
        (BACKEND_TEST_DOCKERFILE, "COPY app ./app"),
        (BOT_TEST_DOCKERFILE, "COPY discocs_bot/bot ./bot"),
    )

    for dockerfile_path, copy_sources in cases:
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        deps = dockerfile.index("uv sync --frozen --no-install-project")
        assert deps < dockerfile.index(copy_sources), dockerfile_path.name

    # umap/numpy ставятся мимо uv.lock, поэтому финальный sync обязан быть
    # --inexact: обычный sync приводит venv в точное соответствие локу и снёс
    # бы их (карта коллекции упала бы на ленивом импорте уже в проде).
    backend = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    assert "umap-learn" in backend
    assert backend.index("umap-learn") < backend.index("uv sync --frozen --inexact")


def test_trivy_blocks_only_fixable_high_or_critical_findings():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")
    # Гейт обязан быть настоящим сканом с родным --ignore-unfixed: у `trivy
    # convert` такого флага нет (билд #247: "unknown flag: --ignore-unfixed"),
    # а отфильтровать unfixed через Rego-политику — риск молча пропускающего
    # гейта.
    gate = (
        "aquasec/trivy image --skip-db-update --cache-backend memory "
        "--ignore-unfixed --severity HIGH,CRITICAL --exit-code 1"
    )
    assert gate in pipeline
    # HTML-вкладка публикуется до блокирующего гейта — иначе падение на
    # HIGH/CRITICAL оставило бы билд без отчёта, в котором видны находки.
    assert pipeline.index('reportName: "Trivy: ${svc}"') < pipeline.index(gate)


def test_reports_reuse_a_single_image_analysis():
    """Таблица и HTML не пересканируют образ, а переформатируют один JSON."""
    code = _pipeline_code()

    # Два разбора слоёв на ветку: отчётный скан и гейт. Раньше их было три —
    # с --cache-backend memory каждый начинал с нуля, и только backend стоил
    # 30.6+24.0+22.7с на билде #239.
    assert code.count("trivy image --skip-db-update") == 2
    assert code.count("--format json -o /report.json") == 1

    # Таблица в консоль и HTML-вкладка — два `convert` над этим JSON.
    assert code.count("trivy convert") == 2


def test_independent_checks_run_in_one_parallel_stage():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    checks = pipeline.index("stage('Checks')")
    build = pipeline.index("stage('Build & Push')")
    assert checks < build

    # Тесты трёх частей и скан зависимостей не связаны друг с другом: если их
    # снова растащить по последовательным стадиям, стадия начнёт стоить сумму,
    # а не максимум.
    assert pipeline.index("parallel {", checks) < build
    for branch in (
        "stage('Tests: backend')",
        "stage('Tests: bot')",
        "stage('Tests: ui')",
        "stage('Deps CVE')",
    ):
        assert checks < pipeline.index(branch) < build

    # trivy fs читает только lock-файлы — ждать сборки образов ему незачем.
    assert checks < pipeline.index("deploy/ci/Dockerfile.trivy-fs") < build

    # failFast обрывал бы соседние ветки на первом падении, и junit/coverage
    # почти доехавших наборов снова терялись бы (см. билд #53).
    assert "failFast" not in pipeline[checks:build]

    # Каждая ветка публикует свой junit сама — общий `junit 'junit-*.xml'`
    # после параллельной стадии подхватил бы отчёты только полностью успешного прогона.
    for report in ("junit-backend.xml", "junit-bot.xml", "junit-ui.xml"):
        assert f"junit '{report}'" in pipeline


def test_sonar_runs_alongside_the_image_scan():
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    build = pipeline.index("stage('Build & Push')")
    analyze = pipeline.index("stage('Analyze & Scan')")

    # Сборка идёт отдельной стадией и строго после проверок: Trivy нужны
    # готовые образы. Docker Login — внутри неё.
    assert pipeline.index("stage('Checks')") < build < analyze
    assert build < pipeline.index("stage('Docker Login')") < analyze

    # Sonar'у нужен только coverage из Checks, Trivy — образы из Build & Push,
    # друг от друга они не зависят. Раньше Sonar шёл параллельно сборке, но
    # после её ускорения (131 -> 22.8с, билд #248) прятать его стало не под чем.
    assert pipeline.index("parallel {", analyze) < pipeline.index("stage('Sonar')")
    assert analyze < pipeline.index("stage('Security Scan')")


def test_image_scans_share_one_vulnerability_db_refresh():
    code = _pipeline_code()

    warmup = code.index("aquasec/trivy image --download-db-only")
    fan_out = code.index("['backend', 'frontend', 'bot'].collectEntries")

    # Три ветки на общем volume trivy-db-cache качали бы и распаковывали БД
    # конкурентно в один каталог, поэтому обновление — один раз до fan-out,
    # а сам скан идёт с --skip-db-update.
    assert warmup < fan_out
    assert code.count("--download-db-only") == 1
    assert code.count("--skip-db-update") == 2

    # Ветки делят read-only БД уязвимостей, но изменяемый layer scan cache
    # обязан быть локальным для процесса: filesystem-кэш держит BoltDB-лок и
    # не обслуживает параллельные процессы Trivy ("Failed to acquire cache or
    # database lock", билд #238). Оба скана ветки — с memory-бэкендом.
    assert code.count("--cache-backend memory") == 2
