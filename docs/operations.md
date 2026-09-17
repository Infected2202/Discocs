# Operations

## Legacy implicit user

`DISCOCS_OWNER_USER` now only names the implicit user for private admin/CLI and
auth-disabled compatibility callers that do not provide an explicit `user_id`.
Request-scoped multiuser API code does not use this fallback.

The one-time Phase 2 owner/primary-key migration has been retired. Startup never
rewrites personal ownership or creates `premigrate-*` backups. A pre-Phase-2
database fails the current schema guard and must be upgraded with an older release.

This document collects practical notes for running the MVP safely while the
library and analysis database are still local SQLite files.

## Runtime Files

Runtime state is intentionally local and ignored by git:

```text
data/app.db
data/app.db-shm
data/app.db-wal
data/index_*_hnsw.bin
data/logs/
models/*.pb
models/*.onnx
models/*.json
eval/results/
```

Do not commit model binaries, SQLite databases, generated indexes, logs, or
local benchmark output.

## Model Files

Recommendation model files are placed in `models/` manually.

Discogs-EffNet head-pack files can be downloaded by the app:

```bash
recs download-models --pack discogs-effnet-heads
```

When network access from the server/container is broken, use the standalone
fallback downloader on another machine:

```bash
python scripts/download_head_models.py --out-dir models
```

Then copy the downloaded files into the server `models/` directory.

The web Dashboard has a `Head models` details panel showing ready and missing
model files.

## Navidrome Likes

`GET /api/v1/navidrome/starred/ids` reads the current user's liked tracks, albums, and
artists directly from Navidrome with one `getStarred2` request. The returned
Navidrome IDs are mapped to existing local track, release, and artist IDs; likes
whose catalog entities have not been synchronized are omitted from the local ID
lists.

## SQLite Safety

Avoid opening a live `data/app.db` through a network share while the service is
running. SQLite is a single-file database and direct SMB/UNC access from tools
like DBeaver can produce misleading corruption errors or unsafe locking
behavior.

Prefer server-local checks:

```bash
sqlite3 data/app.db "PRAGMA integrity_check;"
```

For desktop inspection, create a consistent snapshot on the server first:

```bash
sqlite3 data/app.db ".backup 'data/app.snapshot.db'"
sqlite3 data/app.snapshot.db "PRAGMA integrity_check;"
```

Open `data/app.snapshot.db` in DBeaver or copy that snapshot to another machine.

If corruption is confirmed on the server, keep the original file and recover
into a new database:

```bash
cp data/app.db data/app.corrupt.$(date +%Y%m%d-%H%M%S).db
sqlite3 data/app.db ".recover" | sqlite3 data/app.recovered.db
sqlite3 data/app.recovered.db "PRAGMA integrity_check;"
```

Only replace `data/app.db` after checking the recovered database.

## Useful Inspection Queries

Overall counts:

```bash
sqlite3 data/app.db "
select 'tracks', count(*) from tracks
union all select 'embeddings', count(*) from embeddings
union all select 'model_outputs', count(*) from track_model_outputs
union all select 'predictions', count(*) from track_predictions
union all select 'features', count(*) from track_features;
"
```

Head output coverage:

```bash
sqlite3 data/app.db "
select model_name, count(*) as tracks, min(dim), max(dim), avg(length(scores_blob))
from track_model_outputs
group by model_name
order by model_name;
"
```

Top predictions sample:

```bash
sqlite3 -header -column data/app.db "
select t.id, t.artist, t.title, p.model_name, p.rank, p.label, round(p.score, 4) as score
from track_predictions p
join tracks t on t.id = p.track_id
where p.rank <= 5
order by t.id, p.model_name, p.rank
limit 80;
"
```

Audio feature sample:

```bash
sqlite3 -header -column data/app.db "
select t.id, t.artist, t.title, f.feature_name, f.value, f.text_value, f.unit, f.confidence
from track_features f
join tracks t on t.id = f.track_id
order by t.id, f.feature_name
limit 80;
"
```

## Removing Unwanted Analysis Models

The head-pack storage is normalized, so unwanted models can be removed without
touching recommendation embeddings:

```sql
delete from track_model_outputs where model_name in ('mood_sad', 'mood_party');
delete from track_predictions where model_name in ('mood_sad', 'mood_party');
```

