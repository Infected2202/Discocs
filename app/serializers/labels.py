"""Label serializers."""
from __future__ import annotations

from urllib.parse import quote

from app.models import Label
from app.serializers.search import dashboard_shelf_item
from app.services.labels import description_segments
from app.store import Store


def label_image_url(label: Label) -> str:
    # Версия в URL — чтобы новая картинка из label-sync не пряталась за кэшем браузера.
    version = quote(label.metadata_synced_at or "0", safe="")
    return f"/api/v1/labels/{label.id}/image?v={version}"


def label_artwork(label: Label) -> dict[str, object]:
    has_image = bool(label.image_path)
    return {
        "url": label_image_url(label),
        "source": (label.image_source or "label") if has_image else "placeholder",
        "placeholder": not has_image,
    }


def label_summary_dict(label: Label) -> dict[str, object]:
    return {
        "id": label.id,
        "name": label.name,
        "release_count": label.release_count,
        "liked": label.liked,
        "artwork": label_artwork(label),
        "top_genres": list(label.top_genres),
    }


def label_detail_dict(store: Store, label: Label) -> dict[str, object]:
    description = None
    if label.description:
        description = {
            "segments": description_segments(store, label.description),
            "source": label.description_source,
        }
    return {
        **label_summary_dict(label),
        "genres": [{"name": name, "release_count": count} for name, count in store.label_genres(label.id)],
        "description": description,
        "links": [
            {"url": str(link["url"]), "title": link.get("title")}
            for link in label.links
            if isinstance(link.get("url"), str)
        ],
    }


def label_shelf_item(label: Label) -> dict[str, object]:
    item = dashboard_shelf_item(
        "label",
        label.id,
        label.name,
        "",
        f"/labels/{label.id}",
        badges=[],
    )
    item["artwork"] = label_artwork(label)
    item["release_count"] = label.release_count
    item["top_genres"] = list(label.top_genres)
    # Лейбл не источник воспроизведения — на карточке нет кнопки Play.
    item["play_action"] = None
    return item
