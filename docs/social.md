# Социальные функции

Видеть, что слушают другие пользователи discocs, и иметь страницу профиля со
статистикой, открытую остальным залогиненным. Пользователей несколько и все
свои: всё открыто всем залогиненным, тумблеров приватности нет — кроме
приватных плейлистов, которые видит только владелец. Полная спека и фазы —
[`plans/social-spec.md`](../plans/social-spec.md).

Статус: **Ф1 (фундамент, backend)** — таблица `listens`, встроенные аватары,
слой доступа к профилю. Присутствие «сейчас слушает», API профиля и UI — Ф2–Ф5.

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

Неизвестный username → `ProfileNotFoundError` (404 в API Ф3), service-принципал
→ `ProfileViewerRequiredError`. Никогда не отдаются: flow-профиль,
сессии/очередь, `user_settings` (кроме аватара), Navidrome-креды,
preference-score/дизлайки.
