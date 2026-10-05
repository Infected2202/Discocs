"""Shared rules of horizontal shelves and their full lists (docs/web-ui.md «Полки»).

Every horizontal shelf of the UI previews the same number of cards —
``SHELF_PREVIEW_LIMIT`` (two slider pages at the widest 8-column layout); the
UI has the same constant (``ui/src/lib/shelves.ts``). A shelf whose source has
more than that links to its full list, which pages through the same source
with ``limit``/``offset``; ``page_info`` builds the paging fields every such
endpoint returns.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

SHELF_PREVIEW_LIMIT = 16
# Upper bound of ``limit`` on full-list endpoints.
FULL_LIST_MAX_LIMIT = 100

_T = TypeVar("_T")


def page_info(total: int, limit: int, offset: int) -> dict[str, int | None]:
    """``total``/``limit``/``offset``/``next_offset`` of one page of a list."""
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def page_of(items: Sequence[_T], limit: int, offset: int) -> tuple[list[_T], dict[str, int | None]]:
    """Slice an already complete list into one page plus its paging fields."""
    return list(items[offset : offset + limit]), page_info(len(items), limit, offset)
