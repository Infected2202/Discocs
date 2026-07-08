# Миграция backend-путей под единый префикс /api/v1

## Проблема

Прямой переход/обновление страницы на `/artists/3468` в новом UI даёт
`{"detail":"Not Found"}`. Причина: nginx (`deploy/nginx/default.conf.template`)
проксирует в backend всё, что матчит `^/(api|tracks|artists|releases|mixes|
settings|...)(/|$)` — по префиксу, без разбора, HTML-навигация это или XHR.
Часть backend-путей делит первый сегмent с SPA-роутами
(`ui/src/router.tsx`: `artists/:id`, `releases/:id`, `settings`), поэтому
прямая навигация на `/artists/3468` улетает в backend, а не в SPA-фолбэк —
backend отвечает своим 404, потому что настоящий эндпоинт лежит на
`/api/v1/artists/{id}`, а не на голом `/artists/{id}`.

Client-side переход (из дашборда) не задет, т.к. React Router не делает
полный HTTP-запрос на `/artists/3468` — только на `/api/v1/artists/3468`.

## Решение

Свести **все** backend REST-пути под `/api/v1/*`, кроме `/health`, `/admin/*`,
`/debug/ui` (не REST, HTML/health-check) и **кроме Navidrome-плагина**
(`plugins/navidrome-instant-mix`, Go, деплоится отдельно внутри Navidrome —
по решению пользователя не трогаем, он всё равно будет выпилен в будущем;
`/navidrome/similar` и `/navidrome/plugin-event` для него временно
перестанут отвечать после миграции backend).

nginx / vite после миграции проксируют в backend только `^/(api|admin|
health)(/|$)`, всё остальное — SPA fallback (`try_files ... /index.html`).

## Затронутые потребители (кроме плагина)

| Потребитель | Файлы | Что делать |
|---|---|---|
| Backend routes | `app/api/*.py` | Все роутеры → `APIRouter(prefix="/api/v1")`, декораторы — относительные пути |
| Backend URL-генераторы | `app/serializers/{entities,search,mixes}.py`, `app/analysis_helpers.py`, `app/api/middleware.py` | Поправить хардкод путей (cover/instant-mix/audio_url, лог-фильтр) |
| Новый UI | `ui/src/api/*.ts`, `ui/src/store/navidromeStore.ts`, `ui/src/components/player/*`, `ui/src/components/media/TrackMenu.tsx`, `ui/vite.config.ts` | Обновить fetch-пути, сузить `proxyPaths` |
| Старая админка | `app/ui.html` | ~20 мест с `fetch`/`json(...)` на голые пути |
| Воркер (CLI) | `app/cli.py` | `/workers/*` → `/api/v1/workers/*` |
| Бот | `discocs_bot/bot/services/discocs.py` | `/navidrome/similar` → `/api/v1/navidrome/similar` (`/health` не трогаем) |
| nginx | `deploy/nginx/default.conf.template` | Сузить regex до `api|admin|health` |
| Тесты | `tests/test_api.py`, `test_mixes.py`, `test_api_middleware.py`, `test_auth.py`, `test_cli_worker.py`, `ui/src/**/*.test.tsx` | Обновить пути, добавить тест-страж на конвенцию |
| Доки | `docs/navidrome-plugin.md` и др. | Отметить breaking change для плагина |

## Не трогаем

- `plugins/navidrome-instant-mix/main.go` — Go-плагин Navidrome, отдельный деплой.
- `/health`, `/admin/*`, `/debug/ui` — остаются как есть.
- `app/api/auth.py` — уже `APIRouter(prefix="/api/v1/auth")`, паттерн корректный, менять нечего.

## Карта путей (старый → новый)

### app/api/tracks.py
- `/tracks` → `/api/v1/tracks`
- `/tracks/search` → `/api/v1/tracks/search`
- `/browse/facets` → `/api/v1/browse/facets`
- `/lost-files` (GET, DELETE) → `/api/v1/lost-files`
- `/analysis/errors` (GET, DELETE) → `/api/v1/analysis/errors`
- `/tracks/{track_id}` → `/api/v1/tracks/{track_id}`
- `/tracks/{track_id}/analysis` → `/api/v1/tracks/{track_id}/analysis`
- `/tracks/{track_id}/audio` (HEAD+GET) → `/api/v1/tracks/{track_id}/audio`
- `/tracks/{track_id}/cover` → `/api/v1/tracks/{track_id}/cover`
- `/tracks/{track_id}/similar` → `/api/v1/tracks/{track_id}/similar`
- `/text-search` → `/api/v1/text-search`
- `/tracks/similar/mix` → `/api/v1/tracks/similar/mix`

### app/api/jobs.py
- `/stats` → `/api/v1/stats`
- `/models/head-pack` → `/api/v1/models/head-pack`
- `/jobs/analyze` → `/api/v1/jobs/analyze`
- `/models/download-head-pack` → `/api/v1/models/download-head-pack`
- `/jobs/download-head-models` → `/api/v1/jobs/download-head-models`
- `/jobs/analyze-heads` → `/api/v1/jobs/analyze-heads`
- `/jobs/analyze-audio-features` → `/api/v1/jobs/analyze-audio-features`
- `/jobs/analyze-genres` → `/api/v1/jobs/analyze-genres`
- `/jobs/index` → `/api/v1/jobs/index`
- `/jobs/check-missing-files` → `/api/v1/jobs/check-missing-files`
- `/jobs/navidrome-sync` → `/api/v1/jobs/navidrome-sync`
- `/jobs` (GET) → `/api/v1/jobs`
- `/jobs/{job_id}` → `/api/v1/jobs/{job_id}`
- `/jobs/{job_id}/cancel` → `/api/v1/jobs/{job_id}/cancel`
- `/index/rebuild` → `/api/v1/index/rebuild`
- `/feedback` → `/api/v1/feedback`
- (уже `/api/v1/jobs/release-aggregates`, `/api/v1/albums/settings`,
  `/api/v1/jobs/release-aggregates/status`, `/api/v1/jobs/albums-for-you`,
  `/api/v1/jobs/albums-for-you/status`, `/api/v1/jobs/flow-profile`,
  `/api/v1/jobs/flow-profile/status` — убрать дублирующий префикс в
  декораторе, т.к. он появится от `APIRouter(prefix=...)`)

