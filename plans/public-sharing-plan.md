# План: публичный шаринг треков и релизов

Статус: **согласован, к реализации** (2026-07-19)

## Цель

Добавить в основной UI возможность создать публичную ссылку на:

1. один трек;
2. релиз (альбом, EP, сингл и другие типы релизов) как упорядоченный плейлист.

Получатель ссылки не авторизуется в discocs и видит отдельную адаптивную
страницу прослушивания на основе визуального представления расширенного
плеера. Публичная ссылка не должна открывать доступ к каталогу, поиску,
рекомендациям, персональным данным, настройкам и обычным API приложения.

Ключевая модель безопасности: ссылка является **capability URL**. Любой, кто
знает её секретный токен, может слушать только явно включённые в неё треки до
истечения срока или отзыва ссылки.

## Не входит в первую версию

- шаринг артиста, пользовательского плейлиста, Flow или сгенерированного микса;
- пароль поверх публичной ссылки;
- публичные комментарии, лайки, дизлайки и scrobbling;
- запись гостевого прослушивания в предпочтения владельца;
- гарантированный запрет скачивания аудио;
- серверный транскодинг специально для публичных ссылок;
- Open Graph-превью с динамическими метатегами;
- одноразовые ссылки или лимит по числу прослушиваний.

Запрет скачивания не является достижимой границей безопасности: если браузер
может воспроизвести аудио, получатель может сохранить переданные байты. В UI
не будет кнопки Download, но это только ограничение интерфейса.

## Текущее состояние и точки интеграции

- Backend auth-gate находится в `app/api/auth_middleware.py`. При включённой
  авторизации сейчас публичны только `/health` и auth handshake endpoints.
- Новый UI маршрутизируется в `ui/src/router.tsx`; все рабочие страницы, кроме
  `/login`, находятся внутри `RequireAuth` и `AppShell`.
- `ui/src/components/player/ExpandedPlayer.tsx` содержит нужное адаптивное
  представление, но зависит от персональной playback-сессии, очереди, лайков,
  событий, autoplay и закрытых страниц каталога.
- `GET|HEAD /api/v1/tracks/{track_id}/audio` в `app/api/tracks.py` уже умеет
  отдавать локальный файл и проксировать Navidrome с поддержкой Range/HEAD.
- Публичный nginx проксирует `/api/*` в backend и отдаёт остальные маршруты
  через SPA fallback (`deploy/nginx/default.conf.template`). Операционные
  endpoint'ы закрыты отдельными location-блоками.
- Пользовательская идентичность уже доступна через `request.state.user_id` и
  user-scoped `Store`.

## Принятые архитектурные решения

1. Использовать собственные непрозрачные хранимые токены, не JWT и не
   stateless signed payload.
2. Токен генерируется через `secrets.token_urlsafe(32)`; в SQLite хранится
   только `SHA-256` токена.
3. Публичная ссылка имеет вид `/share/{token}`.
4. Состав релиза фиксируется в `share_items` при создании ссылки. Позднее
   добавленные в релиз треки автоматически публичными не становятся.
5. Метаданные треков читаются актуальные; snapshot касается членства и порядка.
6. Публичные запросы используют отдельный узкий API. Обычные track/release/
   playback endpoint'ы публичными не становятся.
7. В audio URL используется позиция элемента внутри share, а не произвольный
   `track_id`. Принадлежность проверяет backend.
8. Для Navidrome-аудио публичный endpoint использует серверный service account.
   Учётные данные создателя не сохраняются в share и не выдаются гостю.
9. Публичный плеер имеет локальное клиентское состояние и не создаёт строки в
   `playback_sessions`, `queue_items` или `playback_events`.
10. Sharing включён по умолчанию и отключается явным feature flag.
11. Право создавать публичные ссылки есть у любого обычного пользователя
    с валидной сессией; service principal этого права не имеет.
12. Отозванная, истёкшая и несуществующая ссылка снаружи выглядят одинаково.

## Модель данных

### `shares`

