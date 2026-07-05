# CI/CD

Автосборка и деплой на домашний сервер `192.168.1.41`. Вся инфраструктура (Jenkins,
Nexus, Gitea, целевой хост) — одна машина `.41`.

## Поток

```
push в Gitea ──webhook──> Jenkins
   └─ Test         прогон pytest в контейнере python:3.11 (deploy/ci/Dockerfile.test)
   └─ Build&Push   сборка backend/frontend/bot → Nexus (docker-dev @ :5000)
   │                 теги:  :<git-sha>  (всегда)   +  :latest  (только с main)
   └─ Deploy        [post/success, только main] по SSH на TARGET_SERVER:
                       scp compose+.env в TARGET_DIR → docker compose pull && up -d --force-recreate
```

Файлы:

| Файл | Роль |
|---|---|
| `Jenkinsfile` | описание пайплайна |
| `deploy/ci/Dockerfile.test` | образ для прогона тестов в CI |
| `deploy/prod/docker-compose.yml` | прод-стек: тянет готовые образы из Nexus |
| `deploy/prod/.env.example` | шаблон прод-окружения (реальный `.env` — секрет, не в гит) |

## Реестр образов

- Nexus UI/API: `http://192.168.1.41:8081`, hosted-репо `docker-dev`.
- Docker-эндпоинт (push/pull): **`192.168.1.41:5000`**.
- Имена образов: `192.168.1.41:5000/discocs/{backend,frontend,bot}`.
- Пул анонимный, push — под `nexus_token`.
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
| `nexus_token` | Username/Password | логин в Nexus для push *(уже есть)* |
| `discocs_prod_env` | Secret file | прод `.env` (содержимое `deploy/prod/.env.example`, заполненное) |
| `HS_SSH_KEY` | SSH Username with private key | деплой-ключ на `TARGET_SERVER` *(уже есть)* |

`discocs_prod_env`: заполни `deploy/prod/.env.example` реальными значениями
(минимум `DISCOCS_MUSIC_DIR` и `DISCOCS_STATE_DIR`), сохрани как файл и загрузи в Jenkins →
Manage Jenkins → Credentials → Add → **Secret file**, ID `discocs_prod_env`.

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
`deploy/prod/docker-compose.yml` и заполненный `.env` в `TARGET_DIR` на
`TARGET_SERVER` и там же гоняет `pull` + `up -d --force-recreate`. Вручную на хосте:

```bash
cd /home/infected2202/docker/discocs   # TARGET_DIR, там же лежит .env
docker compose -p discocs --env-file .env pull
docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans
```

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

## Задел: SonarQube

Стадия `Sonar` в `Jenkinsfile` закомментирована. Чтобы включить:

1. Поднять SonarQube, в Jenkins настроить сервер (`Manage Jenkins → System → SonarQube servers`, имя `sonar`) + токен.
2. Добавить `sonar-project.properties` (`sonar.projectKey=discocs`, `sonar.sources=app`).
3. Раскомментировать стадию `Sonar` и, при желании, добавить Quality Gate через `waitForQualityGate`.

## Траблшутинг

| Симптом | Причина / фикс |
|---|---|
| `http: server gave HTTP response to HTTPS client` | нет `insecure-registries` для `192.168.1.41:5000` на хосте |
| push `:latest` не перезаписывается | в Nexus hosted-репо выключен *Allow redeploy* |
| `docker: command not found` в пайплайне | в Jenkins-контейнер не проброшен сокет / нет docker CLI |
| бинд-маунт музыки падает на `up` | `DISCOCS_MUSIC_DIR` в `.env` указывает на несуществующий путь |
