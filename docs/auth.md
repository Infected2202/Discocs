# Авторизация (Phase 1 — access gate)

Цель: закрыть discocs перед выносом в публичный домен. Фаза 1 — **гейт доступа**
поверх общих данных. Разделение данных по пользователям (персональные
рекомендации) — отдельный будущий эпик, схема пока одно-пользовательская.

## Модель

**Navidrome как источник личности (IdP).** Своей базы паролей нет. Логин-форма —
два поля (логин + пароль); URL Navidrome зашит в конфиге. Бэкенд проверяет
креды Subsonic-`ping`'ом к настроенному Navidrome. Успех → создаётся серверная
сессия, ставится cookie. Пароль сохраняется только как AES-GCM ciphertext,
ключ которого выводится из сырого session-token в HttpOnly cookie; одной БД
для расшифровки недостаточно. Войти может любой, у кого валидный лог/пас на
настроенном Navidrome.

Публичный SPA не читает и не изменяет глобальные настройки Navidrome. Адрес
сервера задаётся владельцем на backend через `DISCOCS_NAVIDROME_URL`; обычный
пользователь вводит только свои Navidrome username/password на странице входа.
Интерактивные звёзды, плейлисты и скробблинг выполняются под аккаунтом активной
сессии. Служебные каталог, обложки и download используют сервисный аккаунт,
который доступен только в приватной `/admin` и серверном конфиге, а не в
публичной Settings.

## Где стоит гейт

Гейт — **middleware в бэкенде** (`app/api/auth_middleware.py`), а не только в SPA:
окно логина во фронте косметично, если API открыт. Middleware покрывает и
`/admin`, и все API-роутеры.

Запрос авторизован, если выполнено **любое**:

1. гейт выключен (`DISCOCS_AUTH_ENABLED` не `true`), **или**
2. путь публичный: `/health`, `/api/v1/auth/{login,logout,session}`, **или**
3. валидный заголовок `X-Discocs-Service-Token` (машинный принципал:
   воркеры/бот/плагин), **или**
4. валидная session-cookie.

Иначе — `401`.

### Почему нужен service-token, а не «доверие подсети»

В compose весь браузерный трафик приходит на бэкенд от frontend-nginx (docker-IP),
и воркеры/бот — тоже с docker-IP. По одному source-IP «свой/чужой» не различить,
поэтому машинные вызовы аутентифицируются общим токеном.

## Сессии

- Таблица `sessions` (см. `app/store/base.py`). Хранится только **SHA-256 токена**,
  не сам токен: утечка БД не воскрешает живую сессию. Пароли не хранятся.
- Токен — 256-бит из `secrets.token_urlsafe`. Cookie: `HttpOnly`, `SameSite=Lax`,
  `Secure` (когда запрос по HTTPS — по `X-Forwarded-Proto`), `Path=/`.
- Абсолютный срок жизни — `DISCOCS_SESSION_TTL_HOURS` (по умолчанию 720 ч = 30 дней).
  Протухшие сессии удаляются лениво при обращении.

## Поведение SPA (когда показывается /login)

Клиент (`ui/src/components/auth/RequireAuth.tsx`) редиректит на `/login`
**только** если сервер явно ответил «не авторизован»: `authenticated: false`
из `/api/v1/auth/session` или `401` от API. **Сетевая ошибка — не разлогин**:
на мобильных при разворачивании вкладки первый fetch часто падает (радиомодуль
ещё не поднял сеть), а сессия при этом жива. В этом случае:

- если состояние сессии уже известно (кэш react-query) — приложение остаётся
  как есть, запрос ретраится;
- если состояния нет (холодный старт офлайн) — экран «Нет соединения с
  сервером» с кнопкой «Повторить», не форма логина.

`LoginPage` при открытии сам проверяет `/auth/session`: если сессия валидна —
сразу уводит обратно (на страницу из `state.from`, которую передал
`RequireAuth`), форма не показывается.

## Защита от брутфорса

