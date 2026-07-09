# План: пользовательские плейлисты

Статус: **согласован, к реализации** (2026-07-08)

## Цель

Полноценные пользовательские плейлисты:

1. «Add to playlist» в контекстном меню трека → модалка выбора плейлиста
   (секция «Недавние» + полный список + кнопка «Новый»).
2. Модалка «Новый плейлист»: название + описание + косметический селект
   «Доступ» (без «Соавторы»; single-tenant — селект пока ни на что не
   влияет, значение сохраняется на вырост).
3. Кнопка в расширенном плеере над очередью — сохранить все треки текущей
   очереди (source + manual, **без** autoplay-пула) в плейлист через те же
   модалки.
4. Кнопка Save на странице микса → модалка «Новый плейлист» с предзаполненным
   названием/описанием из микса.
5. Обложки плейлистов — динамический коллаж 2×2 из первых 4 треков
   (аналогично mix-обложкам), недостающие тайлы — тёмно-серый фон.
6. Дашборд: шелф «Playlists» внизу + карточка «Playlists» в верхнем
   For You-шелфе.

## Что уже есть (переиспользуем)

| Что | Где |
|---|---|
| Таблицы `playlists` / `playlist_items` | `app/store/base.py:482-503` |
| Модели `Playlist`, `PlaylistItem` | `app/models.py:540-554` |
| `get_playlist`, `list_playlist_items`, `save_generated_mix_as_playlist` | `app/store/mixes.py` |
| Спец-плейлист likes (Navidrome starred) | `app/api/playlists.py`, `ui/src/api/playlists.ts` |
| Страница плейлиста (пока только likes) | `ui/src/pages/PlaylistPage.tsx`, роут `/playlists/:id` |
| Генератор коллаж-обложек миксов | `app/mix_covers.py` |
| Контекстное меню трека | `ui/src/components/media/TrackMenu.tsx` |
| Кнопка Save микса | `ui/src/pages/MixPage.tsx` (`POST /api/v1/mixes/{id}/save`) |
| Шелфы дашборда | `app/services/dashboard.py`, `app/api/dashboard.py`, `ui/.../ForYouShelf.tsx` |
| Модалки (Radix dialog) | `ui/src/components/ui/dialog.tsx` |

## 1. Модель данных

Миграция в `StoreBase` (`app/store/base.py`), стиль — как существующие
`ALTER TABLE ... ADD COLUMN`-миграции:

```sql
ALTER TABLE playlists ADD COLUMN description TEXT;        -- NULL ok
ALTER TABLE playlists ADD COLUMN cover_path TEXT;         -- сгенерированный коллаж
```

- `kind`: уже есть. Значения: `saved_mix` (существующее) + новое `manual`.
  Валидация через `PLAYLIST_KINDS = {"manual", "saved_mix"}` в `app/models.py`.
- `playlist_items` не меняем. Ограничение `UNIQUE (playlist_id, track_id)`
  оставляем — дубликаты в плейлисте запрещены, повторное добавление
  обрабатываем идемпотентно (см. открытые вопросы).
- Позиции: `PRIMARY KEY (playlist_id, position)`, добавление в конец =
  `MAX(position)+1`. Reorder — отдельной финальной фазой (см. §5.6 и §7),
  схема его уже поддерживает.
- `Playlist` dataclass дополняется `description`, `cover_path`;
  обновить `row_to_playlist` в `app/store/_helpers.py`.

## 2. Store (app/store/mixes.py — мixin уже отвечает за плейлисты)

Новые методы `MixesStoreMixin`:

- `create_playlist(*, title, description=None, kind="manual", source=None, track_ids=None) -> Playlist`
- `update_playlist(playlist_id, *, title=None, description=None) -> Playlist | None`
- `delete_playlist(playlist_id) -> bool` (каскад по FK); если на плейлист
  ссылается `generated_mixes.saved_playlist_id` — обнулить ссылку и вернуть
  миксу `status='active'`, чтобы кнопка Save снова работала (следующий
  refresh-цикл сам пометит его stale, если микс устарел)
