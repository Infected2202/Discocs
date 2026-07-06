# План разбора и устранения замечаний SonarQube

Дата снимка: 2026-07-06

Проект SonarQube: `discocs`

Ветка: `main`

Дата анализа SonarQube: 2026-07-06 12:18:20 UTC

## 1. Исходное состояние

- Quality Gate: `ERROR`.
- Открытые issues: **227**:
  - `CRITICAL`: 67;
  - `MAJOR`: 111;
  - `MINOR`: 49;
  - bugs: 19;
  - vulnerability: 1;
  - code smells: 207.
- По подсистемам:
  - backend `app/`, без старой админки: 73;
  - Telegram bot: 16;
  - новый UI `ui/src/`: 82;
  - старая админка `app/ui.html`: 56.
- Security Hotspots: 15 всего, 4 имеют статус `TO_REVIEW`.
- Общая coverage: 52.5%.
- Условия, из-за которых Quality Gate красный:
  - coverage нового кода: 50.4% при пороге 80%;
  - reviewed security hotspots: 75% при требовании 100%.
- Дублирование нового кода проходит gate: 0.30936% при пороге 3%.
- Оценочный maintainability debt SonarQube: 2303 минуты, но это число не
  учитывает стоимость проверки поведения после крупных рефакторингов.

После снимка SonarQube появился коммит
`a5c5bf38d23c78cf824bb6c186f8a6bda586f1f4`, который существенно изменил
`app/cli.py`. Поэтому восемь CLI-issues помечены ниже как потенциально устаревшие.

## 2. Принципы выполнения

1. Каждая рабочая порция начинается с characterization-теста, который фиксирует
   текущее полезное поведение, и заканчивается целевыми тестами изменённого кода.
2. Одна порция — одна связная причина изменения и отдельный коммит.
3. После каждой порции запускаются узкие тесты подсистемы, затем полный набор
   тестов перед повторным анализом.
4. Issue считается закрытым только после проверки поведения и исчезновения ключа
   в свежем анализе. Простое снижение метрики complexity не является критерием
   готовности.
5. Старая админка заморожена в состоянии as is: разработка в ней не ведётся,
   исправления откладываются, а UI-работа по умолчанию выполняется только в
   `ui/src/`.
6. Для каждого issue сохранять ключ из приложения A в сообщении коммита или в его
   описании. Это позволит сверять прогресс без повторного изучения SonarQube.

### 2.1. Принятые решения

Зафиксировано по итогам обсуждения с Саней:

1. `/admin` остаётся доступной, но не развивается и пока не исправляется. Её 56
   issues не входят в первую волну. Четыре `CRITICAL` в этой группе — две
   complexity-функции и два лишних аргумента при вызове `loadLostFiles` /
   `loadErroredFiles`; последние игнорируются JavaScript и не создают падение.
   Единственная security vulnerability админки имеет severity `MINOR`.
2. Root в одноразовом UI test container,
   `6550f731-6547-454d-8810-5073f0460bd3`, пока не исправляется: варианты
   непривилегированного пользователя и pnpm cache требуют отдельного разбора.
3. Dashboard shuffle, `1c29092e-24a1-481a-b049-fa334e4a9c64`, признан безопасным:
   `random.shuffle` меняет только порядок рекомендаций и не участвует в
   security boundary. Рекомендуемый hotspot resolution: `SAFE`.
4. MD5 в Subsonic-аутентификации,
   `04a845ed-ddd6-4382-b08f-1ae71aaf27c8`, продиктован протоколом
   Navidrome/Subsonic. Рекомендуемый resolution: `ACKNOWLEDGED`; секрет не должен
   логироваться, транспорт должен быть доверенным или защищённым.
5. HTTP для worker не запрещается. `DISCOCS_WORKER_SERVER` описывает соединение
   worker → backend, а не весь веб-доступ. HTTP допустим для localhost и
   доверенной локальной сети; production deployment должен использовать HTTPS.
   Будущее усиление — предупреждение при публичном `http://` endpoint или
   opt-in-флаг `require TLS`, но не безусловная блокировка, ломающая локальную
   работу. Для `a24e89a3-82ef-4e93-ad11-fdf816782d96` рекомендуемый текущий
   resolution: `ACKNOWLEDGED`.
6. Bot cancellation, async file I/O и сравнение float не требуют отдельного
   продуктового решения. Это задачи реализации с тестами. Для нулевой нормы
   сохраняется точная семантика `norm == 0.0`: epsilon нельзя вводить без
   изменения требования к малым ненулевым векторам.
7. Complexity анализируется по смыслу кода. Цель — улучшить границы и
   тестируемость, а не механически получить число 15; небольшое превышение можно
   принять, если извлечение helper ухудшает код.

## 3. Этапы

### Этап 0. Обновить baseline и зафиксировать границы

Цель: не чинить уже исчезнувшие или не относящиеся к поддерживаемому продукту
замечания.

1. Запустить CI-анализ на текущем `main` после коммита `a5c5bf3`.
2. Сверить восемь потенциально устаревших ключей `app/cli.py`:
   `eb4a59d1-fc34-40ad-8bc3-058af659b9d6`,
   `06b7e4f3-5096-441c-b016-8bb1266985fd`,
   `31c4d747-8225-464b-8927-0a6f41b21d64`,
   `280c2471-d337-41ae-8a7f-5daa8e808703`,
   `c4979daa-53f7-433d-a184-f9fa40b318e1`,
   `89cb055c-da57-45d8-ae61-3cf98dfa334e`,
   `b591dc12-db21-4875-a636-6e3903666d6e`,
   `17b88ed0-51a2-4812-b1dd-58cccab0fdc9`.
