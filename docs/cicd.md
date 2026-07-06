# CI/CD

Автосборка и деплой на домашний сервер `192.168.1.41`. Вся инфраструктура (Jenkins,
Nexus, Gitea, целевой хост) — одна машина `.41`.

## Поток

```
push в Gitea ──webhook──> Jenkins
   └─ Test         три отдельных образа, каждый BuildKit + --mount=type=cache:
   │                 backend — pytest -n auto (deploy/ci/Dockerfile.test)
   │                 бот     — pytest (deploy/ci/Dockerfile.bot-test)
   │                 фронт   — vitest run --coverage (deploy/ci/Dockerfile.ui-test)
   │                 coverage- и junit-отчёты каждого достаются docker cp'ом,
   │                 junit публикуется через встроенный шаг `junit` (Test Result Trend)
   └─ Sonar        sonar-scanner — отчёт в SonarQube, без Quality Gate (билд не блокируется)
   └─ Build&Push   сборка backend/frontend/bot → Nexus (docker-dev @ :5000)
   │                 теги:  :<git-sha>  (всегда)   +  :latest  (только с main)
   └─ Security Scan Trivy — CVE в зависимостях + в собранных образах, report-only
   │                 находки образов также публикуются как HTML-вкладки на билде
   │                 (Trivy: backend/frontend/bot, через publishHTML)
   └─ Deploy        [post/success, только main] по SSH на TARGET_SERVER:
                       scp compose в TARGET_DIR → docker compose pull && up -d --force-recreate
                       (.env на хосте CI не трогает — заводится и правится вручную)
```

Файлы:

| Файл | Роль |
|---|---|
| `Jenkinsfile` | описание пайплайна |
| `deploy/ci/Dockerfile.test` | образ для прогона тестов backend (`app`) в CI |
| `deploy/ci/Dockerfile.bot-test` | образ для прогона тестов бота (`discocs_bot`) в CI |
| `deploy/ci/Dockerfile.ui-test` | образ для прогона тестов фронтенда (`ui`, vitest) в CI |
| `deploy/ci/Dockerfile.trivy-fs` | образ для Trivy fs-скана зависимостей в CI |
| `uv.lock` / `discocs_bot/uv.lock` | закреплённые версии Python-зависимостей (backend / бот) |
| `deploy/prod/docker-compose.yml` | прод-стек: тянет готовые образы из Nexus |
| `deploy/prod/.env.example` | шаблон прод-окружения (реальный `.env` — секрет, не в гит) |

## Реестр образов

- Nexus UI/API: `http://192.168.1.41:8081`, hosted-репо `docker-dev`.
- Docker-эндпоинт (push/pull): **`192.168.1.41:5000`**.
- Имена образов: `192.168.1.41:5000/discocs/{backend,frontend,bot}`.
- Пул анонимный, push — под `tank_nexus_user_pass` (Username/Password, общий для всех джоб).
- Репо работает по HTTP → на хосте нужен `insecure-registries` (уже настроено):

  ```json
  // /etc/docker/daemon.json
  { "insecure-registries": ["192.168.1.41:5000"] }
  ```

  В Nexus у hosted-репо должно быть включено **Allow redeploy** — иначе `:latest` не перезапишется.

## Разовая настройка

### 1. Credentials в Jenkins

| ID | Тип | Назначение |
|---|---|---|
| `tank_nexus_user_pass` | Username/Password | логин в Nexus для push *(уже есть, общий для всех джоб)* |
| `HS_SSH_KEY` | SSH Username with private key | деплой-ключ на `TARGET_SERVER` *(уже есть)* |
| `sonar_token` | Secret text | токен для `sonar-scanner` *(уже есть, общий для всех джоб)* |

`.env` в CI больше не участвует: заполни `deploy/prod/.env.example` реальными значениями
(минимум `DISCOCS_MUSIC_DIR` и `DISCOCS_STATE_DIR`) и один раз положи как `TARGET_DIR/.env`
прямо на хосте (`192.168.1.41`). Дальше CI его не трогает — правишь на месте, когда нужно
поменять переменную (профиль бота, токен, URL Navidrome и т.п.), без пересборки/редеплоя.

### 2. Агент Jenkins (контейнер)