After removing a head from the registry or marking it disabled in code, future
head analysis will no longer produce it. Existing rows remain until explicitly
deleted.

Audio features are stored separately:

```sql
delete from track_features where extractor in ('audio_features_v1', 'audio_features_v2');
```

## Lost Files

Run a missing-file check when files are moved or deleted:

```text
Dashboard -> Check missing files
```

Missing tracks are not immediately deleted. Review them under `Lost files` and
remove selected records when you are sure they should leave the catalog.

## Бот: туннель, наблюдаемость, самовосстановление

Бот ходит в Telegram только через awg-туннель: он сидит в сетевом неймспейсе
сайдкара `awg` (`network_mode: service:awg`, см. `deploy/prod/docker-compose.yml`).
Всё, что ломается в туннеле, для бота выглядит как «интернета нет».

### Почему это пришлось чинить

16.09.2026 бот молчал полтора суток, показывая `Up` без единой жалобы. Цепочка
была такая:

1. VPN-сервер перезагрузился, и `awg-quick@awg0` на нём стартовал раньше Docker'а.
   Его `PostUp` с `iptables -I DOCKER-USER` упал («No chain/target/match by that
   name» — цепочку создаёт сам Docker), wg-quick откатил конфиг, удалил `awg0`
   и умер насовсем.
2. На хосте разом замолчали все туннели, но healthcheck сайдкара проверял
   `ip link show awg0` — интерфейс существует всегда, даже когда туннель мёртв.
   Docker считал сайдкар здоровым.
3. Бот не падал: PTB ретраит `get_updates` вечно, процесс жив, поэтому
   `restart: unless-stopped` не срабатывал.

Ни один из трёх слоёв не был способен сказать «я сломан». Теперь способен каждый.

### Что проверять при «бот не отвечает»

Порядок от дешёвого к дорогому:

```bash
docker ps --filter name=discocs --format '{{.Names}}\t{{.Status}}'
```

`unhealthy` у `awg` — мёртвый туннель, у `bot` — бот не достучался до Telegram.
Дальше смотреть возраст handshake:

```bash
docker exec discocs-awg-1 awg show awg0
```

Если `latest handshake` совпадает с uptime VPN-сервера — упал сервис на той
стороне, лечится там: `systemctl start awg-quick@awg0`. После подъёма туннеля
контейнеры, сидящие в неймспейсе awg, нужно перезапустить, если пересоздавался
сам awg-контейнер: старый неймспейс остаётся мёртвым навсегда.

### Самовосстановление

| Слой | Что проверяет | Что делает |
|---|---|---|
| healthcheck `awg` | возраст handshake < 300с | помечает контейнер unhealthy |
| монитор в `start-awg.sh` | возраст handshake каждые 30с | 2 попытки переприменить конфиг на месте, затем выход с ошибкой → docker пересоздаёт сайдкар |
| сторож в боте | `get_me()` раз в минуту | пишет heartbeat; после 5 неудач подряд валит процесс → `restart: unless-stopped` |
| healthcheck `bot` | возраст heartbeat < 240с | помечает контейнер unhealthy |

Пороги подобраны так, чтобы `unhealthy` загорался **раньше**, чем начинается
самолечение: сначала видно проблему, потом её чинят. Жёсткий перезапуск
сайдкара идёт с растущей паузой (60с, 120с, … до 10 минут) — когда лежит сам
VPN-сервер, перезапуски не помогают, и честный `unhealthy` лучше карусели.

Настройки сторожа (env бота): `WATCHDOG_INTERVAL_SECONDS`,
`WATCHDOG_TIMEOUT_SECONDS`, `WATCHDOG_MAX_FAILURES`, `HEARTBEAT_PATH`.
Настройки монитора туннеля: `AWG_CHECK_INTERVAL`, `AWG_STALE_AFTER`,
`AWG_SOFT_ATTEMPTS`.

### Гонка на VPN-сервере

Корень той аварии лечится на самом сервере — drop-in
`/etc/systemd/system/awg-quick@awg0.service.d/override.conf`:

```ini
[Unit]
After=docker.service network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=10
```

Плюс `|| true` на правилах `PostUp`/`PostDown`, работающих с цепочкой
`DOCKER-USER`: потеря одного проброса лучше, чем потеря туннеля целиком.