3. Оставить 56 issues старой админки в анализе как видимый осознанный долг.
   Админка доступна as is, но не входит в текущую разработку; не исключать
   `app/ui.html` из SonarQube и не исправлять её в первой волне.
4. Зафиксировать новый код-период SonarQube: без этого работа над общей coverage
   может не поднять именно `new_coverage`.

Результат: актуальный список ключей, решение по `/admin`, понятная база для
сравнения. Изменений поведения на этом этапе нет.

### Этап 1. Security Hotspots и реальные дефекты

Это приоритетная ручная работа: здесь нельзя механически менять статус или код.

#### 1.1. Четыре hotspots `TO_REVIEW`

- `6550f731-6547-454d-8810-5073f0460bd3` — root в
  `deploy/ci/Dockerfile.ui-test`. Исправление отложено из-за неоднозначности
  непривилегированного пользователя и pnpm cache. Hotspot пока оставить
  `TO_REVIEW`, чтобы решение не потерялось.
- `1c29092e-24a1-481a-b049-fa334e4a9c64` — `random.shuffle` в dashboard.
  Случайность используется только для порядка рекомендаций, не для секрета.
  Принятое решение: `SAFE`.
- `a24e89a3-82ef-4e93-ad11-fdf816782d96` — потенциальный clear-text URL,
  передаваемый worker через `DISCOCS_WORKER_SERVER`. HTTP разрешён для localhost
  и доверенной локальной сети, production должен использовать HTTPS. Не вводить
  безусловную блокировку; возможное будущее усиление — warning или opt-in
  `require TLS`. Текущее решение: `ACKNOWLEDGED`.
- `04a845ed-ddd6-4382-b08f-1ae71aaf27c8` — MD5 в Subsonic-аутентификации бота.
  Алгоритм задан протоколом Navidrome/Subsonic. Принятое решение:
  `ACKNOWLEDGED`; транспорт должен быть доверенным/защищённым, секрет не
  логируется.

После ревью выставить статус с комментарием, содержащим принятое решение и
проверенную границу безопасности. Это поднимет hotspot review до 100%.

#### 1.2. Vulnerability

- `82210788-9353-4b16-9448-bd9fe21cf281` — CDN stylesheet без SRI в
  `app/ui.html:7`. Относится к старой админке и блокируется решением этапа 0.
  Варианты: закрепить URL + `integrity`/`crossorigin`, локально поставлять asset
  или признать/исключить неподдерживаемую админку.

#### 1.3. Bugs, способные менять runtime-поведение

Сначала исправлять и тестировать:

- `3fd38444-5372-4f29-be05-86f64410c518` — повторно выбрасывать
  `asyncio.CancelledError` после cleanup бота;
- `975fdb0f-a9dd-4848-99f3-23aafa0b2753` — синхронное чтение файла внутри
  async-функции Navidrome-клиента;
- `a2f4e443-9ccb-421e-9d66-4cdc8575e847`,
  `ee0bf22b-32a8-4ecf-90b5-3ae9674ca95c` — Promise передан в void callback;
- `593bb60e-eeef-4c5e-a1c0-e06b64eda7f5` — проверить реальный тип результата
  `dataclasses.replace` перед изменением `_insert_release_artist`;
- `38e98b16-c119-4292-abd7-56bea5af05fc` — tuple разной длины в recommender;
- `a7297125-93b4-4802-9b0f-b5deac5d2e7a` — сравнение нормы float с нулём:
  сохранить точную проверку математически нулевого вектора и добавить тесты для
  нулевого и малого ненулевого вектора. Если правило остаётся, принять как
  false positive; epsilon не подставлять.

Остальные bugs нового UI — accessibility-дефекты и идут в этап 3. Три runtime
bugs старой админки (`4f8bdd42-...`, `36e7db37-...`, `7622e294-...`) ждут решения
этапа 0.

Критерий готовности: тесты отмены/cleanup, async I/O, Promise callbacks,
типизированных возвратов и численной нормализации; свежий анализ не содержит
исправленных ключей.

### Этап 2. Механические и локальные исправления

Можно выполнять небольшими пакетами по правилу, без архитектурной перестройки.
Несмотря на механический характер, каждый пакет проверяется существующими и
новыми узкими тестами.

Рекомендуемый порядок:

1. Python:
   - `S8572` — `logging.exception`;
   - `S7494`, `S7500`, `S7504` — упрощение коллекций;
   - `S3457` — лишний f-string;
   - `S8513` — один `endswith(tuple)`;
   - `S3358` — вынести conditional expression;
   - `S1186`, `S108` — документировать намеренно пустой метод/блок;
   - `S1172` — удалять параметр только после проверки callback/interface
     контрактов; иначе применить осмысленное подавление правила.
2. TypeScript/JavaScript:
   - объединение импортов `S3863`;
   - `.at()`/`.dataset` (`S7755`, `S7761`);
   - лишние assertions и неиспользуемые props (`S4325`, `S6767`);
   - локальные упрощения условий (`S6606`, `S6660`, `S7735`, `S7748`);
   - nested ternary/template literal (`S3358`, `S4624`) — по одному компоненту,
     с тестом всех веток;
   - пустые методы mock `IntersectionObserver` (`S1186`) — сделать намеренность
     явной без ослабления теста.
3. Не включать сюда `S107`, `S3776`, async/typing issues и accessibility:
   внешне похожие правки там меняют контракты или структуру.

Критерий готовности: каждая ветка преобразованного условия проверена тестом;
публичные и callback-сигнатуры не изменены случайно.

### Этап 3. Новый UI: accessibility и интерактивная семантика

