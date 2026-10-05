# Социальные функции

Видеть, что слушают другие пользователи discocs, и иметь страницу профиля со
статистикой, открытую остальным залогиненным. Пользователей несколько и все
свои: всё открыто всем залогиненным, тумблеров приватности нет — кроме
приватных плейлистов, которые видит только владелец. Полная спека и фазы —
[`plans/social-spec.md`](../plans/social-spec.md).

Статус: **Ф1 (фундамент, backend)** — таблица `listens`, встроенные аватары,
слой доступа к профилю; **Ф2 (присутствие)** — «сейчас слушает» через
Navidrome: отчёты плеера и `GET /social/people`; **Ф3 (API профиля, backend)** —
статистика, лента прослушиваний, лайки, плейлисты; **Ф4 (UI главной)** — шелф
«Люди», относительное время на полке «История», пункт «Мой профиль»;
**Ф5 (UI профиля)** — страница `/u/:username` и полная история
`/u/:username/history` ([ниже](#ui-профиля-ф5)). Все фазы спеки реализованы.

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
(`a01…a19`), файлы — `ui/src/assets/avatars/<key>.webp` (512×512); тест
сверяет, что множества совпадают. Добавить аватар = файл + ключ в whitelist.
Сетка выбора идёт не по ключам, а в фиксированном перемешанном порядке (хеш
ключа, `ui/src/lib/avatars.ts`): новые аватары встают в случайные места, а
порядок не прыгает между открытиями.

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

Единственное чтение чужой сессии — [«Слушать вместе»](#слушать-вместе-ф6):
`app/services/listen_along.py` вызывает на store ведущего один явный метод
`playback_presence_snapshot` и не отдаёт наружу ни сессию, ни её id/состояние —
только копирует id треков очереди в новую сессию **зрителя**.

## API профиля (Ф3)

Роутер `app/api/profile.py`, сборка ответов — `app/services/profile.py`,
агрегации — SQL в `ListensStoreMixin` (`app/store/listens.py`) на store,
привязанном к **владельцу профиля**. Новых таблиц нет.

Общее для всех эндпоинтов профиля (`/profile`, `/listens`, `/likes`,
`/likes/{kind}`, `/top/{kind}`, `/playlists`):

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
  "top_artists_total": 64,
  "top_releases": ["элемент шелфа релиза + listens"],
  "top_releases_total": 40,
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
- `top_artists` (16), `top_releases` (16), `top_tracks` (5) — за период,
  поле `listens` у каждого. 16 — общий размер превью полки
  (`SHELF_PREVIEW_LIMIT`, `app/services/shelves.py`); `top_artists_total` /
  `top_releases_total` — полная длина топа за период (по ним UI решает,
  показывать ли «Ещё»; весь топ — `/top/{kind}`). Артисты: прослушивание засчитывается каждому
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

### `GET /api/v1/users/{username}/likes/{kind}?limit=&offset=`

Полный список одного вида лайков (`kind` ∈ `tracks | releases | artists`,
другое → 422) — «Ещё» у полки лайков. `limit` 1–100 (по умолчанию 50),
`offset` ≥ 0. Те же элементы и порядок, что в соответствующем списке
`/likes`:

```json
{"items": ["элемент шелфа"], "total": 87, "limit": 50, "offset": 0, "next_offset": 50}
```

### `GET /api/v1/users/{username}/top/{kind}?period=&tz=&limit=&offset=`

Полный топ периода (`kind` ∈ `artists | releases`, другое → 422) — «Ещё» у
полок топов. `period`/`tz` — как у `/profile` (окно то же), `limit` 1–100
(по умолчанию 50), `offset` ≥ 0. Порядок и элементы (с `listens`) — те же,
что у `top_artists`/`top_releases` профиля, первая страница при `limit=16`
совпадает с полкой:

```json
{"items": ["элемент шелфа + listens"], "total": 64, "limit": 50, "offset": 0,
 "next_offset": 50, "period": {"key": "30d", "tz": "Europe/Moscow", "since": "…", "until": "…"}}
```

### `GET /api/v1/users/{username}/playlists?limit=&offset=`

```json
{"items": ["плейлист как в GET /api/v1/playlists"], "total": 3,
 "limit": 16, "offset": 0, "next_offset": null}
```

Только собственные плейлисты цели (`list_owned_playlists`), приватные — лишь
когда зритель и есть владелец (и в `total` тоже: чужие приватные не видны и
не считаются). Форма — `playlist_summary_dict`; `editable` считается
относительно зрителя (на чужом профиле всегда `false`). `limit` 1–100 —
необязателен: без него отдаются все плейлисты (`limit` в ответе = их число);
`offset` ≥ 0. Новые (по `updated_at`) первыми.

## UI профиля (Ф5)

Роуты (внутри `AppShell`, под `RequireAuth`): `/u/:username` →
`ui/src/pages/ProfilePage.tsx`, `/u/:username/history` →
`ui/src/pages/ListeningHistoryPage.tsx`, полные списки полок —
`/u/:username/top/:kind?period=`, `/u/:username/likes/:kind`,
`/u/:username/playlists` → `ui/src/pages/ProfileListPages.tsx` (см. «Полные
списки» ниже). i18n — namespace `user`.

**Данные.** Обёртки API — `ui/src/api/profile.ts` (типы по контракту выше),
хуки — `ui/src/api/hooks/useProfile.ts`:

| Хук | Ключ react-query | Что |
|---|---|---|
| `useUserProfile(username, period)` | `["profile", username, "stats", period, tz]` | `/profile`; `tz` = `Intl.DateTimeFormat().resolvedOptions().timeZone` (`viewerTimeZone`); `keepPreviousData` — при смене периода старые цифры видны до прихода новых |
| `useUserListens(username)` | `["profile", username, "listens", 50]` | `/listens`, infinite по `next_offset` |
| `useUserLikes(username)` | `["profile", username, "likes", 16]` | `/likes?limit=16` (превью полок) |
| `useUserPlaylists(username)` | `["profile", username, "playlists", 16]` | `/playlists?limit=16` (превью полки) |
| `useUserTopList(username, kind, period)` | `["profile", username, "top", kind, period, tz, 48]` | `/top/{kind}`, infinite по `next_offset` |
| `useUserLikesList(username, kind)` | `["profile", username, "likes-list", kind, 48]` | `/likes/{kind}`, infinite |
| `useUserPlaylistsList(username)` | `["profile", username, "playlists-list", 48]` | `/playlists`, infinite |
| `useSetMyAvatar()` | — | `PUT /me/avatar`; на успех инвалидирует `["profile"]` и `["social","people"]` |

4xx (неизвестный пользователь) не ретраится.

**Страница** — одна длинная, секциями (как страница артиста), без вкладок:

1. Шапка — `CollectionHeader`: круглый аватар (`avatarUrl`), логин, мета
   «в discocs с <дата> · прослушивания · артисты · лайки» (за всё время). На
   своём профиле (`viewer_is_owner`) аватар — кнопка «Выбрать аватар» →
   `AvatarPickerDialog` (`components/profile/`, существующий `dialog`): сетка
   `AVATAR_KEYS`, текущий отмечен (`aria-pressed`), клик сохраняет и закрывает,
   ошибка — строка в диалоге. Если `usePeople()` показывает пользователя
   играющим (в том числе себя на своём профиле), — строка «Сейчас слушает:
   Артист - Трек» с мигающей зелёной точкой (`motion-safe:animate-pulse`) под
   логином и статами; текст шапки выровнен по центру аватара
   (`CollectionHeader align="center"`). Кнопок действий нет.
2. Переключатель периода — `tabs`: 7д/30д/90д/180д/год/всё время (по умолчанию
   30д; хранится в URL как `?period=`, по умолчанию параметра нет — возврат со
   страницы полного топа восстанавливает период). Управляет статистикой и топами; их заголовки называют период:
   «Статистика (30 дн.)», «Топ артистов (год)», «… (всё время)» (`periodSuffix`).
3. Последние прослушивания — 10 строк `VirtualTrackRow` (`ListenRows`): ключ —
   `listen_id` (повторы видны), вместо длительности — относительное время
   (`formatRelativeTime`, абсолютное — в `title`). «Все» → история.
   Играющий сейчас трек (`now_playing.track` из `/social/people`) стоит первой
   строкой с пометкой «сейчас» — и здесь, и на странице полной истории. Его
   прослушивание, записанное посреди трека, пока он играет, скрыто
   (`withoutCurrentPlay`: тот же трек и не дольше длительности + 1 мин назад),
   иначе он был бы в списке дважды. Сами списки не опрашиваются — опрашивается
   только «сейчас» (`/social/people`, 15 с); когда у пользователя сменился трек
   или воспроизведение остановилось, `useRefreshListensOnPlayChange`
   перезапрашивает профиль и историю, и доигравший трек появляется в списке.
   Повтор того же трека смены не даёт.
4. Статистика (`ProfileStats`) — сводка (прослушивания, часы, артисты); столбики
   по дням или по месяцам (по `by_day_bucket`), 24 столбика по часам, звуковой
   профиль (жанры показываются стилем, жанр — в подсказке; настроения) с долей.
   Графики — div'ы на Tailwind, высота пропорциональна максимуму
   (`barHeightPercent`, ненулевое значение ≥ 2 %); у каждого столбика
   `aria-label`/`title` с датой и числом. Подписи оси — примерно 6 на график.
5. Топ артистов, топ релизов — `Shelf` + `MediaCard` (`shelfItemToCard`), в
   подписи «N прослушиваний». 16 карточек; «Ещё» → полный топ того же периода.
6. Топ треков — `VirtualTrackList` (`listens` → `play_count`, метрика строки
   «N plays»).
7. Лайки — шелфы треков/релизов/артистов (пустые скрыты), «Ещё» → полный
   список этого вида.
8. Плейлисты — шелф (приватные API отдаёт только владельцу), «Ещё» → все.

Пустые состояния: нет прослушиваний вообще — «Пока нет прослушиваний» вместо
блоков 2–6 (лайки и плейлисты остаются); пустой период — сообщение в блоке
статистики. Неизвестный пользователь — «Пользователь не найден», сетевая
ошибка — «переподключение», как на других страницах; загрузка — скелетон.

**Полные списки** (`ProfileListPages.tsx`) — общий `FullListPage` (сетка +
бесконечная прокрутка, как `/shelf/:key`, см. docs/web-ui.md «Shelves: preview
size and «Ещё»»). «Ещё» у полки появляется только когда `total` больше, чем
полка показывает. Ссылки: `/u/:username/top/artists|releases?period=<период>`,
`/u/:username/likes/tracks|releases|artists`, `/u/:username/playlists`.
Заголовки — как у полки: «Топ артистов (30 дн.)» (период из URL, неизвестный
→ 30д), «Лайкнутые треки/релизы/артисты», «Плейлисты»; подзаголовок — логин.
Неизвестный `kind` — «Такого списка нет» без запроса к API. Правила доступа те
же, что у полок: чужие приватные плейлисты не попадают и в полный список.

**История** — `useUserListens`, группировка по локальным суткам
(`groupListensByDay`, `lib/listenHistory.ts`) поверх всех загруженных страниц —
сутки на стыке страниц остаются одной группой; заголовки «Сегодня» / «Вчера» /
«Понедельник, 28 сентября» (год — если не текущий). Подгрузка — по
`IntersectionObserver` у низа страницы и кнопкой «Показать ещё».

## Присутствие («сейчас слушает»)

Источник правды для «кто что слушает» — Navidrome (OpenSubsonic-расширение
`playbackReport`). Локально discocs помнит только последнее состояние плеера
на строке его собственной сессии (`playback_sessions.presence_*`, Ф6) — это
нужно «Слушать вместе», чтобы найти очередь и позицию ведущего.

### Запись: `POST /api/v1/playback/presence`

Тело `{track_id, state, position_ms, session_id?, queue_item_id?}`, `state` ∈
`starting | playing | paused | stopped`, `position_ms ≥ 0`, лишние поля → 422. Бэкенд
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

**Запись на сессию (Ф6).** Если передан `session_id`, до обращения к Navidrome
(и независимо от его исхода и от маппинга трека) на строку этой сессии пишутся
`presence_state`, `presence_position_ms`, `presence_at` (UTC, время сервера),
`presence_track_id` и `presence_queue_item_id`
(`Store.record_playback_presence`). Только своя сессия: чужой или
несуществующий `session_id` молча игнорируется (ответ тот же 200, строка не
меняется); `queue_item_id` не из этой сессии сохраняется как `NULL`.
`updated_at` и `current_*` сессии не трогаются — это поля протокола очереди
плеера. Отчёты идут только на переходах, так что запись дешёвая; в БД, а не в
памяти — переживает рестарт/деплой.

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
            "now_playing": {"track_id": 42, "title": "…", "artists": "A, B", "state": "playing",
                            "track": {"id": 42, "…": "полный TrackSummary; null, если трек не сопоставлен"}}}]}