- `list_playlists(*, limit, offset) -> list[Playlist]` — `ORDER BY updated_at DESC`
- `count_playlists() -> int`
- `add_playlist_tracks(playlist_id, track_ids) -> int` — append в конец,
  `INSERT OR IGNORE` (дубликаты молча пропускаются), возвращает число
  реально добавленных; обновляет `playlists.updated_at`
- `remove_playlist_tracks(playlist_id, track_ids) -> int` (батч) + переупаковка позиций
- `set_playlist_cover_path(playlist_id, cover_path)`
- `playlist_track_ids(playlist_id) -> list[int]` (для обложек и play)

Любая мутация состава треков трогает `updated_at` — на этом строится
секция «Недавние».

## 3. Обложки

Обобщить `app/mix_covers.py` → функция коллажа, пригодная и миксам, и
плейлистам (файл можно переименовать в `app/collage_covers.py` с
реэкспортом, либо добавить `generate_playlist_cover` рядом):

- Канвас 600×600, фон `#111518` (тёмно-серый — он и есть «пробел» для
  недостающих тайлов, текущее поведение уже совпадает с требованием).
- Плейлисты: тайлы = обложки **первых 4 треков по позиции**, без дедупа по
  cover_art_id (в отличие от миксов) — «4 первых трека сеткой».
- Файл: `data/playlist_covers/{id}.jpg`, путь в `playlists.cover_path`.
- Регенерация: после любого изменения первых 4 позиций (создание,
  add/remove). Дёшево — просто пересобрать после мутации синхронно
  (Navidrome-запросы 4 шт., как у миксов); если станет заметно — вынести в
  background thread по образцу mix-генерации.
- Отдача: `GET /api/v1/playlists/{id}/cover` (по образцу mix cover).
- Fallback в сериализаторе: cover_path → обложка первого трека → placeholder
  (тот же паттерн, что `_generated_mix_artwork`).

## 4. API (app/api/playlists.py, prefix /api/v1)

Спец-роуты `likes` остаются как есть (id="likes" — строка, не пересекается
с числовыми id). Новые роуты:

| Метод | Путь | Тело/ответ |
|---|---|---|
| GET | `/playlists` | `{items: PlaylistSummary[], total, limit, offset, next_offset}`; сортировка updated_at DESC |
| POST | `/playlists` | `{title, description?, track_ids?}` → PlaylistSummary (201) |
| GET | `/playlists/{id}` | PlaylistDetail (summary + `tracks: TrackSummary[]`) |
| PATCH | `/playlists/{id}` | `{title?, description?}` |
| DELETE | `/playlists/{id}` | 204 |
| POST | `/playlists/{id}/tracks` | `{track_ids: [..]}` → `{added: n, track_count}` |
| POST | `/playlists/{id}/tracks/remove` | `{track_ids: [..]}` → `{removed: n, track_count}` — батч для мультивыделения |
| POST | `/playlists/{id}/play` | PlaybackEnvelope — сессия `source_type="playlist"`, как у likes |
| GET | `/playlists/{id}/cover` | JPEG |

`PlaylistSummary`: `{id, title, description, kind, track_count, artwork, created_at, updated_at}`.
Pydantic-схемы — в `app/schemas/requests.py` / `responses.py`,
сериализатор — новый `app/serializers/playlists.py`.

Save микса: расширить `POST /api/v1/mixes/{id}/save` опциональным телом
`{title?, description?}` — `save_generated_mix_as_playlist` уже создаёт
плейлист и линкует `saved_playlist_id`, добавить прокидывание
title/description и генерацию обложки. Ответ дополнить `saved_playlist_id`.

## 5. UI (ui/src — новый интерфейс)

### 5.1 API-слой

`ui/src/api/playlists.ts` — дополнить: `fetchPlaylists`, `createPlaylist`,
`fetchPlaylist(id)`, `updatePlaylist`, `deletePlaylist`, `addTracksToPlaylist`,
`removePlaylistTrack`, `playPlaylist`. Типы в `api/types.ts`
(`PlaylistSummary`, `PlaylistDetail`). Query-ключ `["playlists"]` +
инвалидация после мутаций (tanstack-query, как в MixPage).

### 5.2 Модалки (ui/src/components/playlists/)