29 issues требуют не добавления случайного `role`, а выбора корректного native
элемента и проверки реального взаимодействия.

1. Сначала общий паттерн для карточки/строки/slider:
   `button`/`a` там, где возможно; иначе `role`, `tabIndex`, Enter/Space,
   pointer/touch и предотвращение конфликтов вложенных controls.
2. Затем компоненты:
   `Sidebar`, `Shelf`, `MediaCard`, `TiltedArtwork`, `VirtualTrackRow`,
   `PlayerBar`, `ExpandedPlayer`.
3. Добавить React Testing Library тесты:
   - доступность с клавиатуры;
   - ровно одно действие на click/Enter/Space;
   - отсутствие срабатывания родителя при клике по вложенной кнопке;
   - фокус и корректное имя/alt.
4. Индексные keys (`S6479`) исправлять только после выбора стабильного id; не
   конструировать ключ, который меняется при сортировке.

Ключи находятся в приложении A по правилам `S6848`, `S6847`, `S1082`, `S6819`,
`S1077`, `MouseEventWithoutKeyboardEquivalentCheck`, `S6479`.

Критерий готовности: unit-тесты keyboard/pointer, ручная проверка нового UI через
Playwright на `/app`, отсутствие регрессии drag/seek/expand.

### Этап 4. Контракты, типы и API

Здесь нужно сначала разобраться и, возможно, обсудить дизайн:

- `b41577d0-66fa-44d3-a5b3-1e422d8e53d2`,
  `665040b4-a87d-49b1-8825-4211b9a6f02f` — один объект параметров для
  instant-mix request вместо 18–20 позиционных/keyword аргументов;
- `baf2cdb6-6dc7-4489-a3c6-810329fd375c` — объект playback event или сохранение
  сигнатуры как границы persistence;
- `14834063-295a-48c0-81ae-dfa076c7911b`,
  `03ba733e-ad8f-4b7f-837f-84ea74e6e27f` — исправить реальный return annotation,
  не делать cast ради Sonar;
- `a1aeefe8-1d8c-4f98-9bd0-07397f4638fa` — late binding lambda: добавить тест
  нескольких centroid до фикса;
- `c4979daa-53f7-433d-a184-f9fa40b318e1`,
  `89cb055c-da57-45d8-ae61-3cf98dfa334e` — переход на
  `numpy.random.Generator`: согласовать reproducibility/seed;
- `31c4d747-8225-464b-8927-0a6f41b21d64` — явно определить axis по смыслу
  данных, после обновления baseline.

Критерий готовности: round-trip/API compatibility tests, детерминированные тесты
random-путей, отсутствие широких `Any`/casts, скрывающих проблему.

### Этап 5. Крупные рефакторинги complexity

В снимке 59 структурных issues (`S3776` и `S2004`), из них 50 — Python
`S3776`. Их нельзя собирать в один большой рефакторинг.

Порядок по риску и связности:

1. Низкое превышение (complexity 16–20): serializers, autoplay helpers,
   `navidrome_migration`, небольшие Store-методы. Обычно достаточно guard clauses
   и чистого helper с тестами веток.
2. Flow/recommendation domain:
   `flow_candidates`, `flow_feedback`, `flow_regions`, `release_scoring`,
   `recommender`, `mixes`. Перед извлечением helper зафиксировать ranking,
   фильтрацию, tie-breaking и лимиты артистов/альбомов.
3. Analysis/jobs/workers:
   `analysis_jobs`, `analysis_helpers`, `services/analysis`, `api/jobs`,
   `api/workers`. Разделять orchestration и pure decisions; отдельно тестировать
   resume, cancellation, lease и failure reporting.
4. Новый UI:
   `SettingsPage`, `TrackRow`, `VirtualTrackRow`, `ExpandedPlayer`,
   `PlayerBackdrop`, `PlayerBar`, query hooks. Извлекать pure view-state и
   callbacks, не дробить JSX формально.
5. Bot delivery/transcoder/single-instance: сначала тесты retry, cleanup,
   pagination и отправки файлов.
6. `app/cli.py` — только после свежего анализа; старый complexity 259 относится к
   уже вынесенному `WorkerRuntime`.

Для каждой функции:

1. перечислить observable branches;
2. дописать characterization-тесты отсутствующих веток;
3. вынести pure decision helpers или отдельный runtime/service object;
4. убедиться, что exception boundaries и порядок side effects не изменились;
5. выполнить отдельный коммит.

Критерий готовности: тесты ломаются при инверсии ключевых условий, complexity
ниже порога без новых broad suppressions.

### Этап 6. Старая админка `/admin` — отложен

Админка остаётся доступной as is, но разработка в ней не ведётся. Её 56 issues
сохраняются в SonarQube как видимый долг и не входят в текущую волну исправлений.
Исключать `app/ui.html` из анализа или удалять runtime сейчас не нужно.

При будущем возобновлении разработки порядок будет таким:

1. SRI и два лишних аргумента `loadLostFiles` / `loadErroredFiles`;
2. dialog/accessibility;
3. exception handling;
4. локальные механические упрощения;
5. complexity;
6. браузерные smoke-тесты именно `/admin`.

### Этап 7. Coverage и финальный Quality Gate

Coverage нельзя поднимать пустыми тестами ради строки. Приоритет:

1. новый/изменённый код, определяющий `new_coverage`;
2. characterization-тесты этапов 1, 4 и 5;
3. branch coverage для ranking, jobs, cancellation, player transitions и
   accessibility;
4. убедиться, что `coverage.xml`, `bot-coverage.xml` и
   `ui/coverage/lcov.info` корректно сопоставляются с source paths.