```

- Все пользователи discocs (`users`), **включая вызывающего**; аватар — через
  `ensure_user_avatar` (как `/users`). Service-принципал → 403.
- Дублей логина по регистру нет. Легаси-строки `users`, созданные до
  регистронезависимого сопоставления, на старте сливаются в строку с меньшим
  id (`app/store/user_merge.py`). Новые дубли запрещает уникальный индекс
  `NOCASE`. Подробности — в `docs/data-model.md` (`users`).
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
не ретраится. Шелф «Люди» — см. [UI главной](#ui-главной-ф4).

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

## Слушать вместе (Ф6)

Разовое «подхватить» чужое воспроизведение (решения — `plans/social-spec.md`
§1.6). Зритель получает **свою новую** сессию, стартующую там, где ведущий
сейчас: тот же трек, та же позиция с поправкой на прошедшее время, дальше —
остаток очереди ведущего или радио по треку. Никакой синхронизации потом нет;
у ведущего ничего не меняется. Прослушивания и пропуски зрителя в этой сессии
считаются как обычно (это обычная сессия с обычными событиями).

### `POST /api/v1/users/{username}/listen-along`

Без тела. Роутер — `app/api/profile.py`, логика — `app/services/listen_along.py`.

1. `{username}` резолвится как у профиля (без учёта регистра): неизвестный →
   404 `not_found`, service-принципал → 403 `forbidden`, без сессии → 401.
   Сам себе → 400 `invalid_request`.
2. Что играет ведущий — `getNowPlaying` сервисным аккаунтом, тот же кэш и
   фильтр, что у `GET /social/people` (`now_playing_for`: `state` ∈
   `starting | playing` или без `state`; несколько плееров — наименьший
   `minutesAgo`). Не играет / Navidrome недоступен → 409 `not_playing`.
   Песня без трека discocs (`get_track_by_external_id`) → 409
   `track_not_mapped` (кнопки в UI в этом случае нет).
3. Сессия ведущего — `Store.for_user(host).playback_presence_snapshot(track_id)`:
   среди его незавершённых (`status != 'ended'`) сессий, у которых
   `presence_track_id` или `current_track_id` = этот трек, берётся с самым
   свежим `presence_at`; сессии без отчётов — после них, по `updated_at`.
4. Очередь новой сессии:

   | Случай | Очередь | `mode` | `strategy` |
   |---|---|---|---|
   | обычный источник (release/artist/label/playlist/search/manual/track/autoplay/listen_along) | остаток очереди ведущего в **порядке проигрывания** (`position`, т.е. с учётом shuffle), начиная с текущего элемента; `removed` пропускаются | `linear` | `queue` |
   | личный источник (`flow`, `generated_mix`) | `[трек]` — радио | `radio` | `personal_source` |
   | у ведущего нет сессии discocs (другой клиент Navidrome) | `[трек]` | `radio` | `no_session` |
   | трек не нашёлся в очереди сессии | `[трек]` | `radio` | `not_in_queue` |

   Текущий элемент — `presence_queue_item_id`, иначе `current_queue_item_id`
   сессии (если в нём этот трек), иначе первый элемент очереди с этим треком.
5. Позиция (`live_position_ms`): если последний отчёт сессии про этот трек —
   `starting`/`playing` → `presence_position_ms + (now − presence_at)`;
   `paused`/`stopped` → `presence_position_ms`. Иначе (нет сессии или отчёт
   про другой трек) — `positionMs` из записи Navidrome, если он есть, иначе 0.
   Позиция ≥ длительности трека считается устаревшей → 0.
6. Сессия зрителя: `source_type = "listen_along"`, `source_id` = id
   ведущего в `users`, `source_label` = его логин (UI форматирует «Вместе с
   <логин>» через i18n), `autoplay_enabled = true`, `shuffle_enabled = false`
   (очередь уже в нужном порядке), настройки — дефолтные
   (`playback_session_settings`). Конец очереди → обычный автоплей: для
   `listen_along` сиды — треки скопированной очереди (стратегия
   `listen_along_queue` в `app/autoplay.py`), `source_id` как трек не читается.

Ответ — тот же envelope, что у `POST /playback/sessions`, плюс точка старта:

```json
{"session": {"source_type": "listen_along", "source_id": 2, "source_label": "bob", "…": "…"},
 "queue": {"items": ["…"], "current_item": {"…": "…"}, "…": "…"},
 "start_track_id": 42,
 "start_queue_item_id": "uuid первого элемента новой очереди",
 "start_position_seconds": 73.4,
 "listen_along": {"host": "bob", "strategy": "queue"}}