Логин лимитируется по IP (in-memory, без Redis): `DISCOCS_LOGIN_MAX_ATTEMPTS`
неудач в окне `DISCOCS_LOGIN_LOCKOUT_SECONDS` → `429`. Успешный вход сбрасывает
счётчик. За nginx нужен корректный `X-Forwarded-For` (иначе все клиенты за
прокси сольются в один IP).

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DISCOCS_AUTH_ENABLED` | `false` | Мастер-выключатель гейта |
| `DISCOCS_SERVICE_TOKEN` | `` | Машинный токен (одинаковый у backend/bot/worker) |
| `DISCOCS_SESSION_TTL_HOURS` | `720` | Срок жизни сессии |
| `DISCOCS_SESSION_COOKIE_NAME` | `discocs_session` | Имя cookie |
| `DISCOCS_LOGIN_MAX_ATTEMPTS` | `5` | Порог блокировки логина |
| `DISCOCS_LOGIN_LOCKOUT_SECONDS` | `900` | Окно блокировки |
| `DISCOCS_CORS_ORIGINS` | `` | Явный allowlist origin'ов (с куками). Пусто = wildcard без кук |
| `DISCOCS_NAVIDROME_URL` | из settings.json | Адрес Navidrome для проверки логина |

## Почему гейт по умолчанию выключен

Каждый push авто-деплоится через Jenkins. Гейт, включённый по умолчанию,
залочил бы работающие бот/воркеры/плагин на ближайшем деплое до раздачи токенов.
Поэтому изменение приезжает «тёмным»; включение — сознательный конфиг-шаг при
выносе в домен.

## Чек-лист включения (при выносе в домен)

1. Сгенерировать токен: `openssl rand -hex 32`.
2. Прописать `DISCOCS_SERVICE_TOKEN` **одинаково** у backend, bot, worker.
   Для локальных GPU-workers значение задаётся в корневом `.env` и передаётся
   через `docker-compose.worker.yml`; для production backend/bot — в
   `/home/infected2202/docker/discocs/.env` по шаблону
   `deploy/prod/.env.example`. Сначала пересоздать workers с токеном и только
   затем включать гейт на backend, чтобы не остановить очередь анализа.
3. Плагин Navidrome: если используется с гейтом — задать тот же токен в его
   HTTP-заголовках; иначе его эндпоинты доступны только на внутреннем
   (не проксируемом наружу) порту.
4. Убедиться, что настроен `DISCOCS_NAVIDROME_URL` (или сохранён в settings.json).
5. Поставить `DISCOCS_AUTH_ENABLED=true`.
6. На доменном nginx: TLS (Let's Encrypt), проксировать **только** SPA и
   браузерный API; **не** пробрасывать наружу `/workers` и порт бэкенда 8711.
7. Проверить: аноним → редирект на `/login`; вход валидными кредами Navidrome →
   доступ; воркер/бот с токеном работают.

Для текущего развёртывания порядок команд после записи одного и того же секрета
в оба `.env`:

```powershell
docker compose -p discocs -f docker-compose.worker.yml up -d --force-recreate
```

```bash
cd /home/infected2202/docker/discocs
docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans --wait --wait-timeout 120
```

## Пример доменного nginx (Let's Encrypt)

Терминирует TLS и проксирует на frontend-контейнер. Пробрасывает
`X-Forwarded-Proto`/`X-Forwarded-For`; вырезает входящий `X-Discocs-Service-Token`
(чтобы публичный клиент не мог подставить машинный токен).

```nginx
server {
    listen 443 ssl http2;
    server_name discocs.example.com;

    ssl_certificate     /etc/letsencrypt/live/discocs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/discocs.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "DENY" always;

    location / {
        proxy_pass http://127.0.0.1:80;   # frontend-контейнер discocs
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Никогда не доверять машинному токену от внешнего клиента:
        proxy_set_header X-Discocs-Service-Token "";
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name discocs.example.com;
    return 301 https://$host$request_uri;
}
```

Public frontend nginx applies a second deployment boundary before requests
reach FastAPI. It returns 404 for `/admin`, `/api/map`, worker endpoints and
global job/settings operations. Only the per-user `albums-for-you` and
`flow-profile` maintenance endpoints are allowed from the `/jobs` family.

The backend port `:8711` remains the private operational endpoint for the
legacy admin and remote analysis workers. Restrict it to localhost/private LAN
with the host firewall; do not publish or forward it from the internet.

## Дальнейшие фазы (TODO)

Фаза 1 (эта дока выше) приехала **тёмной** — гейт по умолчанию выключен и пока
ничего не защищает. Дальнейшая работа, по приоритету:

### Фаза 1.5 — Включение + харденинг (следующий шаг перед выносом в домен)

Без этого гейт бесполезен. Для параноика — закрыть до публичной экспозиции:

1. **Поднять домен-nginx + Let's Encrypt** для discocs (конфига/имени пока нет).
   Применить sample выше: TLS, HSTS, вырезание входящего `X-Discocs-Service-Token`,
   не пробрасывать наружу порт 8711 и `/workers`.
2. **Раздать `DISCOCS_SERVICE_TOKEN`** воркеру/боту/плагину и выставить
   `DISCOCS_AUTH_ENABLED=true` (чек-лист «Чек-лист включения» выше).
3. **Зашифровать пароль Navidrome at-rest.** Админский пароль сервисного аккаунта
   лежит открытым текстом в `data/settings.json` (`app/api/settings.py::update_navidrome_settings`).
   Логин Фазы 1 пароль не хранит, но этот — да. Зашифровать (`cryptography`/Fernet,
   ключ из env).
4. **CSRF-токен (double-submit)** для мутаций (star/settings). Фаза 1 опирается
   только на `SameSite=Lax` — для defense-in-depth добавить токен.
5. Мелочи: security-заголовки на уровне приложения (не только в nginx-sample);
   lockout логина сейчас in-memory per-process (при рестарте/нескольких воркерах
   сбрасывается) — перенести в SQLite при необходимости.

### Фаза 2 — Мультиюзер (крупный эпик, ради персональных рекомендаций)

Перепахивание модели данных, не окно. Схема и Store-слой переводятся на
обязательный пользовательский скоуп.

1. **Схема:** `user_id` в PK всех preference/session/mix/playlist/flow/playback-таблиц
   + миграция.
2. **Личность:** таблица `users`, маппинг `сессия → Navidrome username → user_id`.
   ✅ **Реализовано (чекпоинт 1, `plans/multiuser-spec.md`):** таблица `users`
   (`id`, `navidrome_username` unique, `created_at`, `last_login_at`) —
   `app/store/users.py`; апсёрт при успешном логине (`auth.create_session`),
   `user_id` пишется в новую колонку `sessions.user_id`. `resolve_session`
   отдаёт `ResolvedSession(user_id, username)`, middleware кладёт
   `request.state.user_id` (у `service`-принципала — `None`). Легаси-сессии
   (без `user_id`) резолвят id через `users` по username. Регистрации нет —
   аккаунты заводит владелец в Navidrome (§9 спеки).
3. **Per-user креды Navidrome:** ✅ пароль хранится только как AES-GCM
   ciphertext в строке сессии; ключ выводится из сырого session-token, которого
   нет в БД. Интерактивные starred/star/unstar/scrobble используют credentials
   текущей сессии, а служебные catalog/cover/download пути сохраняют сервисный
   аккаунт.
4. **Фильтрация по юзеру** во всех запросах store-миксинов, рекомендере, автоплее,
   дашборде, flow.
   ✅ **Реализовано (чекпоинт 3):** `Store.for_user(user_id)`, default-deny для
   явно unscoped Store, составные PK preference/cache, owner-фильтрация playback,
   flow, generated mixes, playlists, dashboard и recommendation seeds. HTTP-
   контекст связывает Store с `user_id` активной сессии; service-принципал без
   пользователя не получает доступ к персональным операциям.
   ✅ **API boundary (чекпоинт 4):** middleware передаёт identity через
   request-local `ContextVar`, а каждый router получает уже scoped Store из
   `api/deps.py:context()`. Двухпользовательский API-тест проверяет изоляцию
   списков и прямых обращений к playlist/playback ID.
5. **Per-user фон:** ✅ maintenance итерирует всех пользователей отдельными
   scoped Store для albums-for-you, flow и generated mixes. Фоновый service-
   account play-state refresh при включённом auth отключён: session-bound
   credentials недоступны фону, а импорт сервисных данных одному owner был бы
   утечкой. Starred sync и scrobble выполняются интерактивно от имени сессии.

6. **Client identity isolation:** ✅ explicit logout clears React Query,
   persisted playback session/position, player queue/history and Media Session,
   per-user likes, and transient dialogs even if the logout request fails.
   A global 401 clears persisted playback before redirecting to login. Volume,
   mute and sidebar preferences remain because they are device-level, not
   personal library data. The profile popover shows the active username.


### Фаза 3 — Управление сессиями / доп. паранойя (по желанию)

Список активных сессий с отзывом, «запомнить меня» vs короткие сессии, аудит-лог
входов, опционально passkeys/2FA.
