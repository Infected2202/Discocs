"""Background dashboard mix generation preserves the requesting user scope."""
from pathlib import Path
from types import SimpleNamespace

from app.services import dashboard


def test_background_mix_store_keeps_user_id(monkeypatch, tmp_path: Path):
    constructed: list[tuple[Path, int]] = []

    class FakeStore:
        def __init__(self, db_path, *, user_id):
            constructed.append((db_path, user_id))

        def init(self):
            pass

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(dashboard, "Store", FakeStore)
    monkeypatch.setattr(dashboard, "Thread", ImmediateThread)
    monkeypatch.setattr(
        dashboard,
        "ensure_dashboard_mixes",
        lambda *_args: SimpleNamespace(
            diagnostics={"generated_count": 0, "reason": "test"}
        ),
    )
    monkeypatch.setattr(dashboard, "_generated_mix_settings", lambda _settings: {})
    settings = SimpleNamespace(data_dir=tmp_path)

    assert dashboard._start_dashboard_mix_generation(tmp_path / "app.db", settings, 42)
    assert constructed == [(tmp_path / "app.db", 42)]