- смонтирован `/var/run/docker.sock` (нужен только для сборки/пуша образов);
- внутри есть `docker` CLI;
- деплой не требует docker socket в агенте — идёт по SSH на `TARGET_SERVER`
  (см. env-блок `Jenkinsfile`: `TARGET_SERVER`, `TARGET_USER`, `TARGET_PORT`, `TARGET_DIR`);
  на целевом хосте должен быть настроен `insecure-registries` для `192.168.1.41:5000` (см. выше)
  и создана директория `TARGET_DIR` (`/home/infected2202/docker/discocs`).

### 3. Job + триггер (Gitea)

1. Установить плагин **Gitea** в Jenkins.
2. Создать job:
   - **Multibranch Pipeline** (рекомендуется — строит все ветки/PR, деплоит только `main`), или
   - **Pipeline** → *Pipeline script from SCM* → Git `http://192.168.1.41:3064/HS/discocs`, ветка `main`, Script Path `Jenkinsfile`.
3. Вебхук в Gitea: репо → Settings → Webhooks → тип **Gitea**, URL
   `http://192.168.1.41:8077/gitea-webhook/post`.

### 4. Git remotes

`origin` — GitHub. Чтобы код попадал ещё и в Gitea (и триггерил сборку), добавь
Gitea вторым push-URL к `origin` — один `git push` уедет в оба:

```bash
git remote set-url --add --push origin git@github.com:Infected2202/Discocs.git
git remote set-url --add --push origin http://192.168.1.41:3064/HS/discocs.git
```

Проверить: `git remote -v` (должно быть два `(push)`).

## Деплой и откат

Деплой автоматический на успешной сборке `main`: Jenkins по SSH заливает
`deploy/prod/docker-compose.yml` в `TARGET_DIR` на `TARGET_SERVER` (`.env` там уже лежит,
CI его не перезаписывает) и там же гоняет `pull` + `up -d --force-recreate --wait`. Вручную на хосте:

```bash
cd /home/infected2202/docker/discocs   # TARGET_DIR, там же лежит .env
docker compose -p discocs --env-file .env pull
docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans --wait --wait-timeout 120
```

`--wait` — это и есть post-deploy smoke-check: блокирует команду, пока Docker
healthcheck'и сервисов (уже описаны в `deploy/prod/docker-compose.yml`, у backend —
`curl`/`urlopen` на `/health`) не подтвердят `healthy`, и возвращает ненулевой
код, если за `--wait-timeout` (120с) это не произошло — тогда SSH-шаг в
Jenkins падает и стадия `Deploy` считается зафейленной. До этого `up -d`
считался успехом сразу после старта контейнера, падение через несколько
секунд после деплоя было не видно в CI. Требует Docker Compose ≥ v2.17 на
`TARGET_SERVER` — проверить: `docker compose version`.

**Откат** на конкретный билд — поставь его sha в `.env` и повтори up:

```bash
TAG=a1b2c3d   # значение из тега образа / номера коммита
# правишь TAG в TARGET_DIR/.env на сервере, затем:
docker compose -p discocs --env-file .env up -d --force-recreate
```

## Бот

Стартует только при `COMPOSE_PROFILES=bot` в `.env` (плюс заполненный `BOT_TOKEN`
и прочие переменные). Пока не готов — просто не выставляй профиль, backend и
frontend поднимаются без него. Образ бота при этом всё равно собирается и пушится.

Сеть площадки режет боту прямой исходящий трафик до Telegram API, поэтому вместе
с ботом (тот же профиль `bot`) поднимается сайдкар `awg` — весь сетевой стек бота
(`network_mode: service:awg`) идёт через amneziawg-туннель. Конфиг туннеля и
`start-awg.sh` — секреты, кладутся руками в `${DISCOCS_STATE_DIR}/discocs_awg/`
(не коммитятся, см. `deploy/prod/.env.example`). `backend` по-прежнему резолвится
по имени, т.к. `awg` сидит в той же compose-сети.

## Порты на хосте

| Порт | Сервис | Назначение |
|---|---|---|
| `${DISCOCS_HTTP_PORT:-80}` | frontend | новый UI (основной вход) |
| `8711` | backend | старая админка (`/admin`) + API напрямую, для внутреннего стенда опубликован |

`backend` порт публикуется намеренно (`ports: ["8711:7752"]`) — площадка внутренняя,
доступа снаружи LAN нет, поэтому нет смысла прятать API за одним только nginx.

## SonarQube

