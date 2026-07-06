from __future__ import annotations

import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import json
import logging
import os
from pathlib import Path
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from time import perf_counter
from typing import Annotated, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urljoin

import typer
import numpy as np

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import track_audio_path
from app.config import MUQ_MULAN_MODEL, Settings
from app.embedder import (
    DiscogsEffnetEmbedder,
    MuqMulanEmbedder,
    create_track_embedder,
    download_muq_mulan_model,
    pool_and_normalize,
)
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadOutput,
    download_head_pack_models,
    head_pack_readiness,
)
from app.logging_config import configure_logging, get_analysis_logger
from app.navidrome import NavidromeClient
from app.navidrome_migration import (
    migrate_navidrome_duplicate_tracks,
    repair_navidrome_empty_releases,
)
from app.navidrome_sync import sync_navidrome_catalog
from app.recommender import Recommender, build_index
from app.scanner import AUDIO_EXTENSIONS, iter_audio_files, scan_music_folder
from app.store import Store, similar_track_dict


cli = typer.Typer(no_args_is_help=True)
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()

DISCOGS_EMBEDDING_MODELS = [
    "discogs_multi",
    "discogs_track",
    "discogs_release",
    "discogs_label",
]
EMBEDDING_MODELS = [
    *DISCOGS_EMBEDDING_MODELS,
    MUQ_MULAN_MODEL,
]


def get_store_and_settings() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    return store, settings


@cli.command("db-check")
def db_check(
    full: Annotated[
        bool,
        typer.Option("--full", help="Run PRAGMA integrity_check instead of quick_check."),
    ] = False,
) -> None:
    """Check the SQLite database from the application runtime host."""
    store, settings = get_store_and_settings()
    pragma = "integrity_check" if full else "quick_check"
    typer.echo(f"db_path={settings.db_path}")
    with store.connect() as conn:
        check_rows = conn.execute(f"PRAGMA {pragma}").fetchall()
        ok = len(check_rows) == 1 and str(check_rows[0][0]).lower() == "ok"
        typer.echo(f"{pragma}={check_rows[0][0] if check_rows else '<no rows>'}")
        if not ok:
            for row in check_rows[1:20]:
                typer.echo(str(row[0]))
            raise typer.Exit(1)

        table_checks = [
            ("tracks", "SELECT COUNT(*) FROM tracks"),
            ("embeddings", "SELECT COUNT(*) FROM embeddings"),
            ("track_features", "SELECT COUNT(*) FROM track_features"),
            ("analysis_jobs", "SELECT COUNT(*) FROM analysis_jobs"),
            ("analysis_tasks", "SELECT COUNT(*) FROM analysis_tasks"),
            ("analysis_tasks_not_indexed", "SELECT COUNT(*) FROM analysis_tasks NOT INDEXED"),
            ("external_tracks", "SELECT COUNT(*) FROM external_tracks"),
            ("external_tracks_not_indexed", "SELECT COUNT(*) FROM external_tracks NOT INDEXED"),
        ]
        counts: dict[str, int] = {}
        for label, sql in table_checks:
            counts[label] = int(conn.execute(sql).fetchone()[0])
            typer.echo(f"{label}={counts[label]}")

    if counts["analysis_tasks"] != counts["analysis_tasks_not_indexed"]:
        typer.echo("analysis_tasks index/table count mismatch", err=True)
        raise typer.Exit(1)
    if counts["external_tracks"] != counts["external_tracks_not_indexed"]:
        typer.echo("external_tracks index/table count mismatch", err=True)
        raise typer.Exit(1)
    typer.echo("db_check=ok")


