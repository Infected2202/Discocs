# Социальные функции

Видеть, что слушают другие пользователи discocs, и иметь страницу профиля со
статистикой, открытую остальным залогиненным. Пользователей несколько и все
свои: всё открыто всем залогиненным, тумблеров приватности нет — кроме
приватных плейлистов, которые видит только владелец. Полная спека и фазы —
[`plans/social-spec.md`](../plans/social-spec.md).

Статус: **Ф1 (фундамент, backend)** — таблица `listens`, встроенные аватары,
слой доступа к профилю; **Ф2 (присутствие)** — «сейчас слушает» через
Navidrome: отчёты плеера и `GET /social/people`; **Ф3 (API профиля, backend)** —
статистика, лента прослушиваний, лайки, плейлисты. UI — Ф4–Ф5.

## Что такое прослушивание

Прослушивание — ровно то, что discocs скробблит в Navidrome. Правило одно —
`playback_event_is_listen` (`app/store/_helpers.py`):

- `play_threshold_reached` — всегда прослушивание;
- `completed`, прошедший `playback_event_is_completion`, — прослушивание, если
  в той же сессии раньше не было `play_threshold_reached`/`completed` для того
  же queue item (а без queue item — для того же трека). `completed` вне сессии
  засчитывается всегда;
- дубли (повтор `client_event_id`) не засчитываются никогда — они не
  записываются как новые события;
- всё остальное (`track_started`, `progress`, `skipped`, короткий `completed`…)
  — не прослушивание.

Решение принимается **один раз** — в транзакции, которая пишет событие
(`Store.record_playback_event` → `record_listen_for_event`,
`app/store/listens.py`), и сразу пишется в `listens`. Вердикт едет в
`PlaybackEventResult.listen`; скроббл в Navidrome
(`should_scrobble_navidrome_play`) его переиспользует, а не вычисляет заново.
Поэтому прослушивание записывается независимо от того, настроен ли Navidrome,
есть ли у трека маппинг и удался ли вызов scrobble.