Стадия `Sonar` в `Jenkinsfile` — после `Test`, перед `Build & Push`. Гоняет
`sonar-scanner` (см. `sonar-project.properties`) против сервера
`http://192.168.1.41:9077` под токеном `sonar_token`. Это только отчёт —
`waitForQualityGate` не используется, результат анализа не может завалить
билд, смотреть метрики (code smells, дублирование, security hotspots) —
в вебке Sonar по `projectKey=discocs`. Если качество анализа окажется
полезным, можно добавить `waitForQualityGate` (потребует настроить вебхук
SonarQube → Jenkins).

Покрыты все части кодовой базы: `sonar.sources=app,discocs_bot/bot,ui/src,deploy`
(бэкенд, бот, фронтенд, CI/Dockerfile'ы), `sonar.tests` — соответствующие каталоги
тестов. `deploy` добавлен ради встроенных в Community Build анализаторов **Secrets**
и **Docker/IaC** — они бесплатны и не требуют отдельного софта (gitleaks/hadolint
не нужны), просто раньше `deploy` не попадал в область сканирования.

Для фронтенда отдельный отчёт линтера не подключён: `ui/eslint.config.js`
использует только рекомендованные пресеты (eslint/typescript-eslint/react-hooks),
встроенный TS/JS-анализатор Sonar покрывает то же самое сам — подключать
`sonar.eslint.reportPaths` стоит, только если появятся кастомные правила.

`sonar-scanner` на Jenkins-агенте — версия `8.0.1.6346` (`/opt/sonar-scanner`,
симлинк `/usr/local/bin/sonar-scanner`), несёт собственную JRE 21. Это важно
держать в актуальном состоянии — при апгрейде SonarQube-сервера сканер может
перестать понимать протокол/движок нового сервера (`UnsupportedClassVersionError`)
или новые опции `tsconfig.json` фронтенда (`Unknown compiler option` от
TS/JS-анализатора) — обновлять сканер вместе с сервером, версия и ссылка на
скачивание — https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/scanners/sonarscanner.

Coverage подключён для всех трёх частей — стадия `Test` собирает три образа
и достаёт из каждого свой отчёт через `docker cp` (агент — docker-outside-of-docker,
bind-mount воркспейса недоступен хостовому демону):

- backend (`discocs-test`) — `pytest --cov=app --cov-report=xml` → `coverage.xml`
- бот (`discocs-bot-test`) — `pytest --cov=bot --cov-report=xml` → `bot-coverage.xml`
- фронт (`discocs-ui-test`) — `vitest run --coverage` (provider v8, reporter lcov) → `ui/coverage/lcov.info`

Оба Python-отчёта подхватываются `sonar.python.coverage.reportPaths=coverage.xml,bot-coverage.xml`,
JS-отчёт — `sonar.javascript.lcov.reportPaths=ui/coverage/lcov.info`.

Раньше тесты бота и фронтенда вообще не запускались в CI (только числились
в `sonar.tests`), хотя лежали в репозитории — это был реальный пробел, а не
просто нехватка coverage-метрики: `ui/package.json`'s `test` был на `node --test`
вместо `vitest` (4 файла в `ui/tests` импортировали `test` из `node:test`,
из-за чего vitest их не подхватывал), а `discocs_bot/pyproject.toml` не
объявлял `pytest` даже как dev-зависимость.

### JUnit-отчёты (Test Result Trend)

Каждый из трёх тестовых прогонов пишет ещё и JUnit XML — `pytest --junitxml=`
(backend/бот) и `vitest`'ный встроенный `junit`-репортер (фронт, настроен в
`ui/vite.config.ts`: `reporters: ["default", "junit"]`). Все три (`junit-backend.xml`,
`junit-bot.xml`, `junit-ui.xml`) достаются `docker cp`'ом и публикуются одним
встроенным шагом `junit 'junit-*.xml'` (ядро Jenkins, отдельный плагин не нужен) —
даёт нативный **Test Result Trend** на странице джобы и список конкретно
упавших тестов, без грепа консоли.