Финальная проверка:

- `python -m pytest`;
- `python -m compileall app tests`;
- bot tests/coverage;
- UI typecheck, unit tests и coverage;
- Playwright smoke нового UI; `/admin` — только если этап 6 разрешён;
- новый SonarQube analysis;
- Quality Gate `OK`, hotspots reviewed 100%, new coverage не ниже 80%.

## 4. Предлагаемые рабочие порции

1. Hotspot review без изменения кода.
2. Bot cancellation + async file I/O.
3. Promise callbacks и новый UI accessibility primitives.
4. Python safe mechanical batch.
5. TypeScript safe mechanical batch по компонентам.
6. Instant-mix/playback parameter objects.
7. Backend complexity — по одному domain cluster.
8. UI complexity — по одному компоненту.
9. Bot complexity.
10. `/admin` пропустить до отдельного решения о возобновлении разработки.
11. Coverage gaps и финальный gate.

## Приложение A. Автономный реестр issue keys

Формат: `ключ (rule, строка)`. Строки относятся к анализу от 2026-07-06 и после
изменений могут сместиться; ключ и путь остаются основной ссылкой.

### Backend

- `app/analysis_helpers.py`: `04412b88-74ae-495e-bda1-b674c248bf2e` (`S3776`, 261).
- `app/analysis_jobs.py`: `e86d5cde-f584-4fc4-9e9c-433d207b4902` (`S3776`, 176),
  `d2189e1b-a684-46ea-81db-744e19545b8a` (`S3776`, 307),
  `a3da85d0-a62a-426e-8a67-89fdfeaabc97` (`S3776`, 483).
- `app/api/deps.py`: `b41577d0-66fa-44d3-a5b3-1e422d8e53d2` (`S107`, 242).
- `app/api/flow.py`: `0874fd63-4d40-4b85-a4f7-adf91c409624` (`S3776`, 224),
  `6fa364e5-bb25-48e2-b865-c0b843fcef48` (`S3776`, 372).
- `app/api/jobs.py`: `d281fc75-f7f8-4b4d-9a46-0991e890a86e` (`S3776`, 221),
  `d8347b9a-6147-4179-acce-75c918f02927` (`S3776`, 379),
  `92285812-1760-42e1-ae71-25f16bf732a7` (`S3776`, 544).
- `app/api/middleware.py`: `30e0103e-736e-4320-8090-6dc45f350cab` (`S8513`, 21).
- `app/api/playback.py`: `487af247-4754-47ed-a24f-9f7e42a36f56` (`S3776`, 170).
- `app/api/tracks.py`: `14834063-295a-48c0-81ae-dfa076c7911b` (`S5886`, 657).
- `app/api/workers.py`: `04d53238-9e09-4f9d-96ee-9717274e7830` (`S3776`, 204).
- `app/autoplay.py`: `293c1829-08ff-4b49-9d43-922ff2cc70f0` (`S7494`, 174),
  `93628771-8224-437b-8a05-532afd339574` (`S3776`, 307),
  `f37a4325-af8a-431e-8bbb-8a2ca5f447a9` (`S3776`, 427).
- `app/cli.py`: `eb4a59d1-fc34-40ad-8bc3-058af659b9d6` (`S3776`, 124),
  `06b7e4f3-5096-441c-b016-8bb1266985fd` (`S3776`, 262),
  `31c4d747-8225-464b-8927-0a6f41b21d64` (`S6929`, 888),
  `280c2471-d337-41ae-8a7f-5daa8e808703` (`S3776`, 894),
  `c4979daa-53f7-433d-a184-f9fa40b318e1` (`S6711`, 910),
  `89cb055c-da57-45d8-ae61-3cf98dfa334e` (`S6711`, 927),
  `b591dc12-db21-4875-a636-6e3903666d6e` (`S3776`, 1186),
  `17b88ed0-51a2-4812-b1dd-58cccab0fdc9` (`S107`, 1187).
- `app/embedder.py`: `dc82ad60-a966-4f90-85b6-b7a4df5bb812` (`S1186`, 36),
  `d1aa5499-a394-4163-a382-d267d4807c95` (`S8572`, 527).
- `app/mixes.py`: `a1aeefe8-1d8c-4f98-9bd0-07397f4638fa` (`S1515`, 164),
  `8271f003-58cb-4a68-91d1-c2096673873c` (`S1172`, 437),
  `4a8e59b4-13e0-4d0e-8bf3-0edabedacc23` (`S3776`, 499),
  `ddbd1288-c21b-41b6-9970-ee6051bc0d55` (`S3776`, 826),
  `9a0f8b9a-9a43-4be3-888f-e4ff0293c420` (`S3776`, 892),
  `a7297125-93b4-4802-9b0f-b5deac5d2e7a` (`S1244`, 1026).
- `app/navidrome_migration.py`: `834de9e2-0b04-4cbd-8d4b-daff0161a9bc` (`S3776`, 306).
- `app/navidrome_sync.py`: `a555a14c-8856-42da-9f69-734e714642be` (`S3776`, 95),
  `03ba733e-ad8f-4b7f-837f-84ea74e6e27f` (`S5886`, 259).
- `app/recommender.py`: `9a495eae-a309-433e-ab98-c347059d8678` (`S3776`, 138),
  `38e98b16-c119-4292-abd7-56bea5af05fc` (`S8495`, 400).
- `app/serializers/mixes.py`: `6d634d39-56ef-420b-9823-bd9e5369b39d` (`S3358`, 24).
- `app/serializers/playback.py`: `af78d236-caf3-4e26-8efb-9a9ddcae287d` (`S3776`, 119),
  `dca7aba0-dbe2-4040-82b8-fc4b90e1bd28` (`S3776`, 201).