- **`AddToPlaylistDialog.tsx`** — по скрину:
  - секция «Recent» — горизонтальный ряд из ~4 последних (updated_at DESC)
    с коллаж-обложками;
  - секция «All playlists» — вертикальный список (обложка, название,
    N tracks), скролл;
  - кнопка «+ New» (внизу) → открывает CreatePlaylistDialog;
  - клик по плейлисту → `addTracksToPlaylist(id, trackIds)` → toast/закрытие.
  - Пропсы: `trackIds: number[]`, `open`, `onOpenChange` — одна модалка
    обслуживает и один трек, и очередь.
- **`CreatePlaylistDialog.tsx`** — поля «Name», «Description», косметический
  селект «Visibility» (Public/Private; значение пишем в `source_json` как
  `{"visibility": ...}`, поведения пока не имеет — задел под multi-user).
  Без «Соавторы». Кнопки Cancel/Create. Пропсы: `defaultTitle?`,
  `defaultDescription?`, `trackIds?`, `onCreate?(playlist)` — для сценария
  сохранения микса. Тот же компонент в режиме edit (пропс `playlist?`) —
  для Rename/Description со страницы плейлиста.

Состояние модалок держит вызывающий компонент (или маленький zustand-слайс в
`uiStore`, если пробрасывать неудобно из TrackMenu/ExpandedPlayer/MixPage —
решить по месту; предпочтение — общий store-слайс `playlistDialog`, чтобы
модалка монтировалась один раз в AppShell).

### 5.3 Точки входа

- **TrackMenu** (`components/media/TrackMenu.tsx`): пункт «Add to playlist»
  (иконка ListPlus) → `AddToPlaylistDialog` с `[track.id]`.
- **ExpandedPlayer** (`components/player/ExpandedPlayer.tsx`): иконка-кнопка
  (ListPlus, как на скрине) в хедере панели очереди (рядом с Autoplay-
  тумблером) → `AddToPlaylistDialog` с `queue.items.map(i => i.track_id)`
  — это source+manual элементы, `autoplay_pool` не входит. Дедуп id на
  клиенте (played-треки в items тоже входят — это и есть «текущий активный
  плейлист»).
- **MixPage**: кнопка Save вместо прямого `saveMix(id)` открывает
  `CreatePlaylistDialog` с `defaultTitle=mix.title`,
  `defaultDescription=mix.subtitle`; Create → `saveMix(id, {title, description})`
  → инвалидация mix + playlists.

### 5.4 Страница плейлиста

`PlaylistPage.tsx`: ветвление — `id === "likes"` как сейчас, числовой id →
`fetchPlaylist(id)`. Макет — по образцу MixPage (обложка слева, заголовок,
кнопки в ряд):

- обложка-коллаж (`ArtworkImage` с `/api/v1/playlists/{id}/cover`),
  description под заголовком;
- кнопки: **Play** (`playPlaylist(id)`), **Edit** (CreatePlaylistDialog в
  режиме edit: название/описание), **Delete** (подтверждение, после —
  navigate на дашборд);
- **удаление треков — режим выделения**: при наведении на строку трека
  вместо номера/обложки показывается чекбокс (паттерн play-иконки в
  TrackRow); отметка первого чекбокса включает selection-режим — чекбоксы
  видны на всех строках, вверху списка появляется панель
  «N selected · [корзинка] · Cancel»; корзинка → батч
  `POST /playlists/{id}/tracks/remove` → инвалидация. Выделение — локальный
  `Set<number>` в состоянии страницы; переключается **кликом по самому
  чекбоксу**, клик по остальной части строки сохраняет обычное поведение
  (воспроизведение). Только для редактируемых плейлистов (не likes).

### 5.5 Дашборд

- Backend: `_dashboard_playlists()` в `app/services/dashboard.py` +
  ключ `playlists` в `dashboard_shelf_response` (title «Playlists»),
  элементы: `entity_type="playlist"`, `action.target="/playlists/{id}"`,
  `play_action={type:"post", endpoint:"/api/v1/playlists/{id}/play"}`.
  Ключ добавить в `shelf_keys` в `app/api/dashboard.py` **последним** (внизу).
- `ShelfItemType` в `api/types.ts` дополнить `"playlist"`.
- `ForYouShelf.tsx`: карточка «Playlists» (иконка ListMusic,
  `type:"shelf"` → `/shelf/playlists`).