```

Фронт: `listenAlong(username)` и тип `ListenAlongEnvelope` в
`ui/src/api/profile.ts`. Плеер шлёт в каждом presence-отчёте `session_id` и
`queue_item_id` своей сессии (`PresenceSnapshot.sessionId/queueItemId` в
`ui/src/lib/presence.ts`, заполняет `ui/src/store/presenceReporter.ts`);
`stopped` уходит с теми же id, что и остановленное воспроизведение. Старые
клиенты без `session_id` — сессия ведущего ищется по `current_track_id`, а
позиция берётся из Navidrome.

**UI (Ф7).** На чужом профиле, пока человек слушает трек из библиотеки
(`now_playing.track` не null), справа от строки «Сейчас слушает: …» — зелёная
текстовая кнопка «Слушать» (`data-testid="listen-along"`). Нажатие: `listenAlong`
→ `playFromEnvelope(envelope, start_track_id, { startPositionSeconds })` —
сразу остаток очереди и позиция ведущего. Ошибка (ведущий успел остановиться)
молча игнорируется, статус пропадёт со следующим опросом `usePeople`. На своём
профиле кнопки нет. В плеере подпись такой сессии (имя плейлиста по умолчанию
при «Сохранить очередь») — «Вместе с <логин>» (`player:listenAlongSource`).

## UI главной (Ф4)

**Шелф «Люди»** (`ui/src/components/media/PeopleShelf.tsx`) — первый шелф
главной, над «Для тебя». Обычный `Shelf` из карточек `MediaCard` типа `user`
(`personToCard`), порядок — как отдал `GET /social/people` (играющие первыми).
Данные — `usePeople` (опрос 15 с, пауза на скрытой вкладке).

- Пока первый запрос в полёте — `ShelfSkeleton` в одну строку (место шелфа
  зарезервировано: у пары-тройки пользователей список почти всегда непустой).
- Пустой список или первый запрос упал — шелф не рендерится вовсе. Если упал
  очередной опрос, на экране остаётся последний полученный список (react-query
  держит `data`), чтобы шелф не мигал.

**Карточка `user`** (`MediaCard`, `type="user"`, `id` = логин):

- круглый аватар — `avatarUrl(avatar)` (`ui/src/lib/avatars.ts`); неизвестный
  ключ → буква-заглушка, как у любой карточки без обложки;
- кнопки play нет никогда (даже если передан `onPlay`);
- клик/Enter → `/u/<логин>` (`encodeURIComponent`);
- строка 1 — логин; строка 2 — «Трек — Артист» только при `now_playing`, перед
  ней мигающая зелёная точка (`live`; `animate-ping` под `motion-safe:`, для
  скринридера — «Сейчас играет»). Не играет → только логин. Если Navidrome не
  отдал артиста — только название трека.

**Полка «История».** Элементы шелфа `history` (`_dashboard_history`,
`app/services/dashboard.py`; и на главной, и на `/shelf/history`) несут
`played_at` — `user_track_preferences.last_played_at` как есть (ISO-8601).
`shelfItemToCard` превращает его в `meta` карточки через
`formatRelativeTime(played_at, i18n.language)`; `MediaCard` дописывает `meta`
в строку подписи через « · » — «Артист · 3 ч назад». У остальных полок
`played_at` нет и подпись прежняя.

**«Мой профиль»** — кнопка в попапе `ProfileButton` (над «Открыть
настройки»), ведёт на `/u/<текущий логин>`; пока логин сессии неизвестен,
кнопки нет.