- `app/services/albums_for_you.py`: `03592f3d-bea9-4fce-a106-f2e65df68d2b` (`S3776`, 29).
- `app/services/analysis.py`: `cb7b8d62-ca18-4c72-8db8-dc3c165098b0` (`S3776`, 361),
  `cca30f02-c685-4e68-8c8d-0c7728e82ce1` (`S3776`, 419),
  `33217fac-6540-4e2a-96e8-879c632171d9` (`S3776`, 479).
- `app/services/dashboard.py`: `1c2be838-1160-40d8-82ff-9dbfc867a219` (`S1172`, 304),
  `d7067b9c-564d-469c-b04b-d76be859a080` (`S1172`, 342),
  `82a022b3-50df-4e14-9ca7-19226a69e41c` (`S1172`, 373),
  `e54be2ad-54bb-4d00-926a-a7b37dfc076d` (`S1172`, 415).
- `app/services/flow_candidates.py`: `fc58528c-7ade-45dc-8434-2d88dc323237` (`S3776`, 204),
  `6ad7052c-fd1e-4d7c-8ac2-e19c941fa4b7` (`S3776`, 310),
  `ffe5038b-788b-4fe6-a7d7-f409a9adeca6` (`S7500`, 335),
  `993bfd1c-4b07-415b-9273-9c25106e2511` (`S3776`, 389),
  `8c149f05-e156-4867-a965-9926461941d0` (`S3776`, 568).
- `app/services/flow_feedback.py`: `bdc1b627-855a-4c36-8823-030737c01d21` (`S3776`, 74).
- `app/services/flow_regions.py`: `48ce6bf9-1767-41dd-80de-2854a3c479bb` (`S3776`, 409).
- `app/services/jobs.py`: `5bd81db9-28c3-4577-b6d1-62050a109e39` (`S7504`, 152).
- `app/services/release_scoring.py`: `c1bb54bd-14ca-4c69-a01e-336b78cb88cd` (`S1172`, 108),
  `0e7b716a-d1ae-4081-8861-b2c96c1609e1` (`S3776`, 127),
  `d0d7bc54-cffa-46a0-b13a-22a232fded57` (`S3776`, 323).
- `app/store/_helpers.py`: `4ffb2a18-ffc0-43d8-bb26-99a1c807459d` (`S7494`, 486).
- `app/store/embeddings.py`: `7f7a965f-ad50-4084-8d15-9d3822b765f3` (`S3776`, 648),
  `3e51f5dc-d91b-4a8d-b0e4-2090fb863eb7` (`S3776`, 809).
- `app/store/jobs.py`: `9e836a0b-850a-469e-85d2-16dc1dff916e` (`S3457`, 166).
- `app/store/library.py`: `593bb60e-eeef-4c5e-a1c0-e06b64eda7f5` (`S5655`, 465),
  `1b7b1bbc-6bf9-4262-a20b-ee9b42f5b991` (`S3776`, 1148).
- `app/store/mixes.py`: `665040b4-a87d-49b1-8825-4211b9a6f02f` (`S107`, 390).
- `app/store/playback.py`: `b087cb0a-a2cd-4129-bbf1-d7a7c80be9ac` (`S3776`, 198),
  `8b7118eb-ce96-4c3a-82f4-d01007dec34b` (`S3776`, 289),
  `baf2cdb6-6dc7-4489-a3c6-810329fd375c` (`S107`, 520),
  `906060bd-a870-4453-8162-aaba22b59e16` (`S3776`, 603),
  `cb79e4b8-1fd6-44d2-b20e-fe498c5b952e` (`S3776`, 1054).
- `app/store/release_aggregates.py`: `3fc8a658-e866-4ad4-87a6-efe93070e560` (`S108`, 13).

### Telegram bot

- `discocs_bot/bot/handlers/callbacks.py`:
  `80a293d9-b2e1-4f0f-9e62-937dddf925b8` (`S8572`, 223),
  `6ea4f459-e05c-4da3-b246-29a8fa4e3646` (`S8572`, 259),
  `b90131ac-4247-414f-bebf-0e4b29dc3b98` (`S1172`, 269),
  `49832f6f-2e28-445e-856b-c10e7e5921a9` (`S1172`, 282),
  `ef18c9cf-b9f3-43d7-bc06-c17f508eef95` (`S1172`, 396).
- `discocs_bot/bot/handlers/search.py`: `1b47898c-457b-44af-baee-32ff98a597ee` (`S1172`, 133).
- `discocs_bot/bot/main.py`: `3fd38444-5372-4f29-be05-86f64410c518` (`S7497`, 129).
- `discocs_bot/bot/services/delivery.py`:
  `c1987c66-6665-4ccf-ac0a-b74971fc4b12` (`S3776`, 176),
  `8f99205d-c4c3-449b-b903-b9b0dba4508d` (`S3776`, 284),
  `d808fbbd-2027-45ea-9069-68709b0a1a00` (`S3776`, 760),
  `03f0dda1-f7e0-4b44-bdad-8b6d44bca120` (`S8572`, 794),
  `618184ea-6f27-40c4-98ef-92fb906491a9` (`S3776`, 837).
- `discocs_bot/bot/services/navidrome.py`: `975fdb0f-a9dd-4848-99f3-23aafa0b2753` (`S7493`, 125).
- `discocs_bot/bot/services/transcoder.py`: `ef836c7e-2b1c-428e-a54c-7fdce5ac1093` (`S3776`, 49).
- `discocs_bot/bot/utils/single_instance.py`: `08549c7f-7f17-49b1-92f1-44a0929b0a43` (`S3776`, 82).
- `discocs_bot/bot/utils/track_pages.py`: `dbba7bc5-b5f7-448a-bfc3-6fc00b00bd73` (`S1172`, 256).