@cli.command("db-diagnose-instant-mix")
def db_diagnose_instant_mix(
    root_pages: Annotated[
        str,
        typer.Option(
            "--root-pages",
            help="Comma-separated rootpage numbers from PRAGMA integrity_check, e.g. 695446,57842.",
        ),
    ] = "",
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
) -> None:
    """Read-only diagnostics for Instant Mix history and SQLite b-tree objects."""
    settings = Settings.from_env()
    db_path = settings.db_path
    typer.echo(f"db_path={db_path}")
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        typer.echo(f"{path.name}_exists={path.exists()} size={path.stat().st_size if path.exists() else 0}")

    rootpage_values = [
        int(part.strip())
        for part in root_pages.split(",")
        if part.strip()
    ]
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        typer.echo(f"journal_mode={conn.execute('PRAGMA journal_mode').fetchone()[0]}")
        typer.echo(f"page_count={conn.execute('PRAGMA page_count').fetchone()[0]}")
        typer.echo(f"freelist_count={conn.execute('PRAGMA freelist_count').fetchone()[0]}")
        typer.echo("instant_mix_schema_objects:")
        for row in conn.execute(
            """
            SELECT type, name, tbl_name, rootpage, sql
            FROM sqlite_master
            WHERE tbl_name = 'instant_mix_requests' OR name LIKE '%instant_mix%'
            ORDER BY type, name
            """
        ):
            typer.echo(
                f"  rootpage={row['rootpage']} type={row['type']} name={row['name']} tbl={row['tbl_name']}"
            )

        if rootpage_values:
            placeholders = ",".join("?" for _ in rootpage_values)
            typer.echo("rootpage_lookup:")
            rows = conn.execute(
                f"""
                SELECT type, name, tbl_name, rootpage, sql
                FROM sqlite_master
                WHERE rootpage IN ({placeholders})
                ORDER BY rootpage
                """,
                rootpage_values,
            ).fetchall()
            if rows:
                for row in rows:
                    typer.echo(
                        f"  rootpage={row['rootpage']} type={row['type']} name={row['name']} tbl={row['tbl_name']}"
                    )
            else:
                typer.echo("  <no sqlite_master object matched these rootpages>")

        for label, sql in (
            ("instant_mix_count_indexed", "SELECT COUNT(*) FROM instant_mix_requests"),
            ("instant_mix_count_not_indexed", "SELECT COUNT(*) FROM instant_mix_requests NOT INDEXED"),
        ):
            try:
                typer.echo(f"{label}={conn.execute(sql).fetchone()[0]}")
            except sqlite3.DatabaseError as exc:
                typer.echo(f"{label}_error={exc}")

        typer.echo("instant_mix_recent_not_indexed:")
        try:
            rows = conn.execute(
                """
                SELECT rowid, id, created_at, model_name, seed_item_id, seed_track_id, status,
                       result_count, requested_count, effective_count, params_json
                FROM instant_mix_requests NOT INDEXED
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            _echo_instant_mix_rows(rows)
        except sqlite3.DatabaseError as exc:
            typer.echo(f"instant_mix_recent_not_indexed_error={exc}")

        typer.echo("instant_mix_rowid_probe:")
        try:
            min_rowid, max_rowid = conn.execute(
                "SELECT MIN(rowid), MAX(rowid) FROM instant_mix_requests NOT INDEXED"
            ).fetchone()
            typer.echo(f"  min_rowid={min_rowid} max_rowid={max_rowid}")
        except sqlite3.DatabaseError as exc:
            typer.echo(f"  rowid_bounds_error={exc}")
            min_rowid = max_rowid = None

        if min_rowid is not None and max_rowid is not None:
            printed = 0
            bad_rowids: list[int] = []
            for rowid in range(int(min_rowid), int(max_rowid) + 1):
                try:
                    rows = conn.execute(
                        """
                        SELECT rowid, id, created_at, model_name, seed_item_id, seed_track_id, status,
                               result_count, requested_count, effective_count, params_json
                        FROM instant_mix_requests NOT INDEXED
                        WHERE rowid = ?
                        """,
                        (rowid,),
                    ).fetchall()
                except sqlite3.DatabaseError:
                    bad_rowids.append(rowid)
                    continue
                if rows and printed < limit:
                    _echo_instant_mix_rows(rows)
                    printed += len(rows)
            typer.echo(f"  readable_rows_printed={printed} bad_rowids={bad_rowids}")


def _echo_instant_mix_rows(rows: list[sqlite3.Row]) -> None:
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except json.JSONDecodeError:
            params = {"<invalid_params_json>": row["params_json"]}
        typer.echo(
            "  "
            f"rowid={row['rowid']} created_at={row['created_at']} id={row['id']} "
            f"model={row['model_name']} requested_model={params.get('requested_model')} "
            f"effective_model={params.get('effective_model')} seed_item_id={row['seed_item_id']} "
            f"seed_track_id={row['seed_track_id']} status={row['status']} results={row['result_count']} "
            f"requested_count={row['requested_count']} effective_count={row['effective_count']}"
        )


@cli.command("db-rebuild-clean")
def db_rebuild_clean(
    output: Annotated[
        Path,
        typer.Option("--output", help="Path for the rebuilt SQLite database."),
    ],
    drop_track_features: Annotated[
        bool,
        typer.Option(
            "--drop-track-features/--keep-track-features",
            help="Skip copying derived audio feature rows; they can be re-analyzed.",
        ),
    ] = True,
    drop_instant_mix_history: Annotated[
        bool,
        typer.Option(
            "--drop-instant-mix-history/--keep-instant-mix-history",
            help="Skip copying Instant Mix history.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite output if it already exists."),
    ] = False,
) -> None:
    """Build a fresh SQLite DB by copying readable rows from the current DB."""
    settings = Settings.from_env()
    source = settings.db_path
    if output.exists():
        if not force:
            typer.echo(f"Output already exists: {output}", err=True)
            raise typer.Exit(1)
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    typer.echo(f"source={source}")
    typer.echo(f"output={output}")

    destination_store = Store(output)
    destination_store.init()

    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as src, sqlite3.connect(
        output,
        timeout=30,
        isolation_level="IMMEDIATE",
    ) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only = ON")
        dst.execute("PRAGMA foreign_keys = OFF")
        dst.execute("PRAGMA synchronous = FULL")
        tables = [
            str(row["name"])
            for row in src.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        skipped_tables: list[str] = []
        copied_counts: dict[str, int] = {}
        skipped_rows: dict[str, list[int]] = {}
        for table in tables:
            if drop_track_features and table == "track_features":
                skipped_tables.append(table)
                typer.echo(f"skip table={table} reason=drop_track_features")
                continue
            if drop_instant_mix_history and table == "instant_mix_requests":
                skipped_tables.append(table)
                typer.echo(f"skip table={table} reason=drop_instant_mix_history")
                continue
            try:
                count, bad_rowids = _copy_table_readable_rows(src, dst, table)
            except sqlite3.DatabaseError as exc:
                typer.echo(f"copy_error table={table} error={exc}", err=True)
                skipped_tables.append(table)
                continue
            copied_counts[table] = count
            if bad_rowids:
                skipped_rows[table] = bad_rowids
            typer.echo(
                f"copied table={table} rows={count}"
                + (f" skipped_rowids={bad_rowids}" if bad_rowids else "")
            )
        dst.commit()
        dst.execute("PRAGMA foreign_keys = ON")
        quick_check = dst.execute("PRAGMA quick_check").fetchall()

    typer.echo(f"skipped_tables={skipped_tables}")
    typer.echo(f"skipped_rows={skipped_rows}")
    typer.echo(f"quick_check={quick_check[0][0] if quick_check else '<no rows>'}")
    if not quick_check or str(quick_check[0][0]).lower() != "ok":
        raise typer.Exit(1)
    typer.echo("db_rebuild_clean=ok")


def _copy_table_readable_rows(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
) -> tuple[int, list[int]]:
    columns = [
        str(row["name"])
        for row in src.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    ]
    if not columns:
        return 0, []
    quoted_table = _quote_identifier(table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
    select_sql = f"SELECT rowid, {quoted_columns} FROM {quoted_table} NOT INDEXED ORDER BY rowid"
    try:
        rows = src.execute(select_sql).fetchall()
        dst.executemany(insert_sql, [[row[column] for column in columns] for row in rows])
        return len(rows), []
    except sqlite3.DatabaseError:
        bounds = src.execute(f"SELECT MIN(rowid), MAX(rowid) FROM {quoted_table} NOT INDEXED").fetchone()
        if bounds is None or bounds[0] is None or bounds[1] is None:
            return 0, []
        copied = 0
        bad_rowids: list[int] = []
        for rowid in range(int(bounds[0]), int(bounds[1]) + 1):
            try:
                row = src.execute(
                    f"SELECT rowid, {quoted_columns} FROM {quoted_table} NOT INDEXED WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                bad_rowids.append(rowid)
                continue
            if row is None:
                continue
            dst.execute(insert_sql, [row[column] for column in columns])
            copied += 1
        return copied, bad_rowids


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def get_navidrome_client() -> NavidromeClient:
    settings = Settings.from_env()
    return NavidromeClient(settings.navidrome)


@cli.command()
def scan(music_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Scan a music folder and upsert track metadata."""
    store, _settings = get_store_and_settings()
    logger.info("Starting scan music_dir=%s", music_dir)
    total = 0
    changed = 0
    for scanned in scan_music_folder(music_dir):
        _track_id, did_change = store.upsert_track(scanned)
        total += 1
        changed += int(did_change)
        if total % 100 == 0:
            logger.info("Scan progress music_dir=%s scanned=%s changed=%s", music_dir, total, changed)
    logger.info(
        "Finished scan music_dir=%s scanned=%s changed=%s tracks=%s",
        music_dir,
        total,
        changed,
        store.count_tracks(),
    )
    typer.echo(f"scanned={total} changed={changed} tracks={store.count_tracks()}")


@cli.command("normalize-library")
def normalize_library() -> None:
    """Backfill normalized artist/release sidecar tables from existing tracks."""
    store, _settings = get_store_and_settings()
    status = store.backfill_library_normalization()
    typer.echo(f"tracks={status.total_tracks}")
    typer.echo(f"tracks_with_release={status.tracks_with_release}")
    typer.echo(f"tracks_with_artist={status.tracks_with_artist}")
    typer.echo(f"releases={status.releases}")
    typer.echo(f"artists={status.artists}")
    typer.echo(f"orphan_releases={status.orphan_releases}")
    typer.echo(f"orphan_artists={status.orphan_artists}")


@cli.command("inspect-folder")
def inspect_folder(
    music_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Show what the container can see in a music folder."""
    all_files = [path for path in music_dir.rglob("*") if path.is_file()]
    audio_files = list(iter_audio_files(music_dir))
    suffix_counts: dict[str, int] = {}
    for path in all_files:
        suffix = path.suffix.lower() or "<none>"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    typer.echo(f"path={music_dir.resolve()}")
    typer.echo(f"exists={music_dir.exists()} is_dir={music_dir.is_dir()}")
    typer.echo(f"all_files={len(all_files)}")
    typer.echo(f"supported_audio_files={len(audio_files)}")
    typer.echo(f"supported_extensions={', '.join(sorted(AUDIO_EXTENSIONS))}")
    typer.echo("top_extensions:")
    for suffix, count in sorted(suffix_counts.items(), key=lambda item: item[1], reverse=True)[:20]:
        typer.echo(f"  {suffix}: {count}")
    typer.echo("examples:")
    for path in all_files[:limit]:
        typer.echo(f"  {path}")


@cli.command("navidrome-ping")
def navidrome_ping() -> None:
    """Check configured Navidrome Subsonic API access."""
    client = get_navidrome_client()
    payload = client.ping()
    version = payload.get("version", "")
    server_version = payload.get("serverVersion", "")
    suffix = f" server_version={server_version}" if server_version else ""
    typer.echo(f"navidrome=ok api_version={version}{suffix}")


@cli.command("navidrome-list")
def navidrome_list(
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 10,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    query: Annotated[str, typer.Option("--query")] = "",
) -> None:
    """List songs from Navidrome through the Subsonic API."""
    client = get_navidrome_client()
    songs = client.list_songs(limit=limit, offset=offset, query=query)
    typer.echo(f"songs={len(songs)} offset={offset} query={query!r}")
    for song in songs:
        label = f"{song.artist or ''} - {song.title or ''}".strip(" -")
        album = f" album={song.album}" if song.album else ""
        duration = f" duration={song.duration}" if song.duration is not None else ""
        suffix = f" suffix={song.suffix}" if song.suffix else ""
        typer.echo(f"{song.id}  {label}{album}{duration}{suffix}")


@cli.command("navidrome-download")
def navidrome_download(
    item_id: Annotated[str, typer.Option("--item-id")],
    out_dir: Annotated[Path | None, typer.Option("--out-dir")] = None,
    suffix: Annotated[str | None, typer.Option("--suffix")] = None,
) -> None:
    """Download a Navidrome song to a local temp file for diagnostics."""
    settings = Settings.from_env()
    client = NavidromeClient(settings.navidrome)
    downloaded = client.download_track(item_id, out_dir, suffix=suffix)
    typer.echo(
        f"path={downloaded.path} bytes={downloaded.bytes_written} "
        f"content_type={downloaded.content_type!r} mode={downloaded.mode}"
    )


@cli.command("navidrome-sync")
def navidrome_sync(
    page_size: Annotated[int, typer.Option("--page-size", min=1, max=2000)] = 2000,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    mark_stale: Annotated[
        bool,
        typer.Option("--mark-stale/--keep-stale"),
    ] = True,
) -> None:
    """Import/update tracks from Navidrome and save external ID mappings."""
    store, settings = get_store_and_settings()
    client = NavidromeClient(settings.navidrome)

    def progress(count: int, song) -> None:
        if count == 1 or count % 100 == 0:
            label = f"{song.artist or ''} - {song.title or song.id}".strip(" -")
            typer.echo(f"syncing={count} item_id={song.id} {label}")

    result = sync_navidrome_catalog(
        store,
        client,
        page_size=page_size,
        limit=limit,
        mark_stale=mark_stale,
        progress=progress,
    )
    typer.echo(result.summary())
    if result.failed_count:
        raise typer.Exit(1)


@cli.command("navidrome-merge-duplicates")
def navidrome_merge_duplicates(
    apply: Annotated[
        bool,
        typer.Option("--apply/--dry-run", help="Apply the migration instead of reporting what would change."),
    ] = False,
    page_size: Annotated[int, typer.Option("--page-size", min=1, max=2000)] = 500,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=5000)] = 500,
) -> None:
    """One-time migration: bind Navidrome IDs to existing local tracks and remove empty navidrome:// duplicates."""
    store, settings = get_store_and_settings()
    client = NavidromeClient(settings.navidrome)

    def progress(done: int, total: int) -> None:
        typer.echo(f"applied={done}/{total}")

    result = migrate_navidrome_duplicate_tracks(
        store,
        songs=client.iter_songs(page_size=page_size, limit=limit),
        dry_run=not apply,
        batch_size=batch_size,
        progress=progress if apply else None,
    )
    typer.echo(result.summary())


@cli.command("navidrome-repair-releases")
def navidrome_repair_releases(
    apply: Annotated[
        bool,
        typer.Option("--apply/--dry-run", help="Apply safe one-to-one release repairs."),
    ] = False,
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=5000)] = 250,
) -> None:
    """Merge empty releases left behind after Navidrome album ID changes."""
    store, _settings = get_store_and_settings()

    def progress(done: int, total: int) -> None:
        typer.echo(f"applied={done}/{total}")

    result = repair_navidrome_empty_releases(
        store,
        dry_run=not apply,
        batch_size=batch_size,
        progress=progress if apply else None,
    )
    typer.echo(result.summary())