```sql
CREATE TABLE IF NOT EXISTS shares (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shares_owner_created
    ON shares(owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shares_expires
    ON shares(expires_at);
```

Инварианты:

- `id` — внутренний UUID; он используется в авторизованном management API.
- `source_type` первой версии: `track | release`.
- `source_id` нужен для отображения источника владельцу, но публичный доступ
  определяется только строками `share_items`.
- `token_prefix` содержит короткий безопасный префикс для списка ссылок; полный
  токен после создания восстановить из БД нельзя.
- `expires_at IS NULL` означает осознанно созданную бессрочную ссылку.
- отзыв выполняется через `revoked_at`, а не физическое удаление: это сохраняет
  аудит. В management UI можно отдельно предусмотреть окончательное удаление
  уже отозванных записей позднее.

### `share_items`

```sql
CREATE TABLE IF NOT EXISTS share_items (
    share_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (share_id, position),
    UNIQUE (share_id, track_id),
    FOREIGN KEY (share_id) REFERENCES shares(id) ON DELETE CASCADE,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_share_items_track
    ON share_items(track_id);
```

Для track-share создаётся один элемент с `position = 0`. Для release-share в
одной транзакции копируются доступные треки из `release_tracks` в порядке
`position`. Пустой источник не разрешается шарить.

Если трек удалён из каталога, соответствующий `share_items` удаляется по FK.
Если файл только помечен missing, metadata endpoint оставляет элемент в списке
с признаком недоступности, а audio endpoint возвращает безопасную ошибку.

## Store

Добавить `app/store/shares.py` и включить `SharesStoreMixin` в сборный `Store`.
Методы не размещать непосредственно в `app/store/__init__.py`.

Минимальный контракт:

- `create_track_share(owner_user_id, track_id, expires_at, title=None)`;
- `create_release_share(owner_user_id, release_id, expires_at, title=None)`;
- `list_user_shares(owner_user_id, include_revoked=False)`;
- `get_user_share(owner_user_id, share_id)`;
- `update_user_share(owner_user_id, share_id, *, title, expires_at)`;
- `revoke_user_share(owner_user_id, share_id, revoked_at)`;
- `resolve_active_share(token_hash, now)`;
- `list_share_items(share_id)`;
- `get_active_share_item(token_hash, position, now)`;
- `touch_share_access(share_id, now)` с ограничением частоты записи.

Создание share, проверка источника и snapshot `share_items` выполняются в одной
SQLite-транзакции. Management-методы всегда фильтруют по `owner_user_id`.

## Конфигурация

Добавить серверные настройки:

| Переменная | Default | Назначение |
|---|---:|---|
| `DISCOCS_SHARING_ENABLED` | `true` | Полностью отключает public sharing при `false` |
| `DISCOCS_SHARE_DEFAULT_TTL_HOURS` | `168` | Default 7 дней |
| `DISCOCS_SHARE_MAX_TTL_HOURS` | `8760` | Максимальный срок 1 год |

Бессрочная ссылка передаётся явным `expires_at: null` и требует отдельного
подтверждения в UI. Если решим запретить бессрочные ссылки в production,
backend должен делать это конфигурационно, а не только скрывать вариант в UI.

При `DISCOCS_SHARING_ENABLED=false`:

- management API отвечает `404` или стабильной feature-disabled ошибкой;
- публичные ссылки отвечают одинаковым `404 Share unavailable`;
- frontend не показывает действия Share.

Любой обычный авторизованный пользователь может создавать share. UI
скрывает действия при выключенном sharing; service principal создавать share не может.

## Авторизованный management API

### Создание

```http
POST /api/v1/shares
Content-Type: application/json

{
  "source_type": "release",
  "source_id": 42,
  "title": "Послушай этот альбом",
  "expires_at": "2026-07-26T12:00:00+03:00"
}
```

Ответ `201`:

```json
{
  "share": {
    "id": "internal-uuid",
    "source_type": "release",
    "source_id": 42,
    "title": "Послушай этот альбом",
    "item_count": 10,
    "created_at": "...",
    "expires_at": "...",
    "revoked_at": null,
    "token_prefix": "NDxb4"
  },
  "url": "https://public-host/share/full-secret-token"
}
```

Полный URL возвращается только в ответе создания. Повторно получить его через
list/get нельзя; вместо этого пользователь создаёт новую ссылку.

### Управление

```text
GET    /api/v1/shares
GET    /api/v1/shares/{share_id}
PATCH  /api/v1/shares/{share_id}
DELETE /api/v1/shares/{share_id}   # идемпотентный revoke
```

Требования:

- пользователь видит и изменяет только собственные shares;
- чужой `share_id` возвращает `404`, не `403`;
- source type/id после создания неизменяемы;
- PATCH меняет только название и срок;
- продление уже истёкшей ссылки допускается только явным PATCH владельца;
- revoked share нельзя оживить PATCH: требуется создать новую ссылку;
- абсолютный публичный URL строится по доверенным forwarded headers только от
  настроенного proxy либо через отдельный canonical public base URL.

## Публичный API

Публичными являются только следующие безопасные методы и пути:

```text
GET  /api/v1/public/shares/{token}
GET  /api/v1/public/shares/{token}/cover
GET  /api/v1/public/shares/{token}/items/{position}/audio
HEAD /api/v1/public/shares/{token}/items/{position}/audio
```

Metadata response не должен содержать:

- внутренний `share.id`;
- `owner_user_id` или username владельца;
- локальный путь файла;
- Navidrome item ID;
- обычный внутренний `track_id`;
- ссылки на закрытые API и страницы каталога;
- анализ, предпочтения и персональные поля.

Пример безопасного ответа:

```json
{
  "kind": "release",
  "title": "Album title",
  "subtitle": "Artist",
  "expires_at": "...",
  "artwork_url": "/api/v1/public/shares/{token}/cover",
  "items": [
    {
      "position": 0,
      "title": "Track title",
      "artist": "Artist",
      "duration": 245.4,
      "available": true,
      "audio_url": "/api/v1/public/shares/{token}/items/0/audio"
    }
  ]
}
```

Для неизвестного, истёкшего и отозванного токена все публичные endpoint'ы
возвращают одинаковые status/body. Формат токена проверяется до обращения к БД;
слишком длинные и некорректные значения отклоняются.

## Изменение auth middleware

Не добавлять wildcard в существующий `PUBLIC_PATHS`. Вынести отдельную
проверку `is_public_share_request(request)` со строгими условиями:

- feature flag включён;
- путь соответствует одному из публичных share-route;
- метод только `GET` или `HEAD`;
- `OPTIONS` остаётся в текущей CORS-модели и не превращает API в cross-origin;
- никакие `/api/v1/shares` management-route не попадают под исключение.

Тесты middleware должны использовать негативные соседние пути, например:

```text
/api/v1/public/sharesx/...
/api/v1/public/shares/{token}/../../settings
/api/v1/shares/...
POST /api/v1/public/shares/{token}
```

## Выдача аудио

Общую механику локального файла и Navidrome-потока вынести из route handler в
сервис, принимающий уже разрешённый `Track`. Он не должен сам решать, имеет ли
пользователь доступ: обычный и публичный route выполняют свои проверки до
вызова сервиса.

Публичный audio flow:

1. проверить синтаксис и hash токена;
2. одним Store-вызовом найти активный share item по `(token_hash, position)`;
3. не принимать путь, Navidrome ID или track ID от клиента;
4. проверить наличие/доступность трека;
5. для локального трека вернуть `FileResponse` с Range/HEAD;
6. для Navidrome использовать серверные credentials и существующий streaming
   proxy с передачей `Range`;
7. вернуть `Content-Disposition: inline`, не attachment;
8. не менять `missing_at` и другие глобальные данные на основании единичного
   подозрительного публичного запроса без подтверждённой ошибки источника.

Ответы metadata, cover и audio получают:

```text
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Robots-Tag: noindex, nofollow, noarchive
```