### Новый UI

- `ui/src/api/hooks/useDashboard.ts`: `39556319-2883-4490-8570-ffce89c17ac5` (`S2004`, 19).
- `ui/src/api/hooks/useShelf.ts`: `f6ca716e-0c42-42d3-a9fd-97cc705441bb` (`S4325`, 18).
- `ui/src/components/layout/AppShell.tsx`:
  `8301b9ba-42b8-4b7d-8fa6-a15c39f460ac` (`S3735`, 28),
  `fef07f87-39c1-4a82-8536-ff93b3282278` (`S7748`, 48).
- `ui/src/components/layout/Sidebar.tsx`:
  `5cb6e489-65cc-4602-9cdc-7b0612dce5ae` (`S2301`, 18),
  `88518f4f-59b2-42a3-a7b4-b0c1b34046af` (`S6847`, 29),
  `fdcc9710-46e8-4a51-92e2-b09200631de3` (`S1082`, 29).
- `ui/src/components/media/ForYouShelf.tsx`: `24643960-2a38-4939-a9b9-26e154bc1276` (`S3358`, 49).
- `ui/src/components/media/MediaCard.tsx`:
  `8f0060d6-0165-465c-bcf3-ca9c2f1b1149` (`S6767`, 15),
  `de1626cc-dca2-4dee-afcf-0b550cb3c921` (`S6848`, 49),
  `cb0052b6-ef53-4d01-bbd4-80f1391adcea` (`S6848`, 106),
  `fc0cbbc0-0145-4354-9c79-898382225251` (`S1082`, 106),
  `7db06a8b-90e8-4f33-a72f-3bec8b85c2dc` (`S3358`, 115).
- `ui/src/components/media/Shelf.tsx`:
  `479e00e6-740e-4f1b-a44a-70a7d2358ab6` (`S1082`, 55),
  `0ae6c029-80d2-423b-9c00-31c4702c2553` (`S6847`, 55),
  `ac822a9f-9ac3-4c3d-8c7f-3dc711fac2a9` (`S6479`, 97).
- `ui/src/components/media/TiltedArtwork.tsx`: `0866d7be-5824-404b-b9ac-8d70545daf72` (`S6848`, 40).
- `ui/src/components/media/TrackMenu.tsx`:
  `49e61f98-ce32-4bd4-b55a-34ddb8a9841b` (`S3863`, 14),
  `8b655512-2e5c-4943-b264-6f08111aefc9` (`S3863`, 15).
- `ui/src/components/media/TrackRow.tsx`:
  `a14d9909-93f9-44d1-ab69-7c40d0adfb94` (`S6767`, 31),
  `00ac1800-cd21-4ca8-9cd8-acd0b2b50319` (`S3776`, 34),
  `c638df9f-10fd-4609-8e56-84e89581dd46` (`S3358`, 80),
  `6641a210-9a01-4953-933f-f92f4e36c0b1` (`S7735`, 87),
  `68fb330a-e337-4dcb-bfdb-75630ae5ccf5` (`S3358`, 158).
- `ui/src/components/media/VirtualCardGrid.test.tsx`:
  `a9706150-f401-4920-8dd2-bc6fd0c5ae7a`,
  `975782d4-df44-46b3-b081-377e6fa755c3`,
  `843164a6-88d2-4162-b8ef-d71701f05b1b` (`S1186`, 9–11).
- `ui/src/components/media/VirtualTrackList.test.tsx`:
  `2455f5b9-0612-4d93-adef-7d667c6e75a2` (`S7761`, 89).
- `ui/src/components/media/VirtualTrackRow.tsx`:
  `7c343262-a332-4597-9fa4-dfb64a3baf37` (`S3776`, 27),
  `62b8a6f7-7460-46ea-8902-40e56286f221` (`S6848`, 56),
  `09adc539-2be9-4e2b-bec6-55bd9922d6e3` (`S3358`, 62),
  `3ec4e875-0b4b-464a-a852-d8d5d03f0e4e` (`S3358`, 77).
- `ui/src/components/player/ExpandedPlayer.tsx`:
  `87595531-5c3f-480b-96d7-908f6e72cd50` (`S3776`, 28),
  `a3a9d2b6-23da-403a-96b0-cd286c5cc7f0` (`S1077`, 101),
  `c0169766-df85-4fa3-9cd5-df09a91303a7`, `4bd9ee09-2dcd-47ee-a460-48e0cdcd907e`,
  `ea940511-3028-427b-9981-1a9b137c8b44`, `3049ed19-1b64-4bd3-ada8-f8af79b0b64d`
  (`S6848`, 131/146/229/271),
  `c3873152-6d20-41f8-8042-d1e368ee5b77`, `02897c15-f00c-4203-ad07-27f8f259ebf7`,
  `9e46a1ef-a72c-4d34-bb61-2ff47f3adddc`, `340afba4-0d6b-4fa9-a9f5-afcdbb56f4c4`
  (`S1082`, 131/146/229/271),
  `7317f430-6aec-4866-bb0d-cbf7cd2d4a3b`, `c43e5105-7410-462f-9bd5-19f420240f95`
  (`S3358`, 252/269).