@cli.command()
def extract_one(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
) -> None:
    """Run the embedding spike for one audio file."""
    _store, settings = get_store_and_settings()
    embedder = create_track_embedder(settings, model)
    analysis_logger.info("Extracting one track path=%s model=%s", path, model)
    vector = embedder.extract_track_vector(path)
    typer.echo(f"pooled vector shape: {tuple(vector.shape)}")
    typer.echo(f"norm: {float((vector * vector).sum() ** 0.5):.6f}")


@cli.command()
def analyze(
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Extract embeddings for tracks missing the selected model."""
    store, settings = get_store_and_settings()
    embedder = create_track_embedder(settings, model)
    tracks = store.list_tracks_missing_embedding(model, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze model=%s limit=%s total=%s", model, limit, total)
    if total == 0:
        typer.echo(f"nothing to analyze for model={model}")
        analysis_logger.info("Finished CLI analyze model=%s total=0", model)
        return
    typer.echo(f"analyzing={total} model={model}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            with track_audio_path(store, settings, track) as audio_path:
                vector = embedder.extract_track_vector(audio_path)
            store.save_embedding(track.id, model, vector)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track analyze failed track_id=%s path=%s model=%s seconds=%.1f",
                track.id,
                track.path,
                model,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze model=%s done=%s failed=%s embeddings=%s",
        model,
        done,
        failed,
        store.count_embeddings(model),
    )
    typer.echo(f"analyzed={done} failed={failed} embeddings={store.count_embeddings(model)}")


def post_json(server: str, path: str, payload: dict[str, object], *, timeout: float = 60) -> dict[str, object]:
    url = urljoin(server.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def register_worker_with_retry(
    server: str,
    worker_id: str,
    models: list[str],
    poll_seconds: float,
    once: bool,
) -> None:
    while True:
        try:
            post_json(server, "/workers/register", {"worker_id": worker_id, "models": models})
            return
        except (URLError, TimeoutError, ConnectionError) as exc:
            typer.echo(
                f"server unavailable during register: {server} ({exc}); retrying in {poll_seconds}s",
                err=True,
            )
            if once:
                raise typer.Exit(1) from exc
            time.sleep(poll_seconds)


def download_task_audio(server: str, audio_url: str, target: Path) -> None:
    url = urljoin(server.rstrip("/") + "/", audio_url.lstrip("/"))
    with urlopen(url, timeout=300) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type", "")
    if not payload:
        raise RuntimeError(f"Downloaded empty audio payload from {url}")
    stripped = payload[:512].lstrip()
    if stripped.startswith((b"{", b"[", b"<")):
        snippet = stripped[:240].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Downloaded non-audio payload from {url} content_type={content_type!r}: {snippet}"
        )
    target.write_bytes(payload)


def post_worker_submit_json(server: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    delay = 0.5
    attempt = 0
    while True:
        attempt += 1
        try:
            return post_json(server, path, payload, timeout=300)
        except HTTPError as exc:
            if exc.code not in {429, 500, 503, 504}:
                raise
            typer.echo(
                f"submit busy: HTTP {exc.code}; attempt={attempt}; retrying in {delay:.1f}s",
                err=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
        except OSError as exc:
            typer.echo(
                f"submit failed: {exc}; attempt={attempt}; retrying in {delay:.1f}s",
                err=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


def task_is_active(server: str, worker_id: str, task_id: str) -> bool:
    return bool(worker_task_state(server, worker_id, task_id).get("active"))


def worker_task_state(server: str, worker_id: str, task_id: str) -> dict[str, object]:
    url = urljoin(
        server.rstrip("/") + "/",
        f"/workers/tasks/{task_id}/state?worker_id={worker_id}",
    )
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def audio_features_available() -> bool:
    try:
        import essentia.standard  # noqa: F401
    except ImportError:
        return False
    return True


def worker_failure_retryable(exc: Exception) -> bool:
    if isinstance(exc, KeyError):
        return False
    text = str(exc).lower()
    terminal_fragments = (
        "essentia-tensorflow is required",
        "no module named",
        "model file not found",
        "missing model",
        "unsupported model",
        "ffmpeg failed to decode",
        "decoded no audio samples",
        "invalid data found when processing input",
        "output file #0 does not contain any stream",
        "embedding vector has zero norm",
        "torch is required for muq-mulan",
        "muq is required for muq-mulan",
        "muq-mulan model could not be loaded",
        "duplicate dispatch rule",
    )
    return not any(fragment in text for fragment in terminal_fragments)


def resolve_cpu_workers(cpu_workers: int) -> tuple[int, str]:
    if cpu_workers > 0:
        return cpu_workers, "manual"
    cpu_count = os.cpu_count() or 2
    if cpu_count <= 2:
        return 1, f"auto_from={cpu_count}"
    return max(1, min(8, cpu_count - 2)), f"auto_from={cpu_count}"


def torch_cuda_memory_summary() -> str:
    torch = sys.modules.get("torch")
    if torch is None:
        return "torch_cuda=not_loaded"
    try:
        if not torch.cuda.is_available():
            return "torch_cuda=unavailable"
        return (
            f"torch_cuda_allocated_mb={torch.cuda.memory_allocated() / 1024 / 1024:.1f} "
            f"torch_cuda_reserved_mb={torch.cuda.memory_reserved() / 1024 / 1024:.1f} "
            f"torch_cuda_max_allocated_mb={torch.cuda.max_memory_allocated() / 1024 / 1024:.1f} "
            f"torch_cuda_max_reserved_mb={torch.cuda.max_memory_reserved() / 1024 / 1024:.1f}"
        )
    except Exception as exc:
        return f"torch_cuda_probe_failed={type(exc).__name__}:{exc}"


@cli.command("gpu-info")
def gpu_info() -> None:
    """Print container GPU visibility diagnostics."""
    typer.echo(f"CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '<unset>')}")
    typer.echo(f"NVIDIA_VISIBLE_DEVICES={os.getenv('NVIDIA_VISIBLE_DEVICES', '<unset>')}")
    try:
        completed = subprocess.run(
            ["nvidia-smi"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        typer.echo(f"nvidia-smi exit={completed.returncode}")
        if completed.stdout.strip():
            typer.echo(completed.stdout.strip())
        if completed.stderr.strip():
            typer.echo(completed.stderr.strip(), err=True)
    except FileNotFoundError:
        typer.echo("nvidia-smi not found in container", err=True)
    except Exception as exc:
        typer.echo(f"nvidia-smi failed: {exc}", err=True)
    try:
        import tensorflow as tf

        typer.echo(f"tensorflow={tf.__version__}")
        typer.echo(f"tf built_with_cuda={tf.test.is_built_with_cuda()}")
        typer.echo(f"tf physical GPUs={tf.config.list_physical_devices('GPU')}")
    except Exception as exc:
        typer.echo(f"tensorflow probe failed: {type(exc).__name__}: {exc}", err=True)
    try:
        import torch

        typer.echo(f"torch={torch.__version__}")
        typer.echo(f"torch cuda_available={torch.cuda.is_available()}")
        typer.echo(f"torch cuda_version={torch.version.cuda}")
        if torch.cuda.is_available():
            typer.echo(f"torch cuda_device={torch.cuda.get_device_name(0)}")
    except Exception as exc:
        typer.echo(f"torch probe failed: {type(exc).__name__}: {exc}", err=True)
    try:
        import essentia  # noqa: F401
        import essentia.standard  # noqa: F401

        typer.echo("essentia=available")
    except Exception as exc:
        typer.echo(f"essentia probe failed: {type(exc).__name__}: {exc}", err=True)


@cli.command("gpu-smoke")
def gpu_smoke(
    size: Annotated[int, typer.Option("--size")] = 4096,
) -> None:
    """Run a small TensorFlow matmul to confirm real GPU execution."""
    try:
        import tensorflow as tf
    except Exception as exc:
        typer.echo(f"tensorflow import failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    gpus = tf.config.list_physical_devices("GPU")
    typer.echo(f"tf physical GPUs={gpus}")
    if not gpus:
        raise typer.Exit(1)
    with tf.device("/GPU:0"):
        a = tf.random.uniform((size, size), dtype=tf.float32)
        b = tf.random.uniform((size, size), dtype=tf.float32)
        started = perf_counter()
        c = tf.matmul(a, b)
        value = float(tf.reduce_sum(c).numpy())
        elapsed = perf_counter() - started
    typer.echo(f"gpu matmul ok size={size} seconds={elapsed:.3f} checksum={value:.3f}")


@cli.command("embedding-smoke")
def embedding_smoke(
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    path: Annotated[Path | None, typer.Option("--path", exists=True, dir_okay=False)] = None,
    seconds: Annotated[int, typer.Option("--seconds")] = 180,
    repeat: Annotated[int, typer.Option("--repeat")] = 10,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 64,
    backend: Annotated[str, typer.Option("--backend")] = "auto",
) -> None:
    """Run a real embedding path and sample nvidia-smi while it runs."""
    _store, settings = get_store_and_settings()
    if model == MUQ_MULAN_MODEL:
        embedder = MuqMulanEmbedder(settings)
        started = perf_counter()
        if path is not None:
            vector = embedder.extract_track_vector(path)
        else:
            audio = (np.random.random(max(seconds, 1) * embedder.sample_rate).astype(np.float32) * 0.01) - 0.005
            vector = pool_and_normalize(embedder._predict(audio))
        elapsed = perf_counter() - started
        typer.echo(
            f"embedding smoke ok model={model} seconds={seconds} elapsed={elapsed:.3f} "
            f"shape={tuple(vector.shape)} norm={float(np.linalg.norm(vector)):.6f}"
        )
        return
    typer.echo(f"CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '<unset>')}")
    try:
        import tensorflow as tf

        typer.echo(f"tensorflow={tf.__version__}")
        typer.echo(f"tf physical GPUs={tf.config.list_physical_devices('GPU')}")
    except Exception as exc:
        typer.echo(f"tensorflow probe failed: {type(exc).__name__}: {exc}", err=True)
    embedder = DiscogsEffnetEmbedder(settings, model, batch_size=batch_size, backend=backend)
    audio = (np.random.random(max(seconds, 1) * 16000).astype(np.float32) * 0.01) - 0.005
    stop = False
    gpu_samples: list[str] = []

    def monitor_gpu() -> None:
        while not stop:
            try:
                completed = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=pid,process_name,used_memory",
                        "--format=csv,noheader",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                sample = completed.stdout.strip()
                if sample:
                    gpu_samples.append(sample)
            except Exception:
                pass
            time.sleep(0.1)

    monitor = threading.Thread(target=monitor_gpu, daemon=True)
    monitor.start()
    started = perf_counter()
    try:
        vector = None
        for _index in range(max(repeat, 1)):
            embeddings = embedder._predict(audio)
            vector = np.asarray(embeddings, dtype=np.float32)
    finally:
        stop = True
        monitor.join(timeout=1)
    elapsed = perf_counter() - started
    typer.echo(
        f"embedding smoke ok model={model} backend={backend} batch_size={batch_size} "
        f"seconds={seconds} repeat={repeat} elapsed={elapsed:.3f} shape={vector.shape if vector is not None else None}"
    )
    typer.echo(f"nvidia-smi compute samples={len(gpu_samples)}")
    if gpu_samples:
        typer.echo(gpu_samples[-1])


@cli.command("embedding-compare")
def embedding_compare(
    path: Annotated[Path | None, typer.Option("--path")] = None,
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    batch_size: Annotated[int, typer.Option("--batch-size")] = 64,
    seconds: Annotated[int, typer.Option("--seconds")] = 60,
    min_cosine: Annotated[float, typer.Option("--min-cosine")] = 0.999,
) -> None:
    """Compare Essentia and direct TensorFlow embedding backends."""
    _store, settings = get_store_and_settings()
    reference = DiscogsEffnetEmbedder(settings, model, batch_size=batch_size, backend="essentia")
    direct = DiscogsEffnetEmbedder(settings, model, batch_size=batch_size, backend="tensorflow")
    if path is None:
        rng = np.random.default_rng(123)
        audio = (rng.random(max(seconds, 1) * 16000, dtype=np.float32) * 2.0 - 1.0) * 0.05
        ref_embeddings = reference._predict(audio)
        direct_embeddings = direct._predict(audio)
    else:
        audio = reference._load_audio(path)
        ref_embeddings = reference._predict(audio)
        direct_embeddings = direct._predict(audio)
    ref_vector = np.asarray(ref_embeddings, dtype=np.float32).mean(axis=0)
    direct_vector = np.asarray(direct_embeddings, dtype=np.float32).mean(axis=0)
    ref_vector /= np.linalg.norm(ref_vector) + 1e-12
    direct_vector /= np.linalg.norm(direct_vector) + 1e-12
    pooled_cosine = float(np.dot(ref_vector, direct_vector))
    patch_count = min(len(ref_embeddings), len(direct_embeddings))
    typer.echo(f"reference shape={tuple(ref_embeddings.shape)} direct shape={tuple(direct_embeddings.shape)}")
    typer.echo(f"pooled_cosine={pooled_cosine:.8f} patch_count={patch_count}")
    if pooled_cosine < min_cosine:
        raise typer.Exit(1)


def submit_worker_buffers(
    server: str,
    worker_id: str,
    results: list[dict[str, object]],
    feature_results: list[dict[str, object]],
    head_results: list[dict[str, object]],
    failures: list[dict[str, object]],
    acknowledged_task_ids: set[str] | None = None,
    audio_paths: dict[str, Path] | None = None,
) -> None:
    acknowledged: set[str] = set()
    if results or feature_results or head_results:
        result_count = len(results) + len(feature_results) + len(head_results)
        response = post_worker_submit_json(
            server,
            "/workers/results",
            {
                "worker_id": worker_id,
                "results": results,
                "feature_results": feature_results,
                "head_results": head_results,
            },
        )
        accepted = [str(task_id) for task_id in response.get("accepted", []) if task_id]
        rejected = [
            str(item.get("task_id"))
            for item in response.get("rejected", [])
            if isinstance(item, dict) and item.get("task_id")
        ]
        acknowledged.update(accepted)
        acknowledged.update(rejected)
        typer.echo(
            f"submitted results={result_count} accepted={len(accepted)} rejected={len(rejected)}"
        )
        results.clear()
        feature_results.clear()
        head_results.clear()
    if failures:
        failure_count = len(failures)
        response = post_worker_submit_json(server, "/workers/failures", {"worker_id": worker_id, "failures": failures})
        failed = [str(task_id) for task_id in response.get("failed", []) if task_id]
        acknowledged.update(failed)
        typer.echo(f"submitted failures={failure_count} accepted={len(failed)}")
        failures.clear()
    if acknowledged_task_ids is not None:
        acknowledged_task_ids.update(acknowledged)
    if audio_paths is not None:
        for task_id in acknowledged:
            path = audio_paths.pop(task_id, None)
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    typer.echo(f"temp cleanup failed task_id={task_id}: {exc}", err=True)


def process_rss_mb() -> float | None:
    status_path = Path("/proc/self/status")
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1]) / 1024
    except OSError:
        return None
    return None


def append_embedding_result(
    results: list[dict[str, object]],
    task: dict[str, object],
    model_name: str,
    vector: np.ndarray,
) -> None:
    results.append(
        {
            "task_id": str(task["task_id"]),
            "track_id": int(task["track_id"]),
            "model_name": model_name,
            "dim": int(vector.shape[0]),
            "dtype": "float32",
            "vector_b64": base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode("ascii"),
            "file_size": int(task["file_size"]),
            "mtime": int(task["mtime"]),
        }
    )


def serialized_head_outputs(outputs: list[HeadOutput]) -> list[dict[str, object]]:
    return [
        {
            "model_name": output.model_name,
            "dim": int(output.scores.shape[0]),
            "dtype": "float32",
            "aggregation": output.aggregation,
            "scores_b64": base64.b64encode(np.asarray(output.scores, dtype=np.float32).tobytes()).decode("ascii"),
            "predictions": [
                {
                    "label": prediction.label,
                    "score": prediction.score,
                    "rank": prediction.rank,
                }
                for prediction in output.predictions
            ],
        }
        for output in outputs
    ]


def append_worker_failure(
    failures: list[dict[str, object]],
    task_id: str,
    exc: Exception,
) -> None:
    failures.append(
        {
            "task_id": task_id,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "stage": "worker",
            "retryable": worker_failure_retryable(exc),
        }
    )


def normalize_worker_models(models: list[str]) -> list[str]:
    normalized: list[str] = []
    for model in models:
        normalized.extend(part.strip() for part in model.replace(",", " ").split())
    return list(dict.fromkeys(model for model in normalized if model))


def close_task_on_conflict(
    exc: Exception,
    task_id: str,
    model_name: str,
    audio_path: Path | None,
    close_inactive_task: Callable[[str, str, Path | None], bool],
) -> bool:
    """True if `exc` is an HTTP 409 (task no longer active) and it was closed."""
    if isinstance(exc, HTTPError) and exc.code == 409:
        return close_inactive_task(task_id, model_name, audio_path)
    return False


def fallback_to_essentia_embedding(
    settings: Settings,
    model_name: str,
    gpu_batch_size: int,
    audio_path: Path,
    task: dict[str, object],
    results: list[dict[str, object]],
    failures: list[dict[str, object]],
    completed_task_ids: set[str],
) -> None:
    task_id = str(task["task_id"])
    try:
        vector = np.asarray(
            DiscogsEffnetEmbedder(
                settings,
                model_name,
                batch_size=gpu_batch_size,
                backend="essentia",
            ).extract_track_vector(audio_path),
            dtype=np.float32,
        )
        append_embedding_result(results, task, model_name, vector)
        completed_task_ids.add(task_id)
        typer.echo(
            f"ok task_id={task_id} track_id={task['track_id']} "
            f"model={model_name} backend=essentia-fallback"
        )
    except Exception as fallback_exc:
        append_worker_failure(failures, task_id, fallback_exc)
        completed_task_ids.add(task_id)
        typer.echo(f"failed task_id={task_id}: {fallback_exc}", err=True)


@cli.command("worker")
def worker(
    server: Annotated[str, typer.Option("--server")] = "http://127.0.0.1:8711",
    worker_id: Annotated[str, typer.Option("--worker-id")] = "discocs-worker",
    models: Annotated[list[str], typer.Option("--models")] = [
        *EMBEDDING_MODELS,
        AUDIO_FEATURE_EXTRACTOR,
        "discogs-effnet-heads",
    ],
    claim_batch_size: Annotated[int, typer.Option("--claim-batch-size")] = 16,
    lease_seconds: Annotated[int, typer.Option("--lease-seconds")] = 900,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds")] = 5.0,
    once: Annotated[bool, typer.Option("--once")] = False,
    max_inflight_tasks: Annotated[int, typer.Option("--max-inflight-tasks")] = 64,
    submit_batch_size: Annotated[int, typer.Option("--submit-batch-size")] = 1,
    download_concurrency: Annotated[int, typer.Option("--download-concurrency")] = 1,
    decode_workers: Annotated[int, typer.Option("--decode-workers")] = 1,
    gpu_batch_size: Annotated[int, typer.Option("--gpu-batch-size")] = 64,
    embedding_backend: Annotated[str, typer.Option("--embedding-backend")] = "auto",
    ready_batches: Annotated[int, typer.Option("--ready-batches")] = 1,
    cpu_workers: Annotated[int, typer.Option("--cpu-workers")] = 0,
    max_tasks_before_exit: Annotated[int, typer.Option("--max-tasks-before-exit")] = 0,
    startup_jitter_seconds: Annotated[float, typer.Option("--startup-jitter-seconds")] = 0.0,
) -> None:
    """Run a trusted HTTP pull worker for analysis tasks."""
    _store, settings = get_store_and_settings()
    if worker_id == "auto":
        worker_id = socket.gethostname()
    models = normalize_worker_models(models)
    if AUDIO_FEATURE_EXTRACTOR in models:
        os.environ.setdefault("DISCOCS_FFMPEG_THREADS", "1")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
    if AUDIO_FEATURE_EXTRACTOR in models and not audio_features_available():
        models = [model for model in models if model != AUDIO_FEATURE_EXTRACTOR]
        typer.echo(
            f"disabled model={AUDIO_FEATURE_EXTRACTOR}: essentia-tensorflow is not installed",
            err=True,
        )
    if not models:
        raise typer.BadParameter("No usable models/capabilities remain after dependency checks")
    if startup_jitter_seconds > 0:
        delay = random.uniform(0.0, startup_jitter_seconds)
        typer.echo(f"startup jitter: waiting {delay:.1f}s before worker registration")
        time.sleep(delay)
    embedders = {
        model: create_track_embedder(
            settings,
            model,
            batch_size=gpu_batch_size,
            backend=embedding_backend,
        )
        for model in models
        if model not in {AUDIO_FEATURE_EXTRACTOR, "discogs-effnet-heads"}
    }
    missing_model_paths = [
        embedder.model_path
        for embedder in embedders.values()
        if isinstance(embedder, DiscogsEffnetEmbedder) and not embedder.model_path.exists()
    ]
    if missing_model_paths:
        typer.echo("worker model preflight failed: missing embedding model file(s)", err=True)
        for model_path in missing_model_paths:
            typer.echo(f"  {model_path}", err=True)
        typer.echo("Mount or copy models into DISCOCS_MODEL_DIR before starting the worker.", err=True)
        raise typer.Exit(1)
    audio_feature_analyzer = AudioFeatureAnalyzer() if AUDIO_FEATURE_EXTRACTOR in models else None
    head_pack_analyzer = DiscogsEffnetHeadPackAnalyzer(settings) if "discogs-effnet-heads" in models else None
    resolved_cpu_workers, cpu_workers_source = resolve_cpu_workers(cpu_workers)
    typer.echo(
        "worker starting "
        f"server={server} worker_id={worker_id} models={','.join(models)} "
        f"claim_batch_size={claim_batch_size} max_inflight_tasks={max_inflight_tasks} "
        f"submit_batch_size={submit_batch_size} download_concurrency={download_concurrency} "
        f"decode_workers={decode_workers} gpu_batch_size={gpu_batch_size} ready_batches={ready_batches} "
        f"embedding_backend={embedding_backend} cpu_workers={resolved_cpu_workers} {cpu_workers_source} "
        f"startup_jitter_seconds={startup_jitter_seconds}"
    )
    register_worker_with_retry(server, worker_id, models, poll_seconds, once)
    draining = False

    def request_drain(_signum, _frame) -> None:
        nonlocal draining
        if draining:
            raise KeyboardInterrupt
        draining = True
        typer.echo("stop requested; finishing current task and releasing the rest", err=True)

    previous_sigint = signal.signal(signal.SIGINT, request_drain)
    leased_task_ids: set[str] = set()
    completed_task_ids: set[str] = set()
    acknowledged_task_ids: set[str] = set()
    audio_paths: dict[str, Path] = {}
    results: list[dict[str, object]] = []
    feature_results: list[dict[str, object]] = []
    head_results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    processed_any = False
    try:
        with tempfile.TemporaryDirectory(prefix="discocs-worker-") as temp_dir:
            temp_path = Path(temp_dir)
            with ThreadPoolExecutor(max_workers=max(download_concurrency, 1)) as downloader, ThreadPoolExecutor(
                max_workers=max(resolved_cpu_workers, 1)
            ) as cpu_pool:
                future_to_task: dict[object, tuple[dict[str, object], Path]] = {}
                cpu_future_to_task: dict[object, tuple[dict[str, object], Path]] = {}
                embedding_future_to_task: dict[object, tuple[dict[str, object], Path]] = {}
                embedding_future_started_at: dict[object, float] = {}
                ready_embedding_tasks: list[dict[str, object]] = []
                ready_head_tasks: list[dict[str, object]] = []
                last_metrics_at = perf_counter()
                direct_embedding_pipeline = (
                    embedding_backend in {"auto", "tensorflow"}
                    and any(isinstance(embedder, DiscogsEffnetEmbedder) for embedder in embedders.values())
                )

                def flush_worker_buffers() -> None:
                    nonlocal draining
                    before = len(acknowledged_task_ids)
                    submit_worker_buffers(
                        server,
                        worker_id,
                        results,
                        feature_results,
                        head_results,
                        failures,
                        acknowledged_task_ids,
                        audio_paths,
                    )
                    if max_tasks_before_exit > 0 and len(acknowledged_task_ids) >= max_tasks_before_exit:
                        if len(acknowledged_task_ids) != before:
                            typer.echo(
                                f"max tasks reached: {len(acknowledged_task_ids)}/{max_tasks_before_exit}; draining"
                            )
                        draining = True

                def close_inactive_task(task_id: str, model_name: str, audio_path: Path | None = None) -> bool:
                    try:
                        state = worker_task_state(server, worker_id, task_id)
                    except HTTPError as state_exc:
                        if state_exc.code == 404:
                            acknowledged_task_ids.add(task_id)
                            path = audio_path or audio_paths.pop(task_id, None)
                            if path is not None:
                                audio_paths.pop(task_id, None)
                                try:
                                    path.unlink(missing_ok=True)
                                except OSError as exc:
                                    typer.echo(f"temp cleanup failed task_id={task_id}: {exc}", err=True)
                            typer.echo(
                                f"closed missing task_id={task_id} model={model_name}",
                                err=True,
                            )
                            return True
                        typer.echo(
                            f"state check failed task_id={task_id} model={model_name}: {state_exc}",
                            err=True,
                        )
                        return False
                    except Exception as state_exc:
                        typer.echo(
                            f"state check failed task_id={task_id} model={model_name}: {state_exc}",
                            err=True,
                        )
                        return False
                    if bool(state.get("active")):
                        return False
                    acknowledged_task_ids.add(task_id)
                    path = audio_path or audio_paths.pop(task_id, None)
                    if path is not None:
                        audio_paths.pop(task_id, None)
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as exc:
                            typer.echo(f"temp cleanup failed task_id={task_id}: {exc}", err=True)
                    typer.echo(
                        f"closed inactive task_id={task_id} model={model_name} "
                        f"task_status={state.get('status')} job_status={state.get('job_status')}",
                        err=True,
                    )
                    return True

                def claim_more() -> None:
                    nonlocal processed_any
                    if draining:
                        return
                    capacity = (
                        max(max_inflight_tasks, 1)
                        - len(future_to_task)
                        - len(cpu_future_to_task)
                        - len(embedding_future_to_task)
                        - len(ready_embedding_tasks)
                        - len(ready_head_tasks)
                    )
                    if capacity <= 0:
                        return
                    limit = min(max(claim_batch_size, 1), capacity)
                    claimed = post_json(
                        server,
                        "/workers/claim",
                        {
                            "worker_id": worker_id,
                            "models": models,
                            "limit": limit,
                            "lease_seconds": lease_seconds,
                        },
                    )
                    tasks = claimed.get("tasks", [])
                    if not isinstance(tasks, list) or not tasks:
                        return
                    for task in tasks:
                        task_id = str(task["task_id"])
                        leased_task_ids.add(task_id)
                        suffix = Path(str(task.get("path") or "")).suffix or ".audio"
                        audio_path = temp_path / f"{task_id}{suffix}"
                        audio_paths[task_id] = audio_path
                        future = downloader.submit(
                            download_task_audio,
                            server,
                            str(task["audio_url"]),
                            audio_path,
                        )
                        future_to_task[future] = (task, audio_path)
                    processed_any = True

                def maybe_log_metrics() -> None:
                    nonlocal last_metrics_at
                    now = perf_counter()
                    if now - last_metrics_at < 10:
                        return
                    last_metrics_at = now
                    typer.echo(
                        "worker metrics "
                        f"claimed={len(leased_task_ids)} "
                        f"submitted={len(acknowledged_task_ids)} "
                        f"failed_buffer={len(failures)} "
                        f"download={len(future_to_task)} "
                        f"cpu_features={len(cpu_future_to_task)} "
                        f"embedding_preprocess={len(embedding_future_to_task)} "
                        f"ready_embeddings={len(ready_embedding_tasks)} "
                        f"ready_heads={len(ready_head_tasks)} "
                        f"leased={len(leased_task_ids - acknowledged_task_ids)} "
                        f"rss_mb={process_rss_mb()} "
                        f"{torch_cuda_memory_summary()}"
                    )

                def process_ready_embedding_batches(*, flush: bool = False) -> None:
                    if not ready_embedding_tasks:
                        return
                    grouped_models = list(dict.fromkeys(str(item["task"]["model_name"]) for item in ready_embedding_tasks))
                    for model_name in grouped_models:
                        model_items = [item for item in ready_embedding_tasks if str(item["task"]["model_name"]) == model_name]
                        total_patches = sum(len(item["patches"]) for item in model_items)
                        target_patches = max(gpu_batch_size, 1) * max(ready_batches, 1)
                        if not flush and total_patches < target_patches:
                            continue
                        ready_embedding_tasks[:] = [
                            item
                            for item in ready_embedding_tasks
                            if str(item["task"]["model_name"]) != model_name
                        ]
                        try:
                            batch_started = perf_counter()
                            active_items = []
                            for item in model_items:
                                task_id = str(item["task"]["task_id"])
                                if draining or not task_is_active(server, worker_id, task_id):
                                    close_inactive_task(task_id, model_name, item.get("audio_path"))
                                    continue
                                active_items.append(item)
                            if not active_items:
                                continue
                            all_patches = np.concatenate([item["patches"] for item in active_items], axis=0)
                            patch_counts = [len(item["patches"]) for item in active_items]
                            actual_patches = len(all_patches)
                            padded = 0
                            remainder = actual_patches % gpu_batch_size
                            if remainder:
                                padded = gpu_batch_size - remainder
                                all_patches = np.pad(all_patches, ((0, padded), (0, 0), (0, 0)), mode="constant")
                            direct_model = embedders[model_name].direct_model()
                            predict_started = perf_counter()
                            outputs = []
                            for start in range(0, len(all_patches), gpu_batch_size):
                                outputs.append(direct_model.predict_patches_unpadded(all_patches[start : start + gpu_batch_size]))
                            embeddings = np.concatenate(outputs, axis=0)[:actual_patches]
                            predict_seconds = perf_counter() - predict_started
                            offset = 0
                            for item, patch_count in zip(active_items, patch_counts):
                                task = item["task"]
                                task_id = str(task["task_id"])
                                task_embeddings = embeddings[offset : offset + patch_count]
                                offset += patch_count
                                vector = pool_and_normalize(task_embeddings)
                                append_embedding_result(results, task, model_name, vector)
                                completed_task_ids.add(task_id)
                                typer.echo(
                                    f"ok task_id={task_id} track_id={task['track_id']} "
                                    f"model={model_name} patches={patch_count}"
                                )
                            typer.echo(
                                f"gpu batch model={model_name} tasks={len(active_items)} "
                                f"patches={actual_patches} padded={padded} "
                                f"predict_seconds={predict_seconds:.3f} total_seconds={perf_counter() - batch_started:.3f}"
                            )
                            del all_patches, embeddings, outputs
                        except Exception as exc:
                            for item in model_items:
                                task = item["task"]
                                task_id = str(task["task_id"])
                                audio_path = item["audio_path"]
                                if embedding_backend == "auto":
                                    fallback_to_essentia_embedding(
                                        settings,
                                        model_name,
                                        gpu_batch_size,
                                        audio_path,
                                        task,
                                        results,
                                        failures,
                                        completed_task_ids,
                                    )
                                else:
                                    append_worker_failure(failures, task_id, exc)
                                    completed_task_ids.add(task_id)
                                    typer.echo(f"failed task_id={task_id}: {exc}", err=True)
                        if (
                            len(results) + len(feature_results) + len(head_results) + len(failures)
                            >= max(submit_batch_size, 1)
                        ):
                            flush_worker_buffers()

                def process_ready_head_batches(*, flush: bool = False) -> None:
                    if not ready_head_tasks:
                        return
                    if head_pack_analyzer is None:
                        raise KeyError("discogs-effnet-heads")
                    target_tasks = max(4, ready_batches, 1)
                    if not flush and len(ready_head_tasks) < target_tasks:
                        return
                    batch_items = ready_head_tasks[:target_tasks]
                    del ready_head_tasks[:target_tasks]
                    batch_started = perf_counter()
                    active_items: list[dict[str, object]] = []
                    patch_counts: list[int] = []
                    for item in batch_items:
                        task = item["task"]
                        audio_path = item["audio_path"]
                        task_id = str(task["task_id"])
                        model_name = str(task["model_name"])
                        try:
                            if draining or not task_is_active(server, worker_id, task_id):
                                close_inactive_task(task_id, model_name, audio_path)
                                continue
                            patches = head_pack_analyzer.extract_patch_embeddings(audio_path)
                            if len(patches) == 0:
                                raise ValueError("No EffNet patches extracted")
                            active_items.append({"task": task, "audio_path": audio_path, "patches": patches})
                            patch_counts.append(len(patches))
                        except Exception as exc:
                            if close_task_on_conflict(exc, task_id, model_name, audio_path, close_inactive_task):
                                continue
                            append_worker_failure(failures, task_id, exc)
                            completed_task_ids.add(task_id)
                            typer.echo(f"failed task_id={task_id}: {exc}", err=True)
                    if not active_items:
                        return
                    try:
                        predict_started = perf_counter()
                        outputs_by_task = head_pack_analyzer.analyze_patch_embedding_batch(
                            [np.asarray(item["patches"], dtype=np.float32) for item in active_items]
                        )
                        predict_seconds = perf_counter() - predict_started
                        for item, outputs, patch_count in zip(active_items, outputs_by_task, patch_counts):
                            task = item["task"]
                            task_id = str(task["task_id"])
                            head_results.append(
                                {
                                    "task_id": task_id,
                                    "track_id": int(task["track_id"]),
                                    "model_name": "discogs-effnet-heads",
                                    "file_size": int(task["file_size"]),
                                    "mtime": int(task["mtime"]),
                                    "outputs": serialized_head_outputs(outputs),
                                }
                            )
                            completed_task_ids.add(task_id)
                            typer.echo(
                                f"ok task_id={task_id} track_id={task['track_id']} "
                                f"model=discogs-effnet-heads patches={patch_count}"
                            )
                        typer.echo(
                            f"head batch tasks={len(active_items)} patches={sum(patch_counts)} "
                            f"predict_seconds={predict_seconds:.3f} total_seconds={perf_counter() - batch_started:.3f}"
                        )
                        del outputs_by_task
                    except Exception as exc:
                        for item in active_items:
                            task = item["task"]
                            task_id = str(task["task_id"])
                            append_worker_failure(failures, task_id, exc)
                            completed_task_ids.add(task_id)
                            typer.echo(f"failed task_id={task_id}: {exc}", err=True)
                    finally:
                        for item in active_items:
                            item.pop("patches", None)
                    if (
                        len(results) + len(feature_results) + len(head_results) + len(failures)
                        >= max(submit_batch_size, 1)
                    ):
                        flush_worker_buffers()

                while not draining:
                    maybe_log_metrics()
                    process_ready_embedding_batches()
                    process_ready_head_batches()
                    try:
                        claim_more()
                    except (URLError, TimeoutError) as exc:
                        typer.echo(f"claim failed: {exc}", err=True)
                        if once:
                            raise typer.Exit(1) from exc

                    if (
                        not future_to_task
                        and not cpu_future_to_task
                        and not embedding_future_to_task
                        and not ready_embedding_tasks
                        and not ready_head_tasks
                    ):
                        if once:
                            typer.echo("no tasks" if not processed_any else "done")
                            return
                        post_json(server, "/workers/heartbeat", {"worker_id": worker_id, "models": models})
                        time.sleep(poll_seconds)
                        continue

                    pending = set(future_to_task) | set(cpu_future_to_task) | set(embedding_future_to_task)
                    if not pending:
                        process_ready_embedding_batches(flush=True)
                        process_ready_head_batches(flush=True)
                        continue
                    done_futures, _pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done_futures:
                        if future in embedding_future_to_task:
                            task, audio_path = embedding_future_to_task.pop(future)
                            preprocess_started = embedding_future_started_at.pop(future, perf_counter())
                            task_id = str(task["task_id"])
                            model_name = str(task["model_name"])
                            try:
                                patches = future.result()
                                if len(patches) == 0:
                                    raise ValueError("No EffNet patches extracted")
                                if draining or not task_is_active(server, worker_id, task_id):
                                    close_inactive_task(task_id, model_name, audio_path)
                                    continue
                                ready_embedding_tasks.append(
                                    {"task": task, "audio_path": audio_path, "patches": patches}
                                )
                                typer.echo(
                                    f"ready task_id={task_id} track_id={task['track_id']} "
                                    f"model={model_name} patches={len(patches)} "
                                    f"preprocess_seconds={perf_counter() - preprocess_started:.3f}"
                                )
                                process_ready_embedding_batches()
                            except Exception as exc:
                                if close_task_on_conflict(exc, task_id, model_name, audio_path, close_inactive_task):
                                    continue
                                if embedding_backend == "auto":
                                    if close_inactive_task(task_id, model_name, audio_path):
                                        continue
                                    fallback_to_essentia_embedding(
                                        settings,
                                        model_name,
                                        gpu_batch_size,
                                        audio_path,
                                        task,
                                        results,
                                        failures,
                                        completed_task_ids,
                                    )
                                else:
                                    append_worker_failure(failures, task_id, exc)
                                    completed_task_ids.add(task_id)
                                    typer.echo(f"failed task_id={task_id}: {exc}", err=True)
                            if (
                                len(results) + len(feature_results) + len(head_results) + len(failures)
                                >= max(submit_batch_size, 1)
                            ):
                                flush_worker_buffers()
                            continue

                        if future in cpu_future_to_task:
                            task, audio_path = cpu_future_to_task.pop(future)
                            task_id = str(task["task_id"])
                            model_name = str(task["model_name"])
                            try:
                                features = future.result()
                                if draining:
                                    continue
                                feature_results.append(
                                    {
                                        "task_id": task_id,
                                        "track_id": int(task["track_id"]),
                                        "model_name": model_name,
                                        "file_size": int(task["file_size"]),
                                        "mtime": int(task["mtime"]),
                                        "features": [
                                            {
                                                "name": feature.name,
                                                "value": feature.value,
                                                "text_value": feature.text_value,
                                                "unit": feature.unit,
                                                "confidence": feature.confidence,
                                                "extractor": feature.extractor,
                                            }
                                            for feature in features
                                        ],
                                    }
                                )
                                completed_task_ids.add(task_id)
                                typer.echo(f"ok task_id={task_id} track_id={task['track_id']} model={model_name}")
                            except Exception as exc:
                                if close_task_on_conflict(exc, task_id, model_name, audio_path, close_inactive_task):
                                    continue
                                append_worker_failure(failures, task_id, exc)
                                completed_task_ids.add(task_id)
                                typer.echo(f"failed task_id={task_id}: {exc}", err=True)
                            if (
                                len(results) + len(feature_results) + len(head_results) + len(failures)
                                >= max(submit_batch_size, 1)
                            ):
                                flush_worker_buffers()
                            continue

                        task, audio_path = future_to_task.pop(future)
                        task_id = str(task["task_id"])
                        model_name = str(task["model_name"])
                        try:
                            future.result()
                            if draining:
                                continue
                            if model_name == AUDIO_FEATURE_EXTRACTOR:
                                if audio_feature_analyzer is None:
                                    raise KeyError(model_name)
                                cpu_future = cpu_pool.submit(
                                    audio_feature_analyzer.analyze_track,
                                    audio_path,
                                )
                                cpu_future_to_task[cpu_future] = (task, audio_path)
                                continue
                            elif model_name == "discogs-effnet-heads":
                                if head_pack_analyzer is None:
                                    raise KeyError(model_name)
                                ready_head_tasks.append({"task": task, "audio_path": audio_path})
                                typer.echo(
                                    f"ready task_id={task_id} track_id={task['track_id']} "
                                    f"model={model_name} stage=head-buffer"
                                )
                                process_ready_head_batches()
                                continue
                            else:
                                if (
                                    direct_embedding_pipeline
                                    and model_name in embedders
                                    and isinstance(embedders[model_name], DiscogsEffnetEmbedder)
                                ):
                                    embedding_future = cpu_pool.submit(
                                        embedders[model_name].extract_direct_patches,
                                        audio_path,
                                    )
                                    embedding_future_to_task[embedding_future] = (task, audio_path)
                                    embedding_future_started_at[embedding_future] = perf_counter()
                                    continue
                                vector = np.asarray(
                                    embedders[model_name].extract_track_vector(audio_path),
                                    dtype=np.float32,
                                )
                                results.append(
                                    {
                                        "task_id": task_id,
                                        "track_id": int(task["track_id"]),
                                        "model_name": model_name,
                                        "dim": int(vector.shape[0]),
                                        "dtype": "float32",
                                        "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                                        "file_size": int(task["file_size"]),
                                        "mtime": int(task["mtime"]),
                                    }
                                )
                            completed_task_ids.add(task_id)
                            typer.echo(f"ok task_id={task_id} track_id={task['track_id']} model={model_name}")
                        except Exception as exc:
                            if close_task_on_conflict(exc, task_id, model_name, audio_path, close_inactive_task):
                                continue
                            append_worker_failure(failures, task_id, exc)
                            completed_task_ids.add(task_id)
                            typer.echo(f"failed task_id={task_id}: {exc}", err=True)

                        if (
                            len(results) + len(feature_results) + len(head_results) + len(failures)
                            >= max(submit_batch_size, 1)
                        ):
                            flush_worker_buffers()
                        if not draining:
                            try:
                                claim_more()
                            except (URLError, TimeoutError) as exc:
                                typer.echo(f"claim failed: {exc}", err=True)

                if draining:
                    typer.echo(
                        "releasing "
                        f"{len(future_to_task) + len(cpu_future_to_task) + len(embedding_future_to_task) + len(ready_embedding_tasks) + len(ready_head_tasks)} "
                        "queued in-flight task(s)",
                        err=True,
                    )
        submit_worker_buffers(
            server,
            worker_id,
            results,
            feature_results,
            head_results,
            failures,
            acknowledged_task_ids,
            audio_paths,
        )
    except KeyboardInterrupt:
        typer.echo("forced stop requested; releasing leases", err=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        unreleased = sorted(leased_task_ids - acknowledged_task_ids)
        if unreleased:
            try:
                post_json(
                    server,
                    "/workers/release",
                    {"worker_id": worker_id, "task_ids": unreleased},
                )
            except Exception as exc:
                typer.echo(f"release failed for {len(unreleased)} task(s): {exc}", err=True)


@cli.command("download-models")
def download_models(
    pack: Annotated[str, typer.Option("--pack")] = "discogs-effnet-heads",
) -> None:
    """Download model files required for a model pack."""
    _store, settings = get_store_and_settings()
    logger.info("Downloading model pack pack=%s", pack)
    if pack in {"muq-mulan", MUQ_MULAN_MODEL}:
        try:
            typer.echo("downloading muq_mulan snapshots into the Hugging Face cache...")
            cache_dir = download_muq_mulan_model(settings)
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"ready muq_mulan cache_dir={cache_dir}")
        return
    if pack != "discogs-effnet-heads":
        raise typer.BadParameter("Supported packs: discogs-effnet-heads, muq-mulan")
    results = download_head_pack_models(settings)
    downloaded = sum(1 for result in results if result.downloaded)
    typer.echo(f"downloaded={downloaded} already_present={len(results) - downloaded}")
    for result in results:
        state = "downloaded" if result.downloaded else "ready"
        typer.echo(f"{state} {result.path}")


@cli.command("analyze-heads")
def analyze_heads(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Run all enabled Discogs-EffNet classification heads for missing tracks."""
    store, settings = get_store_and_settings()
    head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
    analyzer = DiscogsEffnetHeadPackAnalyzer(settings)
    tracks = store.list_tracks_missing_head_pack(head_model_names, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze-heads limit=%s total=%s heads=%s", limit, total, len(DISCOGS_EFFNET_HEADS))
    if total == 0:
        typer.echo("nothing to analyze for discogs-effnet-heads")
        analysis_logger.info("Finished CLI analyze-heads total=0")
        return
    typer.echo(f"analyzing_heads={total} heads={len(DISCOGS_EFFNET_HEADS)}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            with track_audio_path(store, settings, track) as audio_path:
                outputs = analyzer.analyze_track(audio_path)
            for output in outputs:
                store.save_model_output(track.id, output.model_name, output.scores, output.aggregation)
                store.save_predictions(track.id, output.model_name, output.predictions)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f} outputs={len(outputs)}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track head analysis failed track_id=%s path=%s seconds=%.1f",
                track.id,
                track.path,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze-heads done=%s failed=%s model_outputs=%s",
        done,
        failed,
        store.count_model_outputs(),
    )
    typer.echo(
        f"analyzed_heads={done} failed={failed} "
        f"model_outputs={store.count_model_outputs()}"
    )


@cli.command("analyze-genres")
def analyze_genres_compat(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Compatibility alias for analyze-heads."""
    analyze_heads(limit=limit)


@cli.command("analyze-audio-features")
def analyze_audio_features(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Extract BPM, key, loudness, and dynamics for tracks missing audio features."""
    store, _settings = get_store_and_settings()
    analyzer = AudioFeatureAnalyzer()
    tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze-audio-features limit=%s total=%s", limit, total)
    if total == 0:
        typer.echo("nothing to analyze for audio features")
        analysis_logger.info("Finished CLI analyze-audio-features total=0")
        return
    typer.echo(f"analyzing_audio_features={total}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            with track_audio_path(store, _settings, track) as audio_path:
                features = analyzer.analyze_track(audio_path)
            store.save_features(track.id, features)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f} features={len(features)}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track audio feature analysis failed track_id=%s path=%s seconds=%.1f",
                track.id,
                track.path,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze-audio-features done=%s failed=%s feature_tracks=%s",
        done,
        failed,
        store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR),
    )
    typer.echo(
        f"analyzed_audio_features={done} failed={failed} "
        f"feature_tracks={store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)}"
    )


