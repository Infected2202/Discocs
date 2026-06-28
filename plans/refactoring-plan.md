# Рефакторинг app/main.py и app/store.py

## Текущее состояние (проблема)

| Файл | Строк | Содержимое |
|------|-------|------------|
| `app/main.py` | 12 235 | Pydantic-схемы, глобальное состояние, хелперы, сериализаторы, сервисная логика, ВСЕ route-хэндлеры, фоновые задачи |
| `app/store.py` | 6 314 | Dataclass-модели, класс `Store` со всеми DB-операциями, row-конвертеры |

Все остальные файлы в `app/` — разумного размера и не требуют рефакторинга сейчас.

---

## Целевая структура

```
app/
├── main.py               (slim: init FastAPI, middleware, include routers, startup/shutdown)
├── state.py              (NEW: глобальное состояние — JOBS, COVER_CACHE, STATS_CACHE, локи)
├── models.py             (NEW: dataclass-модели из store.py — Track, Artist, Release, ...)
│
├── schemas/              (NEW: Pydantic-схемы из main.py)
│   ├── __init__.py
│   ├── requests.py       (AnalyzeRequest, PlaybackSessionCreateRequest, ...)
│   └── responses.py      (ArtistResponse, TrackSummaryResponse, ...)
│
├── serializers/          (NEW: функции *_dict из main.py)
│   ├── __init__.py
│   ├── entities.py       (artist_summary_dict, release_summary_dict, track_summary_dict, ...)
│   ├── playback.py       (playback_session_dict, queue_item_dict, playback_queue_dict, ...)
│   ├── mixes.py          (generated_mix_summary_dict, generated_mix_detail_dict, ...)
│   └── search.py         (search_group, dashboard_shelf_item, ...)
│
├── services/             (NEW: бизнес-логика из main.py)
│   ├── __init__.py
│   ├── jobs.py           (create_job, update_job, finish_job, has_active_job, ...)
│   ├── dashboard.py      (_dashboard_recently_added, _dashboard_listen_again, ...)
│   ├── cover.py          (cached_cover_response, remember_cover, cover_response, ...)
│   ├── analysis.py       (_analyze_job, _analyze_heads_job, _analyze_audio_features_job, ...)
│   └── scrobble.py       (should_scrobble_navidrome_play, maybe_scrobble_navidrome_play, ...)
│
├── api/                  (NEW: route-хэндлеры из main.py)
│   ├── __init__.py
│   ├── deps.py           (context(), instant_mix_settings(), ...)
│   ├── tracks.py         (/tracks/*, /api/v1/tracks/*)
│   ├── artists.py        (/api/v1/artists/*)
│   ├── releases.py       (/api/v1/releases/*)
│   ├── search.py         (/api/v1/search, /text-search)
│   ├── playback.py       (/api/v1/playback/*)
│   ├── mixes.py          (/api/v1/mixes/*, /instant-mix/*)
│   ├── dashboard.py      (/api/v1/dashboard/*)
│   ├── analysis.py       (/jobs/*, /workers/*)
│   ├── navidrome.py      (/navidrome/*, /settings/navidrome)
│   ├── settings.py       (/settings/*, /instant-mix/settings)
│   └── metrics.py        (/metrics/*, /stats, /feedback)
│
└── store.py              (оставить как есть на первом этапе, после — разбить на mixins)
```

---

## Принципы безопасного рефакторинга

1. **Один шаг = один PR, один коммит** — каждый этап должен компилироваться и проходить тесты
2. **Новый файл + редирект-импорт** — при перемещении кода в старом файле оставляем `from app.new_module import X` пока не обновлены все потребители
3. **Никаких изменений логики** — только перемещение кода, переименований не делаем
4. **После каждого этапа** — запустить `python -c "from app.main import app"` и тесты

---

## Пошаговый план выполнения

### Этап 1 — Извлечение dataclass-моделей из store.py → app/models.py