- `ui/src/components/player/PlasmaFBM.tsx`: `a7312443-74a4-4c2c-bd93-295b3cf26874` (`S7748`, 115).
- `ui/src/components/player/PlayerBackdrop.tsx`: `1c32954f-e313-4455-b2df-eeb90dd9861f` (`S2004`, 62).
- `ui/src/components/player/playerBackdropUtils.ts`: `da2db41f-c6f5-4441-b46b-5f62abbfa5a3` (`S4624`, 20).
- `ui/src/components/player/PlayerBar.tsx`:
  `c48e160e-a48c-46fa-a94a-65fbeb7b7596`, `38b2c3a8-0eb2-4bbf-8459-8b0693252864`
  (`S1082`, 88/260),
  `824f1fa0-cf56-464f-b69b-31234347da67`, `4950f743-7d05-4413-b3d1-c9caf9b990d5`,
  `ff6ceb96-c5ff-443d-8f25-a89ed24731ae`, `22908be5-4d08-463d-b2b0-2fbbad240807`
  (`S6848`, 88/104/247/260),
  `16b964a4-7de4-4a82-85bd-6d6b2b4d203d`, `ba003be6-48b4-4cab-b128-dd0a1d72f48e`
  (`S3358`, 122/281),
  `97d87b12-c685-40c1-a84f-f2c353405c3d` (`S2004`, 184),
  `cd810753-d6b1-470d-8fb1-76e822f0d000` (`S7721`, 211).
- `ui/src/components/player/playerBarTransitionUtils.ts`:
  `a2f4e443-9ccb-421e-9d66-4cdc8575e847`,
  `ee0bf22b-32a8-4ecf-90b5-3ae9674ca95c` (`S6544`, 34–35).
- `ui/src/components/player/QueueItem.tsx`: `79125ae5-4cd5-446c-be21-1759b932bca3` (`S3358`, 109).
- `ui/src/components/profile/ProfileButton.tsx`:
  `fc41f054-d0e0-4b99-946d-656b8679313e`,
  `283be871-2d7d-4487-981b-6197b5c0ca34` (`S3358`, 16/22).
- `ui/src/components/ui/slider.tsx`: `78384b1e-cb48-4792-9e4d-83d13397e77e` (`S3358`, 18).
- `ui/src/engine/AudioEngine.test.ts`: `81b989d8-d7b7-4cde-ab13-365bdb938109`,
  `131b4e96-62ec-458b-9cbf-1f32bb8ffb9c`, `71ba2318-4892-4b40-bd8c-bfe992d243d3`
  (`S7755`, 87/96/111).
- `ui/src/lib/playerLogger.ts`: `142d6794-7555-445e-b7fc-4eec3565c7df` (`S7735`, 7).
- `ui/src/lib/runtimeConfig.ts`: `ef5b63fb-52a9-4045-a7a6-4393ed2c0e5b` (`S6606`, 18),
  `735134d1-ba9a-44d2-a304-72c18720eecf` (`S7735`, 18),
  `e3b692b5-9d38-4854-b19c-f0d2fff03095` (`S6660`, 38).
- `ui/src/pages/ReleasePage.tsx`: `7fe14539-0855-4ac2-8e8c-743c51ba2249`,
  `0369745c-98ac-4244-96f5-366be9685a55` (`S3863`, 1–2).
- `ui/src/pages/SettingsPage.tsx`: `a8c30ead-455e-4216-9ed5-3eff27a52f8c` (`S3776`, 62),
  `339abbd0-a0b9-4bbb-a751-792efd58f7fa`, `6d924e42-a440-4bdb-ac2e-aedca54b69f4`,
  `ebe54cc3-43c9-4043-8061-1d8bf86d9ca2`, `a552403d-fe5d-4d98-b49d-165bc282e5e1`,
  `43fe5945-429f-4ab6-bcee-eda5b2861c9c` (`S3358`, 114/116/118/125/153),
  `1b909510-0ae1-40d7-a296-a6dcc5e92e51` (`S7735`, 135).
- `ui/src/pages/ShelfPage.tsx`: `5069fb1a-7898-470b-9165-cf922559099c`,
  `ccf0dae7-cecf-4d9b-842b-da38d28c0a4f` (`S6479`, 31/127).
- `ui/src/store/playerStore.ts`: `36d06d76-93e4-475e-9465-c53ac1f4380f` (`S4325`, 149).

### Старая админка `app/ui.html` — замороженный видимый долг

- Security/HTML/CSS:
  `82210788-9353-4b16-9448-bd9fe21cf281` (`Web:S5725`, 7),
  `e0a32b0e-703c-4729-803b-d3b4cd717c82` (`css:S4666`, 354),
  `ab5b38d4-a21b-43aa-b362-8c49606509a8`, `a8f82132-f1cd-4ee0-be62-bc9c6303d0cb`,
  `87a10dfd-b0b2-439a-9587-4bb8f9efc750`, `72850462-0dd6-4084-be26-794247fde011`,
  `3b30a961-03dc-45b4-94f6-62a5f17317a5` (accessibility, 1087–1088).
- Exception/behavior:
  `d6f993a5-454d-4f62-b4d2-7e99224908b5`, `92ce43c7-9e4e-4608-80ed-97cbdcd595a4`,
  `40f9a4d5-3101-45d8-9d0c-0fdd7782c660`, `f47c6e21-fafa-4d43-b49f-4d93743c8eed`
  (`S2486`, 1297/1334/1976/3495),
  `4f8bdd42-a52e-4774-99a7-6279b2e52503`, `36e7db37-4194-4c57-9592-b9ea2950cf7b`
  (`S930`, 1613/1616),
  `7622e294-3a0d-489a-be4a-295e4c02d2a5` (`S3923`, 3748).
