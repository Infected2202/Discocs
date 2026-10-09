"""Инструменты рабочей машины (docs/tools.md): очередь задач, воркер, итоги агента описаний лейблов."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store.label_merge import _fold
from tests.test_labels import add_release, init_api_store


def _label(store, tmp_path, name: str, releases: int, *, artist: str = "Artist") -> int:
    for i in range(releases):
        add_release(store, tmp_path, f"{name} {i}", (name,), artist=artist)
    label_id = store.label_id_by_name(name)
    assert label_id is not None
    return label_id


def _description(client: TestClient, label_id: int) -> tuple[str | None, str | None]:
    description = client.get(f"/api/v1/labels/{label_id}").json()["label"]["description"]
    if description is None:
        return None, None
    return "".join(segment["text"] for segment in description["segments"]), description["source"]


def _sync(client: TestClient, name: str, text: str, source: str = "wikipedia_en") -> None:
    response = client.put("/api/v1/labels/metadata", json={"name": name, "description": text,
                                                           "description_source": source})
    assert response.status_code == 200


def test_worker_poll_without_jobs_returns_none_and_marks_worker_online(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post("/api/v1/tools/workers/pc-1/poll",
                           json={"tools": ["describe"], "state": {"vram_used_mb": 5000}, "wait": 0})

    assert response.status_code == 200
    assert response.json() == {"job": None}
    workers = client.get("/api/v1/tools").json()["workers"]
    assert [(w["id"], w["online"], w["state"]) for w in workers] == [("pc-1", True, {"vram_used_mb": 5000})]
    assert client.post("/api/v1/tools/workers/bad id!/poll", json={}).status_code == 400


def test_describe_job_runs_from_admin_to_worker_results(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    small = _label(store, tmp_path, "Small", 1)
    big = _label(store, tmp_path, "Big", 3, artist="Nina Kraviz")
    described = _label(store, tmp_path, "Described", 2)
    client = TestClient(app)
    _sync(client, "Described", "From Wikipedia.")

    created = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run", "scope": "missing"})

    assert created.status_code == 200
    job = created.json()["job"]
    # Только лейблы без описания, крупные первыми; прогресс знает, сколько всего.
    assert job["params"]["label_ids"] == [big, small]
    assert client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"}).status_code == 409

    # Воркер, который умеет только music-fill, задачу describe не получает.
    other = client.post("/api/v1/tools/workers/pc-2/poll", json={"tools": ["music-fill"], "wait": 0})
    assert other.json()["job"] is None
    polled = client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0}).json()["job"]
    assert (polled["id"], polled["status"], polled["worker_id"]) == (job["id"], "running", "pc-1")

    batch = client.post(f"/api/v1/tools/jobs/{job['id']}/describe/next", json={"limit": 5}).json()["labels"]
    assert [label["id"] for label in batch] == [big, small]
    assert batch[0]["name"] == "Big"
    assert batch[0]["artists"] == ["Nina Kraviz"]
    assert sorted(batch[0]["releases"]) == ["Big 0 (2020)", "Big 1 (2020)", "Big 2 (2020)"]
    # Уже взятые в работу воркер исключает сам.
    rest = client.post(f"/api/v1/tools/jobs/{job['id']}/describe/next", json={"limit": 5, "exclude": [big]})
    assert [label["id"] for label in rest.json()["labels"]] == [small]

    written = client.post("/api/v1/tools/describe/results", json={
        "job_id": job["id"], "label_id": big, "status": "written",
        "description": "Лейбл [a=Nina Kraviz].", "sources": [{"url": "https://ra.co/labels/big"}],
        "model": "gemma",
    })
    assert written.json() == {"outcome": "written"}
    assert _description(client, big) == ("Лейбл Nina Kraviz.", "agent")
    # Сколько сделано, считает сервер по итогам — счётчики воркера обнуляются при его перезапуске.
    running = client.get("/api/v1/tools/describe").json()["job"]
    assert running["counts"] == {"total": 2, "done": 1, "written": 1, "not_found": 0, "failed": 0, "skipped": 0}
    assert "label_ids" not in running["params"]
    nothing = client.post("/api/v1/tools/describe/results",
                          json={"job_id": job["id"], "label_id": small, "status": "not_found"})
    assert nothing.json() == {"outcome": "not_found"}
    assert _description(client, small) == (None, None)

    # Итог есть по обоим — больше раздавать нечего.
    assert client.post(f"/api/v1/tools/jobs/{job['id']}/describe/next", json={}).json()["labels"] == []
    progress = client.post(f"/api/v1/tools/jobs/{job['id']}/progress",
                           json={"status": "done", "progress": {"total": 2, "done": 2}})
    assert progress.json() == {"control": "run"}
    overview = client.get("/api/v1/tools/describe").json()
    assert overview["job"] is None
    assert overview["stats"]["agent"]["written"] == 1
    assert overview["stats"]["agent"]["not_found"] == 1
    assert overview["stats"]["by_source"] == {"agent": 1, "wikipedia_en": 1}
    assert overview["stats"]["missing"] == {"1": 1, "2": 0, "3+": 0}
    assert [item["label_id"] for item in overview["recent"]] == [big]
    assert overview["recent"][0]["sources"] == [{"url": "https://ra.co/labels/big"}]
    assert described not in job["params"]["label_ids"]


def test_worker_restart_resumes_its_job_and_admin_controls_reach_it(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _label(store, tmp_path, "One", 1)
    client = TestClient(app)
    job_id = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"}).json()["job"]["id"]
    client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0})

    # Перезапущенный воркер получает свою незаконченную задачу, другой воркер — нет.
    again = client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0}).json()["job"]
    assert again["id"] == job_id
    other = client.post("/api/v1/tools/workers/pc-2/poll", json={"tools": ["describe"], "wait": 0}).json()["job"]
    assert other is None

    client.post(f"/api/v1/tools/jobs/{job_id}/control", json={"control": "pause"})
    assert client.post(f"/api/v1/tools/jobs/{job_id}/progress", json={"status": "paused"}).json() == {"control": "pause"}
    client.post(f"/api/v1/tools/jobs/{job_id}/control", json={"control": "cancel"})
    assert client.post(f"/api/v1/tools/jobs/{job_id}/progress", json={}).json() == {"control": "cancel"}
    client.post(f"/api/v1/tools/jobs/{job_id}/progress", json={"status": "cancelled"})
    # Закрытая задача не воскресает от запоздалого прогресса и больше не раздаётся.
    assert client.post(f"/api/v1/tools/jobs/{job_id}/progress", json={"status": "running"}).json() == {"control": "cancel"}
    assert client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0}).json()["job"] is None
    assert client.post("/api/v1/tools/jobs/999/progress", json={}).status_code == 404


def test_queued_job_cancelled_before_any_worker_took_it(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _label(store, tmp_path, "One", 1)
    client = TestClient(app)
    job_id = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"}).json()["job"]["id"]

    cancelled = client.post(f"/api/v1/tools/jobs/{job_id}/control", json={"control": "cancel"}).json()["job"]

    assert cancelled["status"] == "cancelled"
    assert client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0}).json()["job"] is None


def test_agent_never_overwrites_editorial_and_label_sync_never_overwrites_agent(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    editorial = _label(store, tmp_path, "Hand", 1)
    synced = _label(store, tmp_path, "Synced", 1)
    client = TestClient(app)
    client.put(f"/api/v1/labels/{editorial}/description", json={"description": "Написано вручную."})
    _sync(client, "Synced", "Discogs profile.", "discogs")

    skipped = client.post("/api/v1/tools/describe/results",
                          json={"label_id": editorial, "status": "written", "description": "Текст агента."})
    replaced = client.post("/api/v1/tools/describe/results",
                           json={"label_id": synced, "status": "written", "description": "Текст агента."})

    assert skipped.json() == {"outcome": "skipped"}
    assert _description(client, editorial) == ("Написано вручную.", "editorial")
    assert replaced.json() == {"outcome": "written"}
    # Повторная синхронизация лейблов текст агента не трогает.
    _sync(client, "Synced", "Discogs profile, updated.", "discogs")
    assert _description(client, synced) == ("Текст агента.", "agent")
    assert client.post("/api/v1/tools/describe/results",
                       json={"label_id": 99999, "status": "not_found"}).status_code == 404


def test_not_found_keeps_existing_description_and_revert_restores_text_before_agent(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    label = _label(store, tmp_path, "Wiki", 1)
    client = TestClient(app)
    _sync(client, "Wiki", "From Wikipedia.")

    client.post("/api/v1/tools/describe/results", json={"label_id": label, "status": "not_found"})
    assert _description(client, label) == ("From Wikipedia.", "wikipedia_en")

    client.post("/api/v1/tools/describe/results", json={"label_id": label, "status": "written", "description": "Первый."})
    client.post("/api/v1/tools/describe/results", json={"label_id": label, "status": "written", "description": "Второй."})
    # Перегенерация, которая ничего не нашла, написанное не стирает.
    client.post("/api/v1/tools/describe/results", json={"label_id": label, "status": "not_found"})
    assert _description(client, label) == ("Второй.", "agent")

    reverted = client.post(f"/api/v1/tools/describe/{label}/revert")

    assert reverted.json() == {"reverted": True}
    # Откат — к тексту до агента, а не к первому тексту агента.
    assert _description(client, label) == ("From Wikipedia.", "wikipedia_en")
    assert client.post(f"/api/v1/tools/describe/{label}/revert").status_code == 409


def test_describe_scopes_pick_the_right_labels(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    empty = _label(store, tmp_path, "Empty", 3)
    wiki = _label(store, tmp_path, "Wiki", 2)
    hand = _label(store, tmp_path, "Hand", 2)
    agent = _label(store, tmp_path, "Agent", 1)
    tried = _label(store, tmp_path, "Tried", 1)
    selfie = _label(store, tmp_path, "Independent", 4)
    client = TestClient(app)
    _sync(client, "Wiki", "From Wikipedia.")
    client.put(f"/api/v1/labels/{hand}/description", json={"description": "Вручную."})
    store.save_agent_description(agent, job_id=None, status="written", description="Агент.")
    store.save_agent_description(tried, job_id=None, status="not_found")
    store.set_label_sync_state(selfie, "self_released", keys_hash=None)

    assert store.describe_label_ids("missing") == [empty]
    assert store.describe_label_ids("missing", min_releases=4) == []
    assert store.describe_label_ids("replace") == [empty, wiki]
    assert store.describe_label_ids("not_found") == [tried]
    # Выбранные вручную — хоть с текстом агента, но не редакционные и не самовыпуски.
    assert store.describe_label_ids("ids", label_ids=[agent, hand, selfie, wiki]) == [wiki, agent]
    stats = store.describe_stats()
    assert stats["self_released"] == 1
    assert stats["replaceable"] == 1
    assert stats["labels"] == 5


def test_merge_keeps_the_weightier_description(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    main = _label(store, tmp_path, "Main", 2)
    other = _label(store, tmp_path, "Other", 1)
    client = TestClient(app)
    _sync(client, "Main", "Discogs profile.", "discogs")
    store.save_agent_description(other, job_id=None, status="written", description="Агент.")

    with store.connect() as conn:
        _fold(conn, main, other)

    # Текст агента весомее найденного синхронизацией; его запись переехала — откат работает и после склейки.
    assert _description(client, main) == ("Агент.", "agent")
    assert [item["label_id"] for item in store.agent_descriptions()] == [main]

    third = _label(store, tmp_path, "Third", 1)
    store.save_agent_description(third, job_id=None, status="written", description="Ещё агент.")
    client.put(f"/api/v1/labels/{main}/description", json={"description": "Вручную."})
    with store.connect() as conn:
        _fold(conn, main, third)
    assert _description(client, main) == ("Вручную.", "editorial")


def test_music_fill_jobs_are_start_and_stop_only(tmp_path, monkeypatch):
    init_api_store(tmp_path, monkeypatch)
    client = TestClient(app)

    start = client.post("/api/v1/tools/jobs", json={"tool": "music-fill", "action": "start"})
    bad = client.post("/api/v1/tools/jobs", json={"tool": "music-fill", "action": "run"})
    nothing = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"})

    assert start.status_code == 200
    assert start.json()["job"]["action"] == "start"
    assert bad.status_code == 400
    # Лейблов нет — запускать describe не на чем.
    assert nothing.status_code == 400
    job = client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["music-fill"], "wait": 0}).json()["job"]
    assert (job["tool"], job["action"]) == ("music-fill", "start")


def test_admin_has_a_tools_section_wired_to_the_tools_api():
    response = TestClient(app).get("/admin")

    assert response.status_code == 200
    page = response.text
    assert """data-nav="tools" onclick="showSection('tools')\"""" in page
    assert '<section id="tools" class="section">' in page
    # Статистика и текущая задача, запуск, пауза/отмена, перегенерация и откат — через API инструментов.
    assert '"/api/v1/tools/describe?limit=30"' in page
    assert 'toolsApi("/api/v1/tools/jobs"' in page
    assert "/api/v1/tools/jobs/${jobId}/control" in page
    assert "/api/v1/tools/describe/${labelId}/revert" in page
    assert "toolJob('music-fill', 'start')" in page