**Что переносим:** классы-датаклассы из `store.py` строки 35–465:
`Track`, `ExternalTrack`, `Artist`, `Release`, `ArtistSummaryRow`, `ReleaseSummaryRow`,
`ReleaseTrackRow`, `NormalizationStatus`, `InstantMixRequest`, `TrackListing`, `SimilarTrack`,
`TrackPrediction`, `TrackModelOutput`, `TrackFeature`, `FeatureSummary`, `FeatureFilter`,
`FeatureTrack`, `HeadSummary`, `AnalysisJob`, `AnalysisTask`, `AnalysisWorker`,
`PlaybackSession`, `QueueItem`, `PlaybackEvent`, `UserTrackPreference`, `UserReleasePreference`,
`UserArtistPreference`, `PlaybackEventResult`, `GeneratedMix`, `GeneratedMixItem`,
`Playlist`, `PlaylistItem`, а также константы `COMPLETION_FRACTION`, `EARLY_SKIP_FRACTION` и пр.

**Действие:**
- Создать `app/models.py` с этими классами
- В `store.py` заменить определения на `from app.models import *` (для обратной совместимости)
- В `main.py` изменить `from app.store import Track, ...` → `from app.models import Track, ...`

**Риски:** низкие — чистое перемещение dataclass-ов без логики

---

### Этап 2 — Извлечение глобального состояния из main.py → app/state.py

**Что переносим:** константы и глобальные переменные из начала `main.py` (строки ~120–140):
`JOBS`, `JOBS_LOCK`, `DEFERRED_JOBS_LOCK`, `DEFERRED_JOB_ORDER`, `DEFERRED_JOB_STARTERS`,
`ANALYZE_EXECUTORS`, `ANALYZE_EXECUTORS_LOCK`, `SHUTDOWN_REQUESTED`,
`COVER_CACHE`, `COVER_CACHE_LOCK`, `COVER_ERROR_CACHE`,
`STATS_CACHE`, `STATS_CACHE_LOCK`,
`AUTO_INDEX_LOCK`, `AUTO_INDEX_ANALYSIS_JOBS`,
`TEXT_SEARCH_EMBEDDER`, `TEXT_SEARCH_EMBEDDER_LOCK`,
`MIX_GENERATION_LOCK`,
все `MAX_*` / `DEFAULT_*` / `*_TTL_SECONDS` / `*_MAX_*` константы.

**Действие:**
- Создать `app/state.py`
- В `main.py` → `from app.state import JOBS, JOBS_LOCK, COVER_CACHE, ...`

**Риски:** низкие. Важно: всё состояние должно быть singleton — импорты Python гарантируют это.

---

### Этап 3 — Извлечение Pydantic-схем → app/schemas/

**Что переносим:** все `class *Request(BaseModel)` и `class *Response(BaseModel)` из `main.py`
(строки ~142–785, ~665–785).

**requests.py:** `AnalyzeRequest`, `WorkerRegisterRequest`, `WorkerClaimRequest`,
`WorkerResultItem`, `WorkerFeatureItem`, `WorkerFeatureResultItem`, `WorkerPredictionItem`,
`WorkerHeadOutputItem`, `WorkerHeadResultItem`, `WorkerSubmitRequest`, `WorkerFailureItem`,
`WorkerFailuresRequest`, `WorkerReleaseRequest`, `CancelJobRequest`, `AnalyzeHeadsRequest`,
`AnalyzeAudioFeaturesRequest`, `DeleteTracksRequest`, `DeleteAnalysisErrorsRequest`,
`IndexRequest`, `NavidromeSyncRequest`, `NavidromeSettingsRequest`, `NavidromeStarRequest`,
`InstantMixSettingsRequest`, `TextSearchRequest`, `NavidromePluginEventRequest`,
`FeedbackRequest`, `FeatureFilterRequest`, `FeatureSearchRequest`,
`PlaybackSessionCreateRequest`, `PlaybackSessionPatchRequest`, `PlaybackQueueItemRequest`,
`PlaybackQueuePatchRequest`, `PlaybackEventRequest`, `AutoplayRefillRequest`,
`MixGenerateRequest`, `GeneratedMixSettingsRequest`