- Complexity:
  `8a20963a-9e46-4bf9-8a53-4d54c8849a3e` (`S3776`, 1584),
  `d8e1714b-66a8-48a3-bcd5-eef86f893999` (`S3776`, 4401).
- Nested ternary:
  `98c207eb-3977-4fc0-88b9-6b84cea243a8`, `b02819a8-0c64-485d-b06b-d921474304a9`,
  `0e7cb043-dbb9-41bf-bd13-f643a650e7b7`, `7eab1639-d68b-4e05-8a74-9bb6f48b2d26`,
  `926d35dc-c5f7-4b0f-b6e6-ed14c0ac48dd`, `29e1f81a-1cd7-4545-b6bb-f31b1fcdf61a`,
  `0b86602f-5e06-4e0e-9268-3759207a545a`, `154bb977-d449-4849-afbc-759981b9bb83`,
  `3ce71a24-6478-415f-97a0-58c6f7648d78`, `ffea81e6-db8f-4c64-8209-308785398e82`,
  `638b5d59-09f5-4e4d-be23-2014e765a41f`, `de86c390-bd57-4f58-800c-e90aa24e0e35`,
  `584d69be-398e-4576-b010-2468dee6a8b4`, `0166909d-ceb8-49f0-ba82-b98b1e7c2c7d`,
  `42cbb2ab-e251-4ec3-b0cc-9f2517a38e92`, `42906d8b-28ea-479b-bca5-e061aa414d5d`
  (`S3358`).
- Nested template:
  `a1505574-58a4-471f-927a-f1b950ff0f63`, `b87b66cf-9608-4aeb-9c82-31dc93a40fd4`,
  `a9967a9e-331b-4ae7-ad96-9d69d6308cdd`, `a10efa4d-7160-4c33-94fe-5188a1604214`,
  `751f00e3-7901-4184-8cf1-4d6f2d8ba228`, `bf50fc52-7fde-412f-9cee-066e099a3554`,
  `27953da4-0525-484c-88bc-bd9696f227b7`, `04583782-5e6e-46c0-b3c4-e8aa4207f272`,
  `4e22220d-e6f3-4aa7-94c1-1dc1611bbf75`, `36f27d8c-6863-461e-85d0-62d76fbefb15`,
  `4ae5c970-c6d0-412c-873a-195740abeae5` (`S4624`).
- Остальные локальные:
  `8aac7e99-227b-4c1d-a393-e2f3579124ef` (`S6644`, 1394),
  `08b1040d-22df-474d-b6d6-6d4d3e3232d8` (`S7780`, 1440),
  `d5c9d8e2-06c8-49bf-aa5f-42e5d5a29a0c`, `dfdbd5b0-fc28-4f95-b57a-ba6c956790ea`
  (`S7744`, 1841/2010),
  `57904777-3124-4da1-86e9-8a6cbb3475da`, `823035d4-6fe8-4f9a-9829-366d71c5f345`,
  `cdb9761a-8d03-4ea1-b368-273d63a23f16` (`S7778`, 2100–2102),
  `71b6e316-9c1f-4dcf-99bc-71661b938712`, `139c306b-c7d6-4a03-9310-167c257f5ccc`
  (`S7760`, 2418/2450),
  `0facfab7-1172-4918-ad54-c57eb2484e08`, `72a0487e-6852-47df-836e-e6b346162333`
  (`S7735`, 3069/4263),
  `f3cc230d-a6fb-4b9e-9d92-da8217557ccf` (`S7764`, 4504),
  `b6ba404b-50f6-4289-9859-e77eb644b5cc` (`S7785`, 4563).

## Приложение B. Security Hotspots

### Требуют review

- `6550f731-6547-454d-8810-5073f0460bd3` — `deploy/ci/Dockerfile.ui-test`,
  `docker:S6471`; оставить `TO_REVIEW`, исправление отложено.
- `1c29092e-24a1-481a-b049-fa334e4a9c64` — `app/services/dashboard.py`,
  `python:S2245`; принято решение `SAFE`, статус выставляет Саня.
- `a24e89a3-82ef-4e93-ad11-fdf816782d96` — `deploy/worker/Dockerfile`,
  `docker:S5332`; принято решение `ACKNOWLEDGED`, статус выставляет Саня.
- `04a845ed-ddd6-4382-b08f-1ae71aaf27c8` —
  `discocs_bot/bot/services/navidrome.py`, `python:S4790`; принято решение
  `ACKNOWLEDGED`, статус выставляет Саня.

### Уже рассмотрены; сохранять как контрольный журнал

- `2d07a4f2-4acd-46e7-8d52-9c8214d12a86` — ACKNOWLEDGED.
- `7b76cebe-073d-42b2-957e-e64831af296f` — ACKNOWLEDGED.
- `99b6e087-3813-4966-b3c5-18ddbde67eb5` — ACKNOWLEDGED.
- `b9815f11-2854-484d-b2ef-f2d0371dd55c` — ACKNOWLEDGED.
- `1078c76f-308e-48f2-9a2a-c2740412ce6d` — ACKNOWLEDGED.
- `5b193b0a-bc5d-425d-8497-873fd3dc9446` — ACKNOWLEDGED.
- `9df9c4b4-02a6-4b9f-a588-8b7e1669514d` — SAFE.
- `5701bfa7-c75f-4c4c-af26-5113c9f323a8` — SAFE.
- `0f7fe2bb-6b06-4a18-91de-7bab13f61a55` — SAFE.
- `65159205-6920-4b9b-b198-c34a9eb32f89` — SAFE.
- `33f8d43a-7ce7-4189-a29a-7fbb03709999` — ACKNOWLEDGED.
