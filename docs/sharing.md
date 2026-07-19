# Публичный шаринг

Discocs может создавать отзывные публичные ссылки на один трек или релиз.
Получателю не нужна учётная запись: `/share/{token}` открывает отдельный
адаптивный плеер, а релиз отображается как упорядоченная очередь с обложками.
Цветовой акцент плеера извлекается из обложки. Логотип Discocs в шапке ведёт на главную.

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

Sharing включён по умолчанию. Для явного отключения:

```text
DISCOCS_SHARING_ENABLED=false
DISCOCS_SHARE_DEFAULT_TTL_HOURS=168
DISCOCS_SHARE_MAX_TTL_HOURS=8760
DISCOCS_SHARE_MAX_STREAMS_PER_CLIENT=3
DISCOCS_SHARE_MAX_STREAMS=16
DISCOCS_PUBLIC_URL=https://music.example.com
```

- При включённом sharing ссылки может создавать любой обычный
  авторизованный пользователь. Анонимный и service principal не могут
  управлять ссылками.
- `DISCOCS_PUBLIC_URL` задаёт canonical origin генерируемого URL. Если он пуст,
  backend использует browser-visible request origin через frontend proxy.
- Лимиты потоков действуют в памяти одного backend process. При горизонтальном
  масштабировании нужен общий distributed limiter.

Production `.env` хранится на сервере и не обновляется Jenkins.
Если `DISCOCS_SHARING_ENABLED` в нём не задан, compose передаёт backend значение `true`.

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
GET  /api/v1/public/shares/{token}/preview
GET  /api/v1/public/shares/{token}/cover
GET  /api/v1/public/shares/{token}/items/{position}/audio
HEAD /api/v1/public/shares/{token}/items/{position}/audio
```

Audio endpoint принимает позицию snapshot-элемента, не внутренний track ID.
Backend проверяет `(token_hash, position)` до чтения файла или обращения в
Navidrome. Локальный путь, owner ID и Navidrome ID в публичный JSON не попадают.

Metadata и preview требуют revalidation (`private, no-cache`), а обложка и
аудио кешируются только в браузере получателя на один час
(`private, max-age=3600`). Ошибки не кешируются. Все ответы также используют
`no-referrer`, `nosniff` и `noindex,nofollow,noarchive`; CORS для share не
включается. Revoke немедленно закрывает новые запросы к origin, но не может
отозвать уже полученные или сохранённые браузером байты.

## Аудио

Локальные файлы читаются только по пути индексированного разрешённого Track.
Navidrome-треки проксируются серверным service account: credentials владельца
не сохраняются в share и не выдаются гостю. Поддерживаются HEAD, Range и seek.

Публичный Navidrome-поток всегда транскодируется в MP3 320 кбит/с независимо от
личных настроек создателя. Локальный fallback-файл отдаётся в исходном формате,
поскольку встроенного локального транскодера у backend нет.

## Карточки ссылок

Для preview-клиентов nginx направляет `/share/{token}` в серверный HTML endpoint.
Он отдаёт стандартные Open Graph и Twitter Card метатеги: название трека или
релиза, исполнителя, альбом/год, длительность, число треков и абсолютный URL
обложки. Рекламного текста и `og:audio` в карточке нет. Формат не привязан к
Telegram и подходит также для Steam Chat, Discord, Slack и других клиентов,
которые поддерживают обычный link unfurl. Обычный браузер по тому же URL получает
интерактивный SPA-плеер.

## Reverse proxy

Публичный frontend nginx:

- продолжает очищать входящий `X-Discocs-Service-Token`;
- проксирует только общий `/api` контур;
- не открывает admin/workers/settings/jobs;
- маскирует секретные части `/share/{token}` и public API в access log;
- отдаёт share page с `Referrer-Policy: no-referrer` и запретом индексации.
- распознаёт link-preview crawler по User-Agent и отдаёт ему только безопасные
  серверные метаданные без аудиопотока.

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