**responses.py:** `ApiErrorDetail`, `ApiErrorResponse`, `ImageRefResponse`,
`EntityActionResponse`, `ArtistLinkResponse`, `LibraryStatsResponse`, `ArtistSummaryResponse`,
`ReleaseSummaryResponse`, `TrackReleaseLinkResponse`, `TrackSummaryResponse`,
`ReleaseTrackItemResponse`, `SearchTopResultResponse`, `SearchGroupResponse`, `SearchResponse`,
`ArtistResponse`, `DiscographyGroupResponse`, `ArtistDiscographyResponse`,
`AvailabilityStubResponse`, `ArtistAvailabilityStubResponse`, `ReleaseAvailabilityStubResponse`,
`ReleaseResponse`, `ReleaseTracksResponse`, `RelatedDiscographyResponse`, `ImageInfoResponse`,
`NavidromeSimilarItem`, `NavidromeSimilarResponse`, `PlaybackSessionSummaryResponse`,
`PlaybackQueueItemResponse`, `PlaybackQueueResponse`, `PlaybackSessionEnvelopeResponse`,
`PlaybackEventSummaryResponse`, `PlaybackEventIngestResponse`, `PlaybackSettingsResponse`,
`AutoplayRefillResponse`

**Действие:** Создать файлы, добавить в `main.py` импорты из `app.schemas`.

**Риски:** средние — схемы могут ссылаться на модели из `app.models` (нужно правильно настроить импорты).

---

### Этап 4 — Извлечение сериализаторов → app/serializers/

**Что переносим:** функции `*_dict()` и `*_shelf_item()` из `main.py` (строки ~1075–1930):

**entities.py:** `image_ref`, `entity_action`, `release_type_label`, `artist_summary_dict`,
`artist_summary_with_external_image`, `artist_link_dict`, `release_summary_dict`,
`track_summary_dict`, `release_track_dict`, `_track_release_summary`, `_json_object`

**playback.py:** `playback_session_dict`, `queue_item_dict`, `playback_queue_dict`,
`autoplay_pool_dict`, `playback_event_dict`, `playback_event_time_ms`,
`playback_session_response`, `build_initial_playback_queue`, `queue_patch_items`

**mixes.py:** `generated_mix_summary_dict`, `generated_mix_detail_dict`,
`instant_mix_result_dict`, `instant_mix_request_dict`

**search.py:** `search_group`, `_field_search_score`, `_entity_search_score`, `search_top_result`,
`_compact_artist_names`, `dashboard_shelf_item`, `_release_shelf_item`, `_track_shelf_item`,
`_discover_track_shelf_item`, `dashboard_shelf_response`

**Действие:** Создать файлы. Сериализаторы принимают `store: Store` как аргумент — зависимость чистая.

**Риски:** средние — нужно аккуратно разрулить взаимные зависимости между serializers.

---

### Этап 5 — Извлечение сервисной логики → app/services/

**jobs.py** (строки ~1971–2094):
`JobStatus` (dataclass), `create_job`, `update_job`, `finish_job`, `has_active_job`,
`create_deferred_job_if_busy`, `_run_deferred_job`, `maybe_start_next_deferred_job`,
`sync_memory_jobs_from_durable_jobs`, `schedule_auto_index_for_analysis`

**cover.py** (строки ~4355–4405):
`cached_cover_response`, `cached_cover_error`, `remember_cover`, `remember_cover_error`,
`cover_response`

**scrobble.py** (строки ~1383–1460):
`should_scrobble_navidrome_play`, `navidrome_scrobble_submission`, `maybe_scrobble_navidrome_play`

**dashboard.py** (строки ~1694–1970):
`_dashboard_generated_mixes`, `_dashboard_recently_added`, `_dashboard_listen_again`,
`_dashboard_long_time_no_listen`, `_dashboard_discover_random`, `ensure_dashboard_mixes_fast`,
`_start_dashboard_mix_generation`