### app/api/workers.py (всё под /api/v1)
`/workers/tasks/{task_id}/state`, `/workers/register`, `/workers/heartbeat`,
`/workers`, `/workers/claim`, `/workers/tasks/{task_id}/audio`,
`/workers/results`, `/workers/failures`, `/workers/release`

### app/api/metrics.py (всё под /api/v1)
`/metrics/features`, `/metrics/features/{feature_name}/values`, `/metrics/search`

### app/api/navidrome.py (всё под /api/v1 — снимает коллизию artists/releases)
`/navidrome/starred`, `/navidrome/starred/ids`,
`/releases/{release_id}/navidrome-star` (PUT),
`/artists/{artist_id}/navidrome-star` (PUT),
`/tracks/{track_id}/navidrome-star` (PUT),
`/navidrome/starred/similar`, `/navidrome/similar`

### app/api/settings.py (всё под /api/v1 — снимает коллизию settings)
`/settings/navidrome` (GET, PUT), `/navidrome/ping`, `/navidrome/plugin-event`,
`/instant-mix/settings` (GET, PUT)

### app/api/mixes.py
- Убрать дублирующий `/api/v1` в декораторах, кроме:
- `/tracks/{track_id}/instant-mix` → `/api/v1/tracks/{track_id}/instant-mix`
- `/instant-mix/requests` → `/api/v1/instant-mix/requests`
- `/instant-mix/requests/{request_id}` → `/api/v1/instant-mix/requests/{request_id}`

### app/api/playlists.py, dashboard.py, search.py, artists.py, releases.py, playback.py, flow.py
Убрать дублирующий `/api/v1` из декораторов (единый префикс на роутере).

## Прочие места с хардкодом путей

- `app/serializers/entities.py:157` — `artwork` для трека: `/tracks/{id}/cover`
- `app/serializers/search.py:170,195` — `artwork_url=f"/tracks/{id}/cover?size=512"`
- `app/serializers/search.py:205` — `"endpoint": f"/tracks/{id}/instant-mix"`
- `app/serializers/mixes.py:15` — `/tracks/{track_id}/cover?size=512`
- `app/api/middleware.py:16-22` — `should_log_http_request` фильтрует по
  голым префиксам (`/stats`, `/jobs`, `/metrics`, `/navidrome`,
  `/instant-mix`, `/text-search`, `/tracks/.../cover|similar|navidrome-star`)
- `app/analysis_helpers.py:236` — `audio_url: f"/workers/tasks/{id}/audio"`

## Фазы выполнения

Каждая фаза — самостоятельный шаг, после которого код должен быть
consistent (можно остановиться). Тесты пишем вместе с кодом, но не гоняем
локально (см. `CLAUDE.md` — результат смотрим в Jenkins). Коммит и push —
**один раз**, после того как все фазы пройдены (см. правило проекта про
`disableConcurrentBuilds`).

- [x] **Фаза 1 — Backend routes.** Добавить `prefix="/api/v1"` во все
      `APIRouter()` в `app/api/` (кроме `auth.py`), убрать дублирующий
      `/api/v1` из деклараторов, где он уже был захардкожен. Обновить
      `main.py` при необходимости (middleware/exception handler уже
      завязаны на `/api/v1` — трогать не надо).
- [x] **Фаза 2 — Backend-генераторы URL.** Поправить
      `serializers/entities.py`, `serializers/search.py`,
      `serializers/mixes.py`, `api/middleware.py`, `analysis_helpers.py`.
- [x] **Фаза 3 — Тесты backend.** Обновить пути в
      `tests/test_api.py`, `test_mixes.py`, `test_api_middleware.py`,
      `test_auth.py`, `test_cli_worker.py`.
- [x] **Фаза 4 — Новый UI.** `ui/src/api/*.ts`, `navidromeStore.ts`,
      плеер-компоненты (`ExpandedPlayer`, `PlayerBar`, `QueueItem`,
      `TrackMenu`), `vite.config.ts` (`proxyPaths` → `/api`, `/health`,
      `/admin`). Обновить соответствующие `*.test.tsx`.
- [x] **Фаза 5 — Старая админка.** `app/ui.html` — обновить все
      `fetch`/`json(...)` вызовы на голые пути (список — см. выше).
- [x] **Фаза 6 — Воркер и бот.** `app/cli.py` (`/workers/*`),
      `discocs_bot/bot/services/discocs.py` (`/navidrome/similar`).
- [x] **Фаза 7 — nginx.** `deploy/nginx/default.conf.template` — сузить
      regex backend-путей до `^/(api|admin|health)(/|$)`.
- [x] **Фаза 8 — Доки.** `docs/navidrome-plugin.md` — задокументировать,
      что плагин временно не будет отвечать (similar/plugin-event) до
      его обновления или выпила. Проверить другие `docs/*.md` на
      упоминания старых путей.
- [x] **Фаза 9 — Финальный аудит.** grep по репо на предмет оставшихся
      голых упоминаний backend-путей (кроме плагина), один коммит + push
      в `origin` и `gitea`, проверка результата в Jenkins (см. `## CI
      results` в `CLAUDE.md`).