- `ShelfPage` работает через тот же `/dashboard/shelves/{key}` — получит
  playlists бесплатно.

### 5.6 Reorder (финальная фаза, опционально)

Делаем после приёмки основного функционала, если не переусложняет:

- Store: `reorder_playlist_tracks(playlist_id, track_ids)` — полная замена
  порядка (валидация: тот же набор id, что в плейлисте), bump `updated_at`,
  регенерация обложки при смене первых 4.
- API: `POST /playlists/{id}/tracks/reorder` `{track_ids: [..]}` → 200/409.
- UI: drag-n-drop строк на странице плейлиста (dnd-kit — лёгкий, без
  зависимостей на react-dnd/redux; оценить совместимость с виртуализацией
  VirtualTrackList — если конфликтует, drag только в невиртуализированном
  режиме или отложить).

## 6. Тесты

Backend (pytest, in-memory SQLite из conftest):

- store: create/update/delete, append-позиции, `INSERT OR IGNORE` дубликата,
  remove + переупаковка позиций, updated_at bump, round-trip cover_path;
- api: CRUD-happy-path + 404, add tracks (включая дубликаты и несуществующий
  track_id → 4xx), батч remove, play (envelope, пустой плейлист → 409),
  likes не сломан; delete плейлиста сохранённого микса → микс снова active;
- mix save с title/description → плейлист создан, `saved_playlist_id` линк;
- cover: генерация коллажа с 4/2/0 треками (Pillow есть в dev-окружении;
  Navidrome-клиент замокан), серые тайлы при <4.

Frontend (vitest): AddToPlaylistDialog (рендер списка, клик → мутация),
CreatePlaylistDialog (сабмит с предзаполнением), TrackMenu содержит пункт.

Прогон — в Jenkins CI, локально не гоняем (см. CLAUDE.md).

## 7. Порядок работ

Работаем фазами: **каждая фаза = код + тесты → коммит → push (GitHub +
Gitea) → проверка CI в Jenkins → подтверждение Сани → следующая фаза.**

1. **Фаза 1 — store:** схема (description, cover_path, PLAYLIST_KINDS),
   store-методы, row_to_playlist, тесты store. ✅
2. **Фаза 2 — обложки:** обобщение mix_covers под плейлисты + тесты. ✅
3. **Фаза 3 — API:** роуты, сериализатор, схемы, расширение mix save,
   тесты API. ✅ (билд #101)
4. **Фаза 4 — UI, модалки:** api-слой, AddToPlaylistDialog,
   CreatePlaylistDialog, пункт в TrackMenu + vitest. ✅ (билд #103)
5. **Фаза 5 — UI, интеграции:** кнопка в ExpandedPlayer, save-флоу MixPage. ✅ (билд #105)
6. **Фаза 6 — UI, страницы:** PlaylistPage generic (Play/Edit/Delete,
   selection-режим), шелф Playlists + карточка For You. ✅
7. **Фаза 7 — reorder** (§5.6, опционально по итогам приёмки) +
   документация `docs/`.

## Принятые решения (обсуждено 2026-07-08)

1. **Поле «Доступ»** — косметический селект в модалке, значение в
   `source_json.visibility`, поведения не имеет (задел под multi-user).
2. **Дубликаты треков** — идемпотентное добавление (`INSERT OR IGNORE`),
   в ответе `added: 0`, в UI toast «Already in playlist».
3. **Save микса** — расширяем существующий `/mixes/{id}/save` телом
   `{title?, description?}`, линк mix↔playlist и статус `saved` сохраняются.
4. **«Недавние» в модалке** — `updated_at DESC`, 4 шт.
5. **Кнопка в плеере** — сохраняем `queue.items` целиком (played + current
   + upcoming + manual), без `autoplay_pool`.
6. **Likes** — остаётся отдельным спец-плейлистом, в общий список, модалку
   и шелф Playlists не попадает.
7. **Редактирование** — страница плейлиста по образцу MixPage: Play / Edit
   (название+описание) / Delete; удаление треков — чекбоксы при наведении,
   selection-режим с панелью и корзинкой (см. §5.4).