**analysis.py** (строки ~2157–7202):
`exception_detail`, `exception_traceback`, `sqlite_retry`, `raise_worker_sqlite_http_exception`,
`analyze_progress`, `head_pack_status`, `audio_feature_status`, `recommender_index_status`,
`AnalyzeResult`, `HeadAnalyzeResult`, `AudioFeaturesResult`,
`_analyze_job`, `_analyze_heads_job`, `_analyze_audio_features_job`,
`_analyze_genres_job`, `_download_head_models_job`, `_index_job`,
`_iter_analyze_results`, `_iter_analyze_task_results`, `_iter_audio_feature_task_results`,
`_extract_embedding_worker`, `_extract_audio_features_worker`, `_extract_embedding_local`,
`_extract_heads_local`, `_extract_audio_features_local`,
`_prepare_analyze_audio_path`, `_cleanup_audio_manager`,
`register_analyze_executor`, `unregister_analyze_executor`, `terminate_process_pool`,
`configure_analyze_runtime`, `create_analyze_embedder`, `task_to_track`

**Риски:** высокие для analysis.py — это крупнейший блок с multiprocessing, нужна аккуратная проверка.

---

### Этап 6 — Извлечение API роутеров → app/api/

Создаём `APIRouter` для каждого домена, регистрируем в `main.py` через `app.include_router(...)`.

**deps.py:** функция `context()`, `instant_mix_settings()`, `generated_mix_settings()`,
`playback_settings_defaults()`, `playback_session_settings()`, `request_field_names()`,
`_bounded_int()`, `_optional_bounded_float()`, `_navidrome_client()`, `api_error()`

**tracks.py** — маршруты:
`GET /tracks`, `GET /tracks/search`, `GET /tracks/{track_id}`, `GET /tracks/{track_id}/analysis`,
`GET/HEAD /tracks/{track_id}/audio`, `GET /tracks/{track_id}/cover`,
`GET /tracks/{track_id}/similar`, `GET /tracks/similar/mix`,
`GET /lost-files`, `DELETE /lost-files`, `GET /browse/facets`

**artists.py** — маршруты:
`GET /api/v1/artists/{artist_id}`, `/discography`, `/image`, `/top-tracks`, `/similar`

**releases.py** — маршруты:
`GET /api/v1/releases/{release_id}`, `/tracks`, `/related-discography`,
`/recommendations`, `/cover`

**search.py** — маршруты:
`GET /api/v1/search`, `POST /text-search`

**playback.py** — маршруты:
`POST /api/v1/playback/sessions`, все `GET/PATCH/DELETE /api/v1/playback/sessions/{id}/*`,
`POST /api/v1/playback/events`, `GET /api/v1/playback/settings`,
`POST /api/v1/playback/{session_id}/autoplay-refill`

**mixes.py** — маршруты:
`GET/POST /api/v1/mixes`, `GET /api/v1/mixes/{mix_id}`, `POST /api/v1/mixes/{mix_id}/play`,
`GET /instant-mix/settings`, `PUT /instant-mix/settings`,
`GET /instant-mix/requests`, `GET /instant-mix/requests/{id}`,
`POST /tracks/{track_id}/instant-mix`

**dashboard.py** — маршруты:
`GET /api/v1/dashboard`, `GET /api/v1/dashboard/shelves/{key}`

**analysis.py** — маршруты:
`POST /jobs/analyze`, `POST /jobs/analyze-heads`, `POST /jobs/analyze-audio-features`,
`POST /jobs/analyze-genres`, `POST /jobs/index`, `POST /jobs/check-missing-files`,
`POST /jobs/navidrome-sync`, `POST /jobs/download-head-models`,
`GET /jobs`, `GET /jobs/{job_id}`, `POST /jobs/{job_id}/cancel`,
`POST /workers/register`, `POST /workers/heartbeat`, `GET /workers`,
`POST /workers/claim`, `GET /workers/tasks/{task_id}/audio`,
`POST /workers/results`, `POST /workers/failures`, `POST /workers/release`,
`GET /analysis/errors`, `DELETE /analysis/errors`,
`POST /index/rebuild`, `POST /models/download-head-pack`, `GET /models/head-pack`

