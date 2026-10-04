"""Labels API routes: список и страница лейбла, картинка, приём метаданных извне."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import api_error, context
from app.models import LabelMetadata
from app.schemas.requests import LabelDescriptionRequest, LabelMetadataRequest
from app.serializers.entities import release_summary_dict, sort_by_popularity
from app.serializers.labels import label_detail_dict, label_summary_dict
from app.store import group_label_releases
from app.services.labels import (
    LabelImageError,
    decode_label_image,
    label_image_file,
    save_label_metadata,
)

router = APIRouter(prefix="/api/v1")

_LABEL_NOT_FOUND = "Label not found"


@router.get("/labels", response_model=None)
def api_v1_labels(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    store, _settings = context()
    labels, total = store.list_labels(limit=limit, offset=offset)
    return {
        "items": [label_summary_dict(label) for label in labels],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


@router.get("/labels/{label_id}", response_model=None)
def api_v1_label(label_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    label = store.get_label(label_id)
    if label is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    return {
        "label": label_detail_dict(store, label),
        "links": {
            "releases": f"/api/v1/labels/{label_id}/releases",
            "image": f"/api/v1/labels/{label_id}/image",
        },
    }


@router.get("/labels/{label_id}/releases", response_model=None)
def api_v1_label_releases(
    label_id: int,
    sort: Annotated[
        str, Query(pattern="^(release_date_desc|release_date_asc|popularity)$")
    ] = "release_date_desc",
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    label = store.get_label(label_id)
    if label is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    releases = store.label_releases(label_id, newest_first=sort != "release_date_asc")
    fans = store.release_popularity([row.release.id for row in releases])
    if sort == "popularity":
        releases = sort_by_popularity(releases, fans)

    def item(row) -> dict[str, object]:
        return {**release_summary_dict(row), "deezer_fans": fans.get(row.release.id)}

    return {
        "label": {"id": label.id, "name": label.name},
        "sort": sort,
        # Группы по типу релиза — для страницы лейбла; плоский items — для внешних клиентов.
        "groups": [
            {"key": key, "items": [item(row) for row in rows]}
            for key, rows in group_label_releases(releases)
        ],
        "items": [item(row) for row in releases],
    }


@router.put("/labels/{label_id}/like", response_model=None)
def api_v1_like_label(label_id: int) -> dict[str, object] | JSONResponse:
    return _set_label_like(label_id, True)


@router.delete("/labels/{label_id}/like", response_model=None)
def api_v1_unlike_label(label_id: int) -> dict[str, object] | JSONResponse:
    return _set_label_like(label_id, False)


def _set_label_like(label_id: int, liked: bool) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    if store.user_id is None:
        # Сервисный токен — не пользователь, лайкать ему нечем.
        return api_error(403, "forbidden", "Likes require a signed-in user")
    if store.get_label(label_id) is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    store.set_label_liked(label_id, liked)
    return {"label_id": label_id, "liked": liked}


@router.get("/labels/{label_id}/image", response_model=None)
def api_v1_label_image(label_id: int) -> FileResponse | JSONResponse:
    store, _settings = context()
    label = store.get_label(label_id)
    if label is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    path, media_type = label_image_file(label.image_path)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.put("/labels/metadata", response_model=None)
def api_v1_put_label_metadata(request: LabelMetadataRequest) -> dict[str, object] | JSONResponse:
    """Картинка, описание и ссылки лейбла извне (обычно их пишет синхронизация в админке); лейбл — по названию."""
    store, settings = context()
    image = None
    if request.image_base64:
        try:
            image = decode_label_image(request.image_base64)
        except LabelImageError as exc:
            return api_error(400, "invalid_image", str(exc))
    metadata = LabelMetadata(
        name=request.name,
        image_source=request.image_source,
        description=request.description,
        description_source=request.description_source,
        links=[link.model_dump(exclude_none=True) for link in request.links],
        external_ids=dict(request.external_ids),
    )
    try:
        label_id = save_label_metadata(store, settings.data_dir, metadata, image)
    except ValueError as exc:
        return api_error(400, "invalid_label", str(exc))
    label = store.get_label(label_id)
    if label is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    return {"label": label_detail_dict(store, label)}


@router.put("/labels/{label_id}/description", response_model=None)
def api_v1_put_label_description(
    label_id: int, request: LabelDescriptionRequest
) -> dict[str, object] | JSONResponse:
    """Описание, написанное вручную: синхронизация лейблов его не перезаписывает; пустое — снимает защиту."""
    store, _settings = context()
    if store.get_label(label_id) is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    store.set_label_description(label_id, request.description)
    label = store.get_label(label_id)
    if label is None:
        return api_error(404, "not_found", _LABEL_NOT_FOUND)
    return {"label": label_detail_dict(store, label)}