@cli.command("build-index")
def build_index_command(model: Annotated[str, typer.Option("--model")] = "discogs_multi") -> None:
    """Build and save the HNSW cosine index for a model."""
    store, settings = get_store_and_settings()
    logger.info("Starting CLI build-index model=%s", model)
    path = build_index(store, settings, model)
    logger.info("Finished CLI build-index model=%s path=%s", model, path)
    typer.echo(f"index={path}")


@cli.command("similar-mix")
def similar_mix(
    track_ids: Annotated[str, typer.Option("--track-ids")],
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    k: Annotated[int, typer.Option("--k")] = 30,
    max_per_artist: Annotated[int, typer.Option("--max-per-artist")] = 2,
    exclude_same_album: Annotated[bool, typer.Option("--exclude-same-album/--include-same-album")] = True,
) -> None:
    """Print similar tracks for an average blend of seed track embeddings."""
    store, settings = get_store_and_settings()
    parsed_ids: list[int] = []
    for part in track_ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            parsed_ids.append(int(part))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid track id: {part}") from exc
    if not parsed_ids:
        raise typer.BadParameter("Provide at least one track id via --track-ids")
    seeds = []
    for track_id in parsed_ids:
        track = store.get_track(track_id)
        if track is None:
            raise typer.BadParameter(f"Unknown track id: {track_id}")
        seeds.append(track)
    logger.info(
        "Starting CLI similar-mix track_ids=%s model=%s k=%s max_per_artist=%s exclude_same_album=%s",
        parsed_ids,
        model,
        k,
        max_per_artist,
        exclude_same_album,
    )
    results, skipped_seed_ids = Recommender(store, settings, model).similar_mix(
        seeds,
        k=k,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
    )
    if skipped_seed_ids:
        typer.echo(f"Skipped seeds without embeddings: {skipped_seed_ids}")
    typer.echo(f"Blend seeds: {', '.join(str(seed.id) for seed in seeds)}")
    logger.info(
        "Finished CLI similar-mix track_ids=%s model=%s skipped=%s results=%s",
        parsed_ids,
        model,
        skipped_seed_ids,
        len(results),
    )
    for item in results:
        track = item.track
        typer.echo(
            f"{item.similarity:.3f}  {track.id}  "
            f"{track.artist or ''} - {track.title or Path(track.path).stem}"
        )