Существующий `Range`, `Content-Range`, `Accept-Ranges`, `Content-Length`, ETag и
HEAD-контракт обычного плеера не должны регрессировать.

Первая версия отдаёт оригинальный формат. План транскодинга и браузерного
prefetch из `plans/browser-audio-buffering-transcoding-plan.md` не должен
автоматически применять персональные настройки владельца к гостю. Отдельный
фиксированный public playback profile можно добавить позднее.

## Rate limiting и защита ресурсов

Capability token защищает от чтения каталога, но не от расходования трафика
тем, кому ссылка уже известна. Нужны отдельные ограничения:

- rate limit metadata/cover по IP и token hash;
- rate limit создания ссылок по пользователю;
- лимит одновременных audio streams по IP и share;
- глобальный предел параллельных публичных потоков;
- корректное освобождение счётчика в `finally` после завершения/обрыва stream;
- разумные proxy/backend timeouts;
- без буферизации полного файла в памяти backend.

Первая реализация может использовать in-memory limiter, как login throttling,
поскольку production сейчас имеет один backend process. Ограничение и этот
deployment-инвариант документируются. При горизонтальном масштабировании
limiter потребуется вынести из процесса.

## Защита токена от утечек

- никогда не писать полный токен в application log, exception или audit row;
- в логах использовать `share.id` либо `token_prefix`;
- не включать token в telemetry и frontend logger payload;
- изменить nginx access log так, чтобы секретные сегменты `/share/{token}` и
  `/api/v1/public/shares/{token}/...` маскировались;
- не подключать стороннюю аналитику/скрипты к public share page;
- public page задаёт `Referrer-Policy: no-referrer`;
- документация и UI прямо говорят: получатель может переслать ссылку дальше.

Возможное усиление после MVP: одноразовый обмен URL-токена на короткоживущий
HttpOnly guest grant с редиректом на URL без секрета. Это отдельная фаза, потому
что необходимо корректно поддержать несколько share во вкладках, Range media
requests и социальные preview-fetcher'ы.

## Новый UI

### Маршрутизация

Добавить `/share/:token` **вне** `RequireAuth` и `AppShell`. Страница не делает
запрос `/api/v1/auth/session` и не перенаправляет гостя на `/login`.

Состояния страницы:

- загрузка;
- готов трек;
- готов релиз/очередь;
- ссылка недоступна;
- временная ошибка сети с Retry;
- отдельный элемент очереди недоступен, но остальные воспроизводятся.

### Переиспользование расширенного плеера

Не импортировать `ExpandedPlayer` целиком. Выделить презентационные части без
зависимости от Zustand store и закрытых API, например:

- artwork/backdrop;
- динамический цветовой акцент из обложки;
- информация о текущем треке;
- progress/seek;
- playback controls;
- queue panel и responsive mobile tabs.

Обычный `ExpandedPlayer` и новый `SharedPlayerPage` собирают их со своими
actions/state. Рефакторинг должен сохранить поведение существующего плеера.

Публичный плеер поддерживает:

- play/pause;
- seek и отображение времени;
- громкость/mute;
- previous/next;
- выбор трека в очереди;
- мини-обложки треков в очереди;
- repeat one;
- опциональный локальный shuffle для релиза;
- Media Session metadata и media keys;
- мобильный и desktop layout.

Публичный плеер не показывает:

- like/dislike;
- TrackMenu;
- download;
- add/save to playlist;
- autoplay и рекомендации;
- ссылки на закрытые artist/release pages;
- персональную историю и статус владельца.

Не использовать основной `playerStore`: share playback не должен переживать
logout/login, восстанавливать чужую playback-сессию или влиять на очередь
авторизованного приложения. Допустимо переиспользовать `AudioEngine` через
отдельный локальный hook/store с обязательным cleanup при unmount.

### Создание ссылки

Добавить действие Share:

- для трека — в `TrackMenu`;
- для релиза — в `CollectionHeader` страницы релиза.

Диалог:

- заголовок/описание ссылки (optional);
- срок: 1 день, 7 дней по умолчанию, 30 дней, 1 год, без срока;
- предупреждение, что любой получатель сможет слушать и пересылать ссылку;
- Create → показать URL → Copy;
- после закрытия полный URL повторно не показывается.

Действия скрыты, если feature выключен. Backend повторно проверяет
feature flag и наличие обычной пользовательской сессии.

### Управление ссылками

Добавить авторизованный раздел «Shared links» в Settings либо отдельную
страницу. Для каждой записи показывать:

- источник и обложку;
- тип и число треков;
- создана / истекает;
- active / expired / revoked;
- access count и last accessed без IP получателей;
- изменить название/срок;
- revoke;
- создать новую ссылку взамен старой.

## Public nginx и браузерные заголовки

- SPA fallback уже позволяет открыть `/share/{token}`, но route должен быть
  добавлен во frontend router.
- `/api/v1/public/shares/*` проходит через общий API proxy; не создавать более
  широкое публичное location-исключение.
- Входящий `X-Discocs-Service-Token` по-прежнему принудительно очищается.
- Добавить маскирование share token в access log.
- Проверить, что CSP разрешает только same-origin media/image/connect и не
  требует ослабления `script-src`.
- Не добавлять wildcard CORS.
- Не менять закрытие `/admin`, `/api/map`, workers, settings и jobs.

Динамические Open Graph-теги не работают через единый статический `index.html`.
Если preview в мессенджерах понадобится, добавить отдельный server-rendered
preview route позже; он должен применять те же проверки токена и не встраивать
секрет в внешние URL ресурсов.

## Тестовый план

Локально тесты не запускать: по правилам проекта проверки выполняются Jenkins.
Новые тесты обязательны и должны падать при удалении или инверсии проверяемой
логики.

### Store/unit

- токен имеет достаточную энтропию, полный token не хранится в БД;
- создание track share и round-trip;
- release snapshot сохраняет порядок и не меняется после изменения релиза;
- пустой/несуществующий источник отклоняется;
- lookup активного token;
- истёкший и revoked token не разрешаются;
- owner A не видит/не меняет shares owner B;
- чужой source/share ID не позволяет обойти owner scope;
- удаление track корректно влияет на share items;
- изменение TTL и идемпотентный revoke;
- `token_hash` и `(share_id, position)` uniqueness.

### Backend API/auth

- auth требуется для create/list/patch/delete;
- service token не может создать share;
- любой обычный авторизованный пользователь может создать share;
- sharing disabled закрывает management и public routes;
- разрешены только точные публичные GET/HEAD routes;
- POST/PUT/PATCH/DELETE на public path не обходят middleware;
- соседние и path traversal-подобные URL не считаются публичными;
- invalid/expired/revoked возвращают одинаковый публичный ответ;
- metadata не содержит path, owner, internal IDs и Navidrome IDs;
- аудио доступно только по позиции, принадлежащей конкретному share;
- токен одного share нельзя использовать для трека другого;
- локальный audio GET/HEAD/Range;
- Navidrome GET/HEAD/Range и upstream error mapping;
- public endpoint использует service credentials, не credentials владельца;
- response security/cache headers;
- токен не появляется в application log;
- лимиты освобождаются после normal close и client disconnect.

### Frontend

- `/share/:token` не вызывает auth/session и не редиректит на login;
- loading/error/expired states;
- track share воспроизводится как очередь из одного элемента;
- release share сохраняет порядок;
- next/previous/select/seek/repeat;
- недоступный item пропускается или показывает понятную ошибку;
- unmount очищает AudioEngine и object URLs;
- guest page не рендерит like, dislike, download, save и закрытые ссылки;
- адаптивные player/queue tabs;
- Create Share dialog: presets, explicit no-expiry confirmation, copy state;
- feature flag скрывает Share action;
- management list и revoke confirmation;
- рефакторинг общих player-компонентов не меняет обычный ExpandedPlayer.

### Nginx/security regression

