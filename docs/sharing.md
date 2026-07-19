# Публичный шаринг

Discocs может создавать отзывные публичные ссылки на один трек или релиз.
Получателю не нужна учётная запись: `/share/{token}` открывает отдельный
адаптивный плеер, а релиз отображается как упорядоченная очередь.

## Модель безопасности

Публичная ссылка — capability URL: знание полного токена даёт доступ только к
snapshot-набору треков этой ссылки. Токен генерируется из 256 бит случайности;
в SQLite хранится только его SHA-256 и короткий префикс для management UI.

Ссылка перестаёт работать сразу после отзыва или окончания срока. Неизвестные,
истёкшие и отозванные токены снаружи возвращают одинаковый `404`.

Полный токен показывается владельцу только при создании. Его нельзя восстановить
из БД или списка ссылок: вместо этого создаётся новая ссылка.

Отсутствие кнопки Download не предотвращает сохранение аудио. Получатель,
способный воспроизвести поток в браузере, технически может сохранить байты и
переслать capability URL другому человеку.

## Конфигурация

Sharing выключен по умолчанию:

```text
DISCOCS_SHARING_ENABLED=true
DISCOCS_SHARE_DEFAULT_TTL_HOURS=168
DISCOCS_SHARE_MAX_TTL_HOURS=8760
DISCOCS_SHARE_MAX_STREAMS_PER_CLIENT=3
DISCOCS_SHARE_MAX_STREAMS=16
DISCOCS_PUBLIC_URL=https://music.example.com
```

- После включения sharing ссылки может создавать любой обычный
  авторизованный пользователь. Анонимный и service principal не могут
  управлять ссылками.
- `DISCOCS_PUBLIC_URL` задаёт canonical origin генерируемого URL. Если он пуст,
  backend использует browser-visible request origin через frontend proxy.
- Лимиты потоков действуют в памяти одного backend process. При горизонтальном
  масштабировании нужен общий distributed limiter.

Production `.env` хранится на сервере и не обновляется Jenkins. После доставки
кода новые переменные включаются владельцем вручную, затем пересоздаётся backend.

## API

Авторизованное управление:

```text
GET    /api/v1/shares/capabilities
POST   /api/v1/shares
GET    /api/v1/shares
GET    /api/v1/shares/{id}
PATCH  /api/v1/shares/{id}
DELETE /api/v1/shares/{id}
```

`DELETE` выполняет идемпотентный revoke. Владелец видит только свои записи;
чужой идентификатор возвращает `404`.

Публичны только точные `GET|HEAD` маршруты:

```text
GET  /api/v1/public/shares/{token}
GET  /api/v1/public/shares/{token}/cover
GET  /api/v1/public/shares/{token}/items/{position}/audio
HEAD /api/v1/public/shares/{token}/items/{position}/audio
```

Audio endpoint принимает позицию snapshot-элемента, не внутренний track ID.
Backend проверяет `(token_hash, position)` до чтения файла или обращения в
Navidrome. Локальный путь, owner ID и Navidrome ID в публичный JSON не попадают.

Ответы используют `private, no-store`, `no-referrer`, `nosniff` и
`noindex,nofollow,noarchive`. CORS для share не включается.

## Аудио

Локальные файлы читаются только по пути индексированного разрешённого Track.
Navidrome-треки проксируются серверным service account: credentials владельца
не сохраняются в share и не выдаются гостю. Поддерживаются HEAD, Range и seek.

Первая версия отдаёт оригинальный формат (`format=raw` для Navidrome). Личные
настройки транскодинга создателя к гостю не применяются.

## Reverse proxy

Публичный frontend nginx:

- продолжает очищать входящий `X-Discocs-Service-Token`;
- проксирует только общий `/api` контур;
- не открывает admin/workers/settings/jobs;
- маскирует секретные части `/share/{token}` и public API в access log;
- отдаёт share page с `Referrer-Policy: no-referrer` и запретом индексации.

Не публикуйте backend-порт `8711` напрямую в интернет. Внешний TLS proxy должен
передавать исходный Host и HTTPS scheme, не логируя capability URL целиком.

## Эксплуатация

В Settings → Shared links видны источник, срок, статус, число открытий и revoke.
IP получателей не сохраняются. `access_count` — диагностический счётчик открытия
metadata, а не персональная playback-аналитика и не сигнал рекомендаций.

Если ссылка не работает, проверить по порядку:

1. включён ли feature flag;
2. не истёк и не отозван ли share;
3. остались ли snapshot-треки в каталоге и не помечены ли они missing;
4. доступны ли local mount или service credentials Navidrome;
5. не достигнут ли stream limit (`429`, `Retry-After: 60`).