Важно: скрипт извлечения coverage/junit специально не использует `set -e` —
раньше (см. историю, билд #53) первый же упавший набор тестов обрывал весь
скрипт до `docker cp`, и при реальном падении тестов не оставалось вообще
никакого отчёта (ни coverage, ни junit) — приходилось лезть в консоль руками.
Теперь код выхода каждого `docker start -a` трекается вручную (`FAILED`),
`docker cp` всегда выполняется, а стадия `Test` фейлится по итоговому `exit
$FAILED` уже после того, как все отчёты извлечены; `junit` публикуется в
`post { always { ... } }` стадии, а не только при успехе.

## Python-зависимости (uv)

Backend (`app`) и бот (`discocs_bot`) ставятся через `uv sync --frozen` вместо
`pip install -e .` — во всех четырёх Dockerfile'ах, что их используют
(`deploy/backend/Dockerfile`, `deploy/bot/Dockerfile`, `deploy/ci/Dockerfile.test`,
`deploy/ci/Dockerfile.bot-test`). `--frozen` ставит строго из `uv.lock` /
`discocs_bot/uv.lock`, без пересчёта резолвера — то же самое закрепление
версий, что видит `trivy fs` (см. ниже). Бинарник `uv` берётся многостадийным
`COPY --from=ghcr.io/astral-sh/uv:latest`, отдельно ставить не нужно.

Lock-файлы обновляются вручную командой `uv lock` (в корне — для `app`,
в `discocs_bot/` — для бота) при изменении зависимостей в `pyproject.toml`;
CI их не регенерирует и не проверяет свежесть (можно добавить `uv lock --check`
отдельной стадией, если резолвинг начнёт расползаться незамеченным).

## Очистка образов на агенте

`post/always` в `Jenkinsfile` гоняет `docker image prune -a -f --filter "until=48h"`.
Агент — постоянный LXC-контейнер, а не эфемерный воркер: без этого шага тестовые/scan-образы
(`discocs-test`, `discocs-bot-test`, `discocs-ui-test`, `discocs-trivy-fs`) и прод-теги
по `GIT_SHA` копились бы на диске бесконечно (раньше чистка была только на
`TARGET_SERVER`, агента не касалась). `until=48h` — компромисс: свежие билды
ещё можно подебажить руками, старое чистится само.

## Trivy (security scan)

Стадия `Security Scan` в `Jenkinsfile` — после `Build & Push`, report-only
(`--exit-code 0`, билд никогда не валится). Один инструмент закрывает то, что
Sonar Community не умеет бесплатно (SCA — уязвимости в зависимостях):

- `trivy fs` — CVE в `pnpm-lock.yaml` (фронтенд) и в `uv.lock`/`discocs_bot/uv.lock`
  (backend/бот — точный резолвинг версий, см. ниже);
- `trivy image` — CVE в уже собранных образах `backend`/`frontend`/`bot`, через
  `docker.sock` (не bind-mount воркспейса — тут монтируется только сокет, это работает).

БД уязвимостей Trivy (~150+ МБ) кэшируется, а не качается заново на каждый билд:
для `trivy fs` — через `--mount=type=cache` прямо в `RUN` сборки `Dockerfile.trivy-fs`
(скан идёт во время `docker build`, не отдельным `docker run` после — так кэш
BuildKit реально переиспользуется между билдами); для `trivy image` — через
именованный volume `trivy-db-cache` (не трогается `docker image prune`,
живёт отдельно от образов).

Секреты и Dockerfile/IaC-анализ Trivy не делает — это уже покрыто встроенными
анализаторами Sonar (см. выше), дублировать не стали.

### HTML-отчёт

Раньше находки `trivy image` были видны только в консоли билда — приходилось
искать глазами по логу. Стадия `Security Scan` теперь дополнительно гоняет тот
же скан ещё раз с `--format template --template "@contrib/html.tpl"` (шаблон
зашит в образ `aquasec/trivy`, ничего скачивать не нужно) и публикует три
отдельные вкладки на странице билда через `publishHTML` (плагин **HTML
Publisher**, установлен на Jenkins вручную, в репозитории не отражается):
**Trivy: backend**, **Trivy: frontend**, **Trivy: bot**. Двойной скан на
образ (обычный + HTML) не удваивает время заметно — БД уязвимостей уже в
кэше (`trivy-db-cache`), пересканируется только сам образ.

## Траблшутинг

| Симптом | Причина / фикс |
|---|---|
| `http: server gave HTTP response to HTTPS client` | нет `insecure-registries` для `192.168.1.41:5000` на хосте |
| push `:latest` не перезаписывается | в Nexus hosted-репо выключен *Allow redeploy* |
| `docker: command not found` в пайплайне | в Jenkins-контейнер не проброшен сокет / нет docker CLI |
| бинд-маунт музыки падает на `up` | `DISCOCS_MUSIC_DIR` в `.env` указывает на несуществующий путь |
