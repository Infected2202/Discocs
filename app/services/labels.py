"""Labels service: картинки и метаданные лейблов (app/services/label_sync) и разбор описаний."""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path

from app.library import normalize_text
from app.models import LabelMetadata
from app.store import Store

PLACEHOLDER_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "label-placeholder.jpg"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
_EXTENSION_MEDIA_TYPES = dict(_IMAGE_FORMATS.values())

# Упоминание артиста в описании: «[a=Nina Kraviz]». Скрипт сводит к нему
# разметку Discogs, остальное приходит обычным текстом.
_ARTIST_MENTION_RE = re.compile(r"\[a=([^\]\n]{1,200})\]")


class LabelImageError(ValueError):
    pass


@dataclass(frozen=True)
class DecodedImage:
    payload: bytes
    extension: str


def decode_label_image(image_base64: str) -> DecodedImage:
    """base64 → проверенная картинка (JPEG/PNG/WebP), иначе LabelImageError."""
    try:
        payload = base64.b64decode(image_base64, validate=True)
    except ValueError as exc:  # binascii.Error и не-ASCII строка — оба ValueError
        raise LabelImageError("Image is not valid base64") from exc
    return decode_label_image_bytes(payload)


def decode_label_image_bytes(payload: bytes) -> DecodedImage:
    """Скачанная картинка → проверенная (JPEG/PNG/WebP), иначе LabelImageError."""
    if not payload:
        raise LabelImageError("Image is empty")
    if len(payload) > MAX_IMAGE_BYTES:
        raise LabelImageError("Image is too large")
    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(io.BytesIO(payload)) as image:
            image_format = image.format
            image.verify()
    except (OSError, SyntaxError) as exc:  # UnidentifiedImageError — подкласс OSError
        raise LabelImageError("Image is not a readable picture") from exc
    if image_format not in _IMAGE_FORMATS:
        raise LabelImageError(f"Unsupported image format: {image_format}")
    return DecodedImage(payload=payload, extension=_IMAGE_FORMATS[image_format][0])


def save_label_metadata(
    store: Store,
    data_dir: Path,
    metadata: LabelMetadata,
    image: DecodedImage | None,
) -> int:
    label_id = store.save_label_metadata(metadata)
    if image is not None:
        images_dir = data_dir / "label_images"
        images_dir.mkdir(parents=True, exist_ok=True)
        target = images_dir / f"{label_id}.{image.extension}"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(image.payload)
        tmp.replace(target)
        for stale in images_dir.glob(f"{label_id}.*"):
            if stale != target:
                stale.unlink(missing_ok=True)
        store.set_label_image(label_id, str(target), metadata.image_source)
    return label_id


def label_image_file(image_path: str | None) -> tuple[Path, str]:
    """Файл картинки лейбла и его media type; нет своей — заглушка Beatport."""
    if image_path:
        path = Path(image_path)
        media_type = _EXTENSION_MEDIA_TYPES.get(path.suffix.lstrip(".").lower())
        if media_type and path.is_file():
            return path, media_type
    return PLACEHOLDER_IMAGE, "image/jpeg"


def description_segments(store: Store, text: str) -> list[dict[str, object]]:
    """Описание → сегменты: текст и упоминания артистов (со ссылкой, если артист есть в библиотеке)."""
    names = [match.group(1).strip() for match in _ARTIST_MENTION_RE.finditer(text)]
    artist_ids = store.artist_ids_by_names(names) if names else {}
    segments: list[dict[str, object]] = []
    position = 0
    for match in _ARTIST_MENTION_RE.finditer(text):
        if match.start() > position:
            segments.append({"type": "text", "text": text[position:match.start()]})
        name = match.group(1).strip()
        segments.append({
            "type": "artist",
            "text": name,
            "artist_id": artist_ids.get(normalize_text(name)),
        })
        position = match.end()
    if position < len(text):
        segments.append({"type": "text", "text": text[position:]})
    return segments