def test_cancel_closes_a_job_whose_worker_is_gone_and_a_restarted_worker_drops_its_cancelled_job(
    tmp_path, monkeypatch
):
    store = init_api_store(tmp_path, monkeypatch)
    _label(store, tmp_path, "One", 1)
    client = TestClient(app)
    first = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"}).json()["job"]["id"]
    client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0})
    # Воркер давно не на связи — отмену подтвердить некому, задача закрывается сразу и не мешает новой.
    with store.connect() as conn:
        conn.execute("UPDATE tool_workers SET seen_at = '2020-01-01T00:00:00+00:00'")

    cancelled = client.post(f"/api/v1/tools/jobs/{first}/control", json={"control": "cancel"}).json()["job"]

    assert cancelled["status"] == "cancelled"
    second = client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"}).json()["job"]["id"]
    client.post("/api/v1/tools/workers/pc-2/poll", json={"tools": ["describe"], "wait": 0})
    # Воркер на связи — отмену он подтверждает сам; но если он перезапустится раньше, свою отменённую
    # задачу закроет при первом же опросе, а не повиснет на ней.
    client.post(f"/api/v1/tools/jobs/{second}/control", json={"control": "cancel"})
    assert store.tool_job(second)["status"] == "running"
    polled = client.post("/api/v1/tools/workers/pc-2/poll", json={"tools": ["describe"], "wait": 0}).json()["job"]
    assert polled is None
    assert store.tool_job(second)["status"] == "cancelled"