@cli.command()
def similar(
    track_id: Annotated[int | None, typer.Option("--track-id")] = None,
    path: Annotated[Path | None, typer.Option("--path", exists=True, dir_okay=False)] = None,
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    k: Annotated[int, typer.Option("--k")] = 30,
    max_per_artist: Annotated[int, typer.Option("--max-per-artist")] = 2,
    exclude_same_album: Annotated[bool, typer.Option("--exclude-same-album/--include-same-album")] = True,
) -> None:
    """Print similar tracks for a seed track."""
    store, settings = get_store_and_settings()
    logger.info(
        "Starting CLI similar track_id=%s path=%s model=%s k=%s max_per_artist=%s exclude_same_album=%s",
        track_id,
        path,
        model,
        k,
        max_per_artist,
        exclude_same_album,
    )
    seed = store.get_track(track_id) if track_id is not None else None
    if seed is None and path is not None:
        seed = store.find_track_by_path(path)
    if seed is None:
        logger.warning("CLI similar seed not found track_id=%s path=%s", track_id, path)
        raise typer.BadParameter("Provide a known --track-id or --path")

    results = Recommender(store, settings, model).similar(
        seed,
        k=k,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
    )
    typer.echo(f"Seed: {seed.artist or ''} - {seed.title or seed.path}")
    logger.info("Finished CLI similar seed_id=%s model=%s results=%s", seed.id, model, len(results))
    for item in results:
        track = item.track
        typer.echo(
            f"{item.similarity:.3f}  {track.id}  "
            f"{track.artist or ''} - {track.title or Path(track.path).stem}"
        )