`listens(id, user_id, track_id, listened_at, event_id UNIQUE)` — производная
от `playback_events` таблица для быстрых агрегаций по периодам (Ф3).
`listened_at` = `created_at` события. Подробности схемы —
[`docs/data-model.md`](data-model.md#listens).

**Бэкфилл.** При старте, пока `listens` пустая, она выводится из уже
накопленных `playback_events` (`backfill_listens_from_events`): события каждой
сессии прогоняются в порядке вставки через тот же предикат. События без
`user_id` или с неизвестным пользователем пропускаются. Идемпотентно
(`INSERT OR IGNORE` по `event_id`); вручную — `Store.backfill_listens()`.

## Аватары

Только встроенные, загрузки своего нет. Whitelist ключей — `app/avatars.py`
(`a01…a06`), файлы — `ui/src/assets/avatars/<key>.webp`; тест сверяет, что
множества совпадают. Добавить аватар = файл + ключ в whitelist.

Ключ хранится в `user_settings` под ключом `avatar` (без миграции схемы) и не
входит в ответ `/me/settings`. Случайный аватар назначается при логине и лениво
при первом чтении (`ensure_user_avatar`); назначенный больше не меняется, пока
пользователь сам не выберет другой. Ключ, выпавший из whitelist, заменяется
новым случайным.

| Метод | Путь | Что |
|---|---|---|
| GET | `/api/v1/users` | `{"items": [{username, avatar}]}` — все пользователи, больше никаких полей |
| PUT | `/api/v1/me/avatar` | `{key}` → `{avatar}`; только свой; ключ вне whitelist или лишние поля → 422 |

Оба эндпоинта требуют пользователя: service-принципал (без `user_id`) получает
403, без сессии — 401.

## Доступ к чужому профилю

Default-deny `Store.for_user` не ослабляется. `app/services/profile.py`
резолвит `username → user_id` без учёта регистра (как `users`), строит
`Store.for_user(target_id)` и вызывает на нём только явный набор read-методов,
отдавая whitelisted поля:

- `profile_header` — `username`, `avatar`, `created_at`, `viewer_is_owner`;
- `profile_playlists` — собственные плейлисты цели; приватные — только если
  зритель и есть владелец (`can_view_private_playlists`).

Неизвестный username → `ProfileNotFoundError` (404), service-принципал
→ `ProfileViewerRequiredError` (403). Никогда не отдаются: flow-профиль,
сессии/очередь, `user_settings` (кроме аватара), Navidrome-креды,
preference-score/дизлайки.

## API профиля (Ф3)

Роутер `app/api/profile.py`, сборка ответов — `app/services/profile.py`,
агрегации — SQL в `ListensStoreMixin` (`app/store/listens.py`) на store,
привязанном к **владельцу профиля**. Новых таблиц нет.

Общее для всех четырёх эндпоинтов:

- нужен залогиненный пользователь: service-принципал → 403
  `{"error": {"code": "forbidden", …}}`, без сессии → 401;
- `{username}` сравнивается без учёта регистра; неизвестный → 404
  `{"error": {"code": "not_found", …}}`;
- отдаются только перечисленные ниже поля. Нет и не будет: flow-профиля,
  сессий/очереди, `user_settings` (кроме аватара), кредов Navidrome,
  `score`/счётчиков предпочтений, дизлайков.

**Какие прослушивания считаются.** Только те, чей трек ещё есть в библиотеке
(строка `tracks` существует — у `listens.track_id` нет FK). Поэтому все числа
профиля сходятся с тем, что профиль может показать; трек с `missing_at`
(файл пропал при скане) по-прежнему считается.

### Общие формы

- **Трек** (`TrackSummary`, как в `VirtualTrackList`) — `track_summary_dict`:
  `id, title, artist, album, artists[{id,name}], duration, release{id,title}|null,
  artwork, navidrome_item_id, explicit, liked (всегда false), actions`.
- **Элемент шелфа** (`MediaCard` через `shelfItemToCard`) — `dashboard_shelf_item`:
  `id, entity_type, entity_id, title, subtitle, subtitle_links, artwork, action,
  play_action, badges, reason, debug`. Артист — `artist_shelf_item` (как в полке
  «Favourite Artists»), релиз — `_release_shelf_item`, трек — `_track_shelf_item`.
- **Прослушивание** = трек + `listen_id` (уникален, повторы одного трека
  различимы — ключ для React) + `listened_at` (UTC ISO-8601, `…+00:00`).

### `GET /api/v1/users/{username}/profile?period=&tz=`

- `period` ∈ `7d | 30d | 90d | 180d | 365d | all`, по умолчанию `30d`; другое
  значение → 422 (валидация FastAPI).
- `tz` — IANA-имя (`Intl.DateTimeFormat().resolvedOptions().timeZone`).
  Отсутствующее/невалидное/длиннее 64 символов → `UTC` (без ошибки); какой пояс
  применён — в `period.tz`. База поясов — пакет `tzdata` (в slim-образах
  системной нет).
- **Окно периода** — последние N **локальных календарных суток** включая
  сегодня: от локальной полуночи `сегодня − (N−1)` до текущего момента. Так
  столбики по дням и сводка считаются по одному и тому же набору прослушиваний.
  `all` — без нижней границы.

```json
{
  "header": {
    "username": "alice", "avatar": "a03", "created_at": "2026-…",
    "viewer_is_owner": false,
    "totals": {"listens": 1234, "artists": 210, "likes": 87}
  },
  "period": {"key": "30d", "tz": "Europe/Moscow",
             "since": "2026-09-04T21:00:00+00:00", "until": "2026-10-04T10:00:00+00:00"},
  "summary": {"listens": 312, "hours": 21.4, "artists": 64},
  "by_day_bucket": "day",
  "by_day": [{"date": "2026-09-05", "listens": 7}, "…"],
  "by_hour": [0, 0, 1, "… 24 числа"],
  "sound": {
    "genres": [{"label": "Electronic---Techno", "genre": "Electronic", "style": "Techno",
                "listens": 120, "share": 0.4321}],
    "moods":  [{"label": "energetic", "listens": 98, "share": 0.3529}]
  },
  "top_artists":  ["элемент шелфа артиста + listens"],
  "top_releases": ["элемент шелфа релиза + listens"],
  "top_tracks":   ["TrackSummary + listens"],
  "recent":       ["прослушивание × 10"]
}
```

- `header.totals` — за всё время, чистый SQL (без выгрузки строк):
  `listens` — число прослушиваний; `artists` — различные артисты по
  `track_artists` (у трека с несколькими артистами считаются все); `likes` —
  лайкнутые треки (`user_track_preferences.liked`, без пропавших с диска).
- `summary` — то же за период; `hours` = сумма `tracks.duration` всех
  прослушиваний периода / 3600, округление до 0,1.
- `by_day` — каждый бакет окна, нули включены, в поясе зрителя, по
  возрастанию. Бакеты: при охвате ≤ 366 суток — день (`date` = `YYYY-MM-DD`),
  иначе — календарный месяц (`date` = первое число, `YYYY-MM-01`); какой
  выбран — `by_day_bucket` (`day` | `month`). Месяц возможен только для `all`:
  окно `all` начинается с локальной даты первого прослушивания, у
  пользователя без прослушиваний `by_day = []`.
- `by_hour` — 24 числа: прослушивания периода по локальному часу (0–23).
- `sound` — rank-1 метка каждого прослушанного трека, вес = число
  прослушиваний: жанры — `genre_discogs400` (`label` как в модели, плюс
  `genre`/`style` — части до/после `---`), настроения — `mtg_jamendo_moodtheme`
  (метки `energetic`, `deep`, `dark`, …). Топ‑8 каждого; `share` (0..1, 4 знака)
  — доля среди прослушиваний периода, у трека которых есть предсказание этой
  модели. Нет предсказаний → пустые списки.
- `top_artists` (12), `top_releases` (12), `top_tracks` (20) — за период,
  поле `listens` у каждого. Артисты: прослушивание засчитывается каждому
  артисту трека (один раз, сколько бы ролей ни было). Релиз трека — тот же,
  что в его `release` (наименьшая `release_tracks.position`). Ничьи: больше
  прослушиваний → позже последнее прослушивание → меньший id.
- `recent` — 10 последних прослушиваний независимо от периода (как первая
  страница `/listens`).

Бакеты по дням/часам считаются в Python по `listened_at` прослушиваний окна
(поясное смещение с переходами на летнее время в SQLite не выразить); для
`all` это все прослушивания пользователя — при масштабе в несколько
пользователей это тысячи строк одной колонки. Итоги и топы — SQL.

### `GET /api/v1/users/{username}/listens?offset=&limit=`

`limit` 1–100 (по умолчанию 50), `offset` ≥ 0; вне диапазона → 422.

```json
{"items": ["прослушивание"], "total": 1234, "limit": 50, "offset": 0, "next_offset": 50}
```

Новые сначала (`listened_at DESC`, затем `id DESC`), повторы сохраняются.
`next_offset = null` на последней странице. Группировку по дням UI делает сам
по `listened_at` в поясе зрителя.

### `GET /api/v1/users/{username}/likes?limit=&offset=`

`limit` 1–100 (по умолчанию 50) и `offset` применяются к каждому списку.

```json
{
  "tracks":   {"items": ["элемент шелфа трека"],  "total": 87},
  "releases": {"items": ["элемент шелфа релиза"], "total": 12},
  "artists":  {"items": ["элемент шелфа артиста"], "total": 9},
  "limit": 50, "offset": 0
}
```

Лайки цели, а не зрителя: `user_*_preferences.liked = 1` её store (синхронизируются
со звёздами Navidrome при логине/обращении к лайкам). Треки — без пропавших с
диска, новые лайки первыми; релизы/артисты — те же запросы, что полки
«Favourite Albums/Artists» на главной.

### `GET /api/v1/users/{username}/playlists`

```json
{"items": ["плейлист как в GET /api/v1/playlists"], "total": 3}
```

Только собственные плейлисты цели (`list_owned_playlists`), приватные — лишь
когда зритель и есть владелец. Форма — `playlist_summary_dict`; `editable`
считается относительно зрителя (на чужом профиле всегда `false`).

## Присутствие («сейчас слушает»)

Источник правды — Navidrome (OpenSubsonic-расширение `playbackReport`): discocs
ничего о присутствии у себя не хранит.

### Запись: `POST /api/v1/playback/presence`

Тело `{track_id, state, position_ms}`, `state` ∈ `starting | playing | paused |
stopped`, `position_ms ≥ 0`, лишние поля → 422. Бэкенд
(`app/services/presence.py:report_presence`) маппит трек в Navidrome-id
(`external_id_for_track`) и вызывает `reportPlayback(mediaId, mediaType=song,
positionMs, state, playbackRate=1, ignoreScrobble=true)` **кредами текущей
сессии** — как star/scrobble. `ignoreScrobble=true`: play засчитывает только
наш `scrobble(submission=true)` по правилу прослушивания, отчёт о присутствии
не должен давать второй счёт.

| Ответ (всегда 200) | Когда |
|---|---|
| `{"status": "ok"}` | Navidrome принял отчёт |
| `{"status": "skipped", "reason": "no_navidrome_mapping"}` | у трека нет Navidrome-id |
| `{"status": "skipped", "reason": "missing_user_credentials"}` | auth включён, но в сессии нет Navidrome-кредов |
| `{"status": "failed"}` | Navidrome недоступен/ошибка (пишется warning в лог) |

Присутствие не ломает воспроизведение: ошибки Navidrome не превращаются в
HTTP-ошибки, таймаут вызова ≤ 5 с. В `playback_events`, `listens` и
предпочтения **ничего не пишется**. Service-принципал → 403, без сессии → 401.
CSRF-гейт (`auth_middleware`) пропускает same-origin POST с заголовком `Origin`
— так приходит и `sendBeacon` на `pagehide`; кросс-ориджин → 403.

Старый путь «now playing» удалён: `track_started` больше не шлёт
`scrobble(submission=false)` (`navidrome_scrobble_submission` возвращает только
`submission`). Что и когда шлёт плеер — [`docs/ui-player.md`](ui-player.md#presence-reporting-now-playing-for-other-users).

### Чтение: `GET /api/v1/social/people`

```json
{"items": [{"username": "bob", "avatar": "a02",
            "now_playing": {"track_id": 42, "title": "…", "artists": "A, B", "state": "playing"}}]}
```

- Все пользователи discocs (`users`), **кроме вызывающего**; аватар — через
  `ensure_user_avatar` (как `/users`). Service-принципал → 403.
- Живые данные — один `getNowPlaying` **сервисным** аккаунтом
  (`DISCOCS_NAVIDROME_USER`), кэш в процессе на 5 с (`NowPlayingCache`:
  `threading.Lock`, `time.monotonic`, ключ — URL + сервисный юзер). Ошибка
  тоже кэшируется на TTL, чтобы опрос клиентов не долбил лежащий Navidrome.
- Учитываются только записи со `state` ∈ `starting | playing`; запись без
  `state` (старый клиент без `reportPlayback`) считается `playing`, `paused`/
  `stopped` — «не играет».
- `username` записи сопоставляется с `users.navidrome_username` без учёта
  регистра; если у пользователя несколько плееров — берётся запись с
  наименьшим `minutesAgo`.
- `id` записи → трек через `get_track_by_external_id("navidrome", id)`: если
  трек есть — наши `title` и имена артистов (через запятую), иначе
  `track_id: null` и `title`/`artist` из ответа Navidrome.
- Navidrome не настроен или недоступен → у всех `now_playing: null`, ответ 200.
- Сортировка: сначала играющие, внутри групп — по `last_login_at` (новее выше).

Фронт: `fetchPeople` (`ui/src/api/social.ts`) и хук `usePeople`
(`ui/src/api/hooks/usePeople.ts`): `refetchInterval` 15 с,
`refetchIntervalInBackground: false` (на скрытой вкладке не опрашивает), 4xx
не ретраится. Шелф «Люди» — Ф4.

### Ограничения

- Клиенты без `reportPlayback` (другие Subsonic-плееры) видны «играющими» до
  истечения записи в Navidrome (~конец трека), их пауза не видна.
- Repeat-one перезапускает трек без нового `starting` — Navidrome продолжает
  экстраполировать позицию.
- Закрытие вкладки шлёт `stopped` через `sendBeacon`; если браузер был убит
  (а не закрыт), запись живёт в Navidrome до своего таймаута.
- В нативной сборке (Capacitor) `sendBeacon` не используется — на `pagehide`
  уходит обычный keepalive-`fetch`.
- *Проверить вживую:* что сервисный аккаунт видит в `getNowPlaying` сессии всех
  пользователей, что Navidrome отдаёт `state`/`positionMs` в записях и что
  `ignoreScrobble=true` не даёт двойного счёта.