def test_describe_overview_says_whether_the_worker_running_the_job_is_online(tmp_path, monkeypatch):
    store = init_api_store(tmp_path, monkeypatch)
    _label(store, tmp_path, "One", 1)
    client = TestClient(app)
    client.post("/api/v1/tools/jobs", json={"tool": "describe", "action": "run"})
    # В очереди — воркера ещё нет.
    queued = client.get("/api/v1/tools/describe").json()["job"]
    assert (queued["status"], queued["worker_online"], queued["worker_seen_at"]) == ("queued", False, None)

    client.post("/api/v1/tools/workers/pc-1/poll", json={"tools": ["describe"], "wait": 0})
    running = client.get("/api/v1/tools/describe").json()["job"]
    assert (running["status"], running["worker_online"]) == ("running", True)

    # Воркер пропал: статус в базе всё ещё running, но админка должна знать, что задачу никто не выполняет.
    with store.connect() as conn:
        conn.execute("UPDATE tool_workers SET seen_at = '2020-01-01T00:00:00+00:00'")
    stalled = client.get("/api/v1/tools/describe").json()["job"]
    assert (stalled["status"], stalled["worker_online"]) == ("running", False)
    assert stalled["worker_seen_at"] == "2020-01-01T00:00:00+00:00"
    assert "not running: the worker is offline" in client.get("/admin").text