**navidrome.py** — маршруты:
`POST /navidrome/ping`, `POST /navidrome/plugin-event`,
`GET /navidrome/starred`, `GET /navidrome/starred/ids`, `GET /navidrome/starred/similar`,
`GET /navidrome/similar`, `PUT /tracks/{track_id}/navidrome-star`

**settings.py** — маршруты:
`GET /settings/navidrome`, `PUT /settings/navidrome`

**metrics.py** — маршруты:
`GET /metrics/features`, `GET /metrics/features/{name}/values`, `POST /metrics/search`,
`GET /stats`, `POST /feedback`, `GET /debug/ui`

---

### Этап 7 (опционально) — Разбиение Store на миксины

После этапов 1–6 `store.py` останется с классом `Store` (~5300 строк методов).
Безопасный способ разбиения — **mixin-классы**:

```python
# app/store/__init__.py
from app.store.tracks import TracksMixin
from app.store.library import LibraryMixin
from app.store.playback import PlaybackMixin
from app.store.analysis import AnalysisMixin
from app.store.mixes import MixesMixin
from app.store.features import FeaturesMixin

class Store(TracksMixin, LibraryMixin, PlaybackMixin, AnalysisMixin, MixesMixin, FeaturesMixin):
    pass
```

Импорт `from app.store import Store` остаётся рабочим.

Разбивка методов:
- **TracksMixin** — `upsert_track`, `get_track`, `get_tracks`, `list_tracks`, `search_tracks`, `delete_tracks`, `mark_track_missing`, `find_track_by_path`, `external_id_*`, `upsert_external_track`
- **LibraryMixin** — `get_artist`, `get_release`, `list_release_tracks`, `artist_discography`, `related_discography_for_release`, `search_entities`, `backfill_library_normalization`, `update_artist_external_info`
- **PlaybackMixin** — `create_playback_session`, `get_playback_session`, `update_playback_session`, `list_queue_items`, `replace_queue_items`, `append_queue_items`, `remove_queue_item`, `move_queue_item`, `jump_to_queue_item`, `record_playback_event`, все `*_preference*`
- **AnalysisMixin** — `create_analysis_job`, `claim_analysis_tasks`, `complete_analysis_task`, `fail_analysis_task`, `cancel_analysis_job`, `register_analysis_worker`, `expire_analysis_leases`, ...
- **MixesMixin** — `save_generated_mix`, `list_generated_mixes`, `get_generated_mix`, `record_instant_mix_request`, `list_instant_mix_requests`, `save_generated_mix_as_playlist`, ...
- **FeaturesMixin** — `save_embedding`, `load_embedding`, `load_embeddings`, `save_features`, `load_features`, `save_predictions`, `save_model_output`, `count_embeddings`, `search_tracks_by_features`, `list_head_summaries`, ...
- **StoreBase** (`__init__`, `connect`, `init`, `_init_schema`, `_ensure_column`)

---

## Порядок приоритетов

| Приоритет | Этап | Строк в main.py | Сложность |
|-----------|------|-----------------|-----------|
| 1 (сейчас) | Этап 1: models.py | освобождает store.py | низкая |
| 2 | Этап 2: state.py | ~30 | низкая |
| 3 | Этап 3: schemas/ | ~640 | низкая |
| 4 | Этап 4: serializers/ | ~860 | средняя |
| 5 | Этап 5a: services/jobs.py | ~140 | средняя |
| 6 | Этап 5b: services/cover.py | ~70 | низкая |
| 7 | Этап 5c: services/scrobble.py | ~80 | низкая |
| 8 | Этап 5d: services/dashboard.py | ~270 | средняя |
| 9 | Этап 6: api/ роутеры | ~остаток | высокая |
| 10 | Этап 5e: services/analysis.py | ~1400 | высокая |
| 11 | Этап 7: Store mixins | store.py | высокая |

---

## Чеклист перед каждым этапом

- [ ] Все тесты зелёные: `pytest tests/ -x`
- [ ] Приложение стартует: `python -c "from app.main import app"`
- [ ] Нет циклических импортов: `python -c "import app.main"` без ошибок
- [ ] Файл изменений закоммичен отдельно (один этап — один коммит)