- публичная ссылка открывается без session cookie;
- обычный API без cookie остаётся `401`;
- operational routes снаружи остаются `404`;
- spoofed service-token header очищается;
- токен маскируется в access log;
- CSP, referrer policy, robots и no-store присутствуют;
- cross-origin запросы не получают CORS-доступ.

## Документация

При реализации добавить `docs/sharing.md`:

- модель capability URL и ограничения;
- конфигурация и модель доступа;
- создание, срок и отзыв;
- публичные маршруты и reverse proxy;
- что отсутствие Download не предотвращает сохранение аудио;
- rate limit/deployment assumptions;
- troubleshooting локальных и Navidrome-треков.

Обновить:

- `docs/auth.md` — строгое публичное исключение и security model;
- `docs/architecture.md` — shares/share_items и публичный player flow;
- `docs/data-model.md` — новые таблицы и owner scope;
- `docs/cicd.md` или deployment docs, если меняется nginx/config rollout;
- `.env.example` production deployment — новые переменные.

## Этапы реализации

### Этап 1. Домен и Store

- модели/config;
- таблицы и индексы;
- `SharesStoreMixin`;
- token generation/hash;
- snapshot track/release;
- owner-scope, TTL и revoke;
- backend unit tests.

Критерий: Store полностью управляет жизненным циклом ссылки и не хранит секрет.

### Этап 2. Management API

- schemas и serializers;
- create/list/get/patch/revoke;
- feature flag и проверка обычной user session;
- построение canonical URL;
- API/security tests.

Критерий: авторизованный пользователь может создать и отозвать
ссылку; другой пользователь не видит её.

### Этап 3. Публичный metadata/cover/audio API

- точечное исключение auth middleware;
- безопасный public serializer;
- общий audio response service;
- local/Navidrome Range и HEAD;
- response headers;
- rate/concurrency limits;
- backend и middleware tests.

Критерий: гость получает только snapshot-контент конкретной активной ссылки;
все остальные API остаются закрыты.

### Этап 4. Публичный плеер

- выделение презентационных частей ExpandedPlayer;
- route вне RequireAuth;
- локальный share playback state;
- очередь релиза и Media Session;
- responsive/error states;
- frontend tests.

Критерий: трек и релиз полностью прослушиваются на mobile и desktop без login
и без побочных записей в персональные данные.

### Этап 5. Создание и управление ссылками в UI

- Share action у трека и релиза;
- диалог TTL/create/copy;
- management list;
- изменение TTL/title и revoke;
- i18n RU/EN;
- frontend tests.

Критерий: полный пользовательский lifecycle доступен без ручных API-вызовов.

### Этап 6. Hardening, документация и выпуск

- nginx masking и security regression;
- аудит публичного API по default-deny;
- документация/config examples;
- единый commit и push после завершения всей задачи;
- дождаться нового Jenkins build и проверить результат по инструкции проекта;
- при неуспехе сначала testReport/wfapi/Trivy, Sonar — через SonarQube MCP.

Критерий: Jenkins успешен, публичные границы проверены, документация совпадает
с фактической конфигурацией production.

## Definition of Done

- Авторизованный пользователь создаёт отдельную отзывную ссылку на трек или релиз.
- В SQLite отсутствует полный секретный token.
- Гость без cookie слушает только элементы конкретной активной ссылки.
- Релиз отображается и воспроизводится как стабильный упорядоченный плейлист.
- Ссылка перестаёт работать сразу после revoke или TTL.
- Публичная страница адаптивна и не использует персональные playback API.
- Обычные API, UI и operational routes остаются закрыты текущей авторизацией.
- Токены не попадают в application/nginx logs и сторонние referrer'ы.
- Local и Navidrome audio сохраняют HEAD/Range/seek.
- Все новые backend/frontend/security сценарии покрыты тестами.
- Поведение и эксплуатация описаны в `docs/`.
- Изменения закоммичены и отправлены в `origin main` и `gitea main` один раз
  после полного завершения реализации; соответствующий Jenkins build успешен.