@cli.command()
def stats(model: Annotated[str, typer.Option("--model")] = "discogs_multi") -> None:
    """Print catalog stats."""
    store, settings = get_store_and_settings()
    typer.echo(f"db={settings.db_path}")
    typer.echo(f"tracks={store.count_tracks()}")
    typer.echo(f"embeddings[{model}]={store.count_embeddings(model)}")
    typer.echo(f"head_pack_outputs={store.count_model_outputs()}")
    typer.echo(
        "missing_head_pack_tracks="
        f"{store.count_tracks_missing_head_pack([head.id for head in DISCOGS_EFFNET_HEADS])}"
    )
    typer.echo(f"head_pack_ready={head_pack_readiness(settings)['ready']}")
    typer.echo(f"audio_features={store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)}")
    typer.echo(
        f"missing_audio_features={store.count_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR)}"
    )
    normalization = store.normalization_status()
    typer.echo(f"artists={normalization.artists}")
    typer.echo(f"releases={normalization.releases}")
    typer.echo(f"tracks_with_release={normalization.tracks_with_release}")
    typer.echo(f"tracks_with_artist={normalization.tracks_with_artist}")
    typer.echo(f"index={settings.index_path(model)}")


def main() -> None:
    configure_logging()
    cli()


if __name__ == "__main__":
    main()
